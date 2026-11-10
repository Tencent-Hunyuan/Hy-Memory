import json
import os
import pwd
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from ctypes import byref, c_void_p
from enum import StrEnum
from importlib import import_module
from typing import Any, Protocol, cast

DEFAULT_SERVICE_NAME = "one-memory"
# Public compatibility constant; runtime operations use keychain_service_name().
SERVICE_NAME = DEFAULT_SERVICE_NAME
# A frozen Core child can take several seconds to load while three large
# imports are parsing concurrently. These bounds cover process startup plus
# the Security.framework call; they are not model/network timeouts.
KEYCHAIN_READ_TIMEOUT = 10.0
KEYCHAIN_WRITE_TIMEOUT = 15.0
#: Wait after a failed read before asking the Keychain again. Short enough that
#: a user who clicks Allow is picked up within seconds, long enough that a
#: locked store is not re-read on every poll. The old ladder (60/300/900) held
#: a whole memory queue behind one denial for fifteen minutes; recovery is now
#: a property of the store answering, not of a timer running out.
KEYCHAIN_READ_BACKOFF_SECONDS = (2.0, 5.0, 15.0)


class SecretStoreErrorCode(StrEnum):
    """Why a secure-storage read failed, for callers that must say different things.

    "Click Allow in the Keychain prompt", "the read timed out", and "there is
    no credential store on this machine" are three different instructions.
    Collapsing them is why the UI could only ever show one message.
    """

    #: The entry exists but the OS will not hand it over without the user
    #: authorizing this process. Actionable by the user, right now.
    AUTHORIZATION_REQUIRED = "secure_storage_authorization_required"
    #: The bounded child did not answer in time. Worth retrying; not the user's
    #: to fix.
    READ_TIMEOUT = "secure_storage_read_timeout"
    WRITE_TIMEOUT = "secure_storage_write_timeout"
    #: No usable store: denied, missing, or unreadable. Not fixable by retrying
    #: and not the user's to authorize.
    STORE_UNAVAILABLE = "secure_storage_unavailable"
    #: A read refused by an earlier failure's backoff, not by the store now.
    BACKOFF = "secure_storage_backoff"


class SecretStoreUnavailableError(RuntimeError):
    def __init__(
        self,
        message: str = "Operating system secure storage is unavailable",
        *,
        code: SecretStoreErrorCode = SecretStoreErrorCode.STORE_UNAVAILABLE,
    ) -> None:
        super().__init__(message)
        self.code = code


class SecretStore(Protocol):
    def put(self, secret_ref: str, value: str) -> None: ...

    def resolve(self, secret_ref: str) -> str | None: ...

    def has(self, secret_ref: str) -> bool: ...

    def delete(self, secret_ref: str) -> None: ...

    def delete_many(self, secret_refs: tuple[str, ...]) -> None: ...


class KeyringSecretStore:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.RLock()
        self._cache: dict[str, str | None] = {}
        self._read_failures: dict[str, tuple[int, float]] = {}

    def put(self, secret_ref: str, value: str) -> None:
        if not value:
            raise ValueError("Secret value cannot be empty")
        kr = _keyring()
        with self._lock:
            try:
                if _uses_bounded_child(kr):
                    _bounded_keychain_write(secret_ref, value)
                else:
                    kr.set_password(keychain_service_name(), secret_ref, value)
            except _keyring_errors() as error:
                raise SecretStoreUnavailableError(
                    "Operating system secure storage is unavailable"
                ) from error
            self._cache[secret_ref] = value
            self._read_failures.pop(secret_ref, None)

    def resolve(self, secret_ref: str) -> str | None:
        kr = _keyring()
        with self._lock:
            if secret_ref in self._cache:
                return self._cache[secret_ref]
            failures, retry_at = self._read_failures.get(secret_ref, (0, 0.0))
            now = self._clock()
            if now < retry_at:
                # Inside the penalty: answer from backoff rather than spawning
                # another child that is certain to be refused again. The penalty
                # is bounded by the shortest ladder step a healthy store can ever
                # pay, so it never outlasts the user's own recovery.
                raise SecretStoreUnavailableError(
                    "Operating system secure storage read backoff",
                    code=SecretStoreErrorCode.BACKOFF,
                )
            try:
                if _uses_bounded_child(kr):
                    value = _bounded_keychain_read(secret_ref)
                else:
                    value = kr.get_password(keychain_service_name(), secret_ref)
            except _keyring_errors() + (SecretStoreUnavailableError,) as error:
                delay = _next_read_backoff(failures)
                self._read_failures[secret_ref] = (failures + 1, self._clock() + delay)
                if isinstance(error, SecretStoreUnavailableError):
                    raise
                raise SecretStoreUnavailableError(
                    "Operating system secure storage is unavailable"
                ) from error
            self._cache[secret_ref] = value
            self._read_failures.pop(secret_ref, None)
            return value

    def has(self, secret_ref: str) -> bool:
        return self.resolve(secret_ref) is not None

    def delete(self, secret_ref: str) -> None:
        kr = _keyring()
        with self._lock:
            try:
                if _uses_bounded_child(kr):
                    _bounded_keychain_delete(secret_ref)
                elif self.resolve(secret_ref) is not None:
                    kr.delete_password(keychain_service_name(), secret_ref)
            except _keyring_errors() as error:
                raise SecretStoreUnavailableError(
                    "Operating system secure storage is unavailable"
                ) from error
            self._cache[secret_ref] = None
            self._read_failures.pop(secret_ref, None)

    def delete_many(self, secret_refs: tuple[str, ...]) -> None:
        if not secret_refs:
            return
        kr = _keyring()
        with self._lock:
            try:
                if _uses_bounded_child(kr):
                    _bounded_keychain_delete_many(secret_refs)
                else:
                    for secret_ref in secret_refs:
                        with suppress(kr.errors.PasswordDeleteError):
                            kr.delete_password(keychain_service_name(), secret_ref)
            except _keyring_errors() as error:
                raise SecretStoreUnavailableError(
                    "Operating system secure storage is unavailable"
                ) from error
            for secret_ref in secret_refs:
                self._cache[secret_ref] = None
                self._read_failures.pop(secret_ref, None)


def _uses_bounded_child(kr: Any) -> bool:
    """Whether this backend must be called from a killable child process.

    A thread cannot cancel a stuck Security.framework call, and SecretService
    blocks on a D-Bus prompt forever on headless Linux (#375). Reads, writes
    and deletes on either backend all go through the bounded child.
    """
    backend = type(kr.get_keyring()).__module__
    return backend == "keyring.backends.macOS" or backend.startswith(
        "keyring.backends.SecretService"
    )


def _keychain_read_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--keychain-read"]
    return [sys.executable, "-m", "one_memory.keychain_reader"]


def _keychain_write_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--keychain-write"]
    return [sys.executable, "-m", "one_memory.keychain_writer"]


def _keychain_delete_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--keychain-delete"]
    return [sys.executable, "-m", "one_memory.keychain_deleter"]


def _keychain_delete_many_command() -> list[str]:
    return [*_keychain_delete_command(), "--many"]


def _keychain_environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment["HOME"] = pwd.getpwuid(os.getuid()).pw_dir
    return environment


keyring: Any = None


def _keyring() -> Any:
    """Import keyring on first use.

    The Reader process opens the library read-only and must not load the
    secure-storage stack: importing `secrets` used to pull `keyring` in for
    every process that merely reads the model profile table, which is also
    what made the Reader's own import guard fail at startup.
    """
    global keyring
    if keyring is None:
        keyring = import_module("keyring")
    return keyring


def _keyring_errors() -> tuple[type[BaseException], ...]:
    errors = import_module("keyring.errors")
    return cast(
        tuple[type[BaseException], ...],
        (errors.KeyringError, errors.NoKeyringError),
    )


def keychain_service_name() -> str:
    value = os.environ.get("ONE_MEMORY_KEYCHAIN_SERVICE", "").strip()
    if value:
        if not os.environ.get("ONE_MEMORY_TEST_ISOLATED"):
            raise SecretStoreUnavailableError(
                "Custom secure-storage namespaces are only available in isolated tests"
            )
        if len(value) > 128 or any(character.isspace() for character in value):
            raise SecretStoreUnavailableError("Invalid isolated secure-storage namespace")
        return value
    return DEFAULT_SERVICE_NAME


def _bounded_keychain_write(secret_ref: str, value: str) -> None:
    try:
        result = subprocess.run(
            _keychain_write_command(),
            input=json.dumps({"reference": secret_ref, "value": value}),
            capture_output=True,
            text=True,
            timeout=KEYCHAIN_WRITE_TIMEOUT,
            check=False,
            env=_keychain_environment(),
        )
    except subprocess.TimeoutExpired:
        raise SecretStoreUnavailableError(
            "Operating system secure storage timed out",
            code=SecretStoreErrorCode.WRITE_TIMEOUT,
        ) from None
    except (OSError, UnicodeError):
        raise SecretStoreUnavailableError(
            "Operating system secure storage is unavailable"
        ) from None
    if result.returncode != 0 or result.stdout != '{"stored": true}':
        raise SecretStoreUnavailableError("Operating system secure storage is unavailable")


def _bounded_keychain_delete(secret_ref: str) -> None:
    try:
        result = subprocess.run(
            _keychain_delete_command(),
            input=secret_ref,
            capture_output=True,
            text=True,
            timeout=KEYCHAIN_WRITE_TIMEOUT,
            check=False,
            env=_keychain_environment(),
        )
    except subprocess.TimeoutExpired:
        raise SecretStoreUnavailableError("Operating system secure storage timed out") from None
    except (OSError, UnicodeError):
        raise SecretStoreUnavailableError(
            "Operating system secure storage is unavailable"
        ) from None
    if result.returncode != 0 or result.stdout != '{"deleted": true}':
        raise SecretStoreUnavailableError("Operating system secure storage is unavailable")


def _bounded_keychain_delete_many(secret_refs: tuple[str, ...]) -> None:
    try:
        result = subprocess.run(
            _keychain_delete_many_command(),
            input=json.dumps({"references": list(secret_refs)}),
            capture_output=True,
            text=True,
            timeout=KEYCHAIN_WRITE_TIMEOUT,
            check=False,
            env=_keychain_environment(),
        )
    except subprocess.TimeoutExpired:
        raise SecretStoreUnavailableError("Operating system secure storage timed out") from None
    except (OSError, UnicodeError):
        raise SecretStoreUnavailableError(
            "Operating system secure storage is unavailable"
        ) from None
    if result.returncode != 0 or result.stdout != json.dumps({"deleted": len(secret_refs)}):
        raise SecretStoreUnavailableError("Operating system secure storage is unavailable")


def _bounded_keychain_read(secret_ref: str) -> str | None:
    # A thread cannot cancel a stuck Security.framework or D-Bus call. The
    # same executable reads in a short-lived child so its store identity
    # stays unchanged, while timeout kills and reaps the blocked process.
    try:
        result = subprocess.run(
            _keychain_read_command(),
            input=secret_ref,
            capture_output=True,
            text=True,
            timeout=KEYCHAIN_READ_TIMEOUT,
            check=False,
            env=_keychain_environment(),
        )
    except subprocess.TimeoutExpired:
        raise SecretStoreUnavailableError(
            "Operating system secure storage timed out",
            code=SecretStoreErrorCode.READ_TIMEOUT,
        ) from None
    except (OSError, UnicodeError):
        raise SecretStoreUnavailableError(
            "Operating system secure storage is unavailable"
        ) from None
    try:
        body = json.loads(result.stdout)
        if result.returncode or not isinstance(body, dict) or set(body) != {"value"}:
            # A refused read still answers with a body: the child writes
            # {"error": <code>} so the caller can tell an authorization the user
            # has to give from a store that is not there.
            if (
                isinstance(body, dict)
                and set(body) == {"error"}
                and _known_error_code(body["error"])
            ):
                raise SecretStoreUnavailableError(
                    "Operating system secure storage refused the read",
                    code=SecretStoreErrorCode(body["error"]),
                )
            raise ValueError
        value = body["value"]
        if value is not None and not isinstance(value, str):
            raise ValueError
        return value
    except (ValueError, TypeError):
        raise SecretStoreUnavailableError(
            "Operating system secure storage is unavailable"
        ) from None


def _known_error_code(value: object) -> bool:
    """Only codes this build knows, so a hostile child cannot invent a state."""
    return isinstance(value, str) and value in tuple(SecretStoreErrorCode)


def _next_read_backoff(failures: int) -> float:
    """How long to wait after this failure, capped at the ladder's top step.

    The ladder rises so a genuinely locked store is not polled in a loop, and
    stops rising so a store that recovers is never held behind a penalty
    earned by earlier failures. The old top step was 900 seconds: a queue
    stayed parked for a quarter of an hour after the Keychain was ready.
    """
    return KEYCHAIN_READ_BACKOFF_SECONDS[min(failures, len(KEYCHAIN_READ_BACKOFF_SECONDS) - 1)]


def _macos_password_without_prompt(secret_ref: str) -> str | None:
    # A background Core cannot answer a Keychain authorization dialog. Fail
    # closed for inaccessible entries; never change the item's access policy.
    api = cast(Any, import_module("keyring.backends.macOS.api"))
    query = api.create_query(
        kSecClass=api.k_("kSecClassGenericPassword"),
        kSecMatchLimit=api.k_("kSecMatchLimitOne"),
        kSecAttrService=keychain_service_name(),
        kSecAttrAccount=secret_ref,
        kSecReturnData=True,
        kSecUseAuthenticationUI=api.k_("kSecUseAuthenticationUIFail"),
    )
    data = c_void_p()
    try:
        status = api.SecItemCopyMatching(query, byref(data))
        if status == api.error.item_not_found:
            return None
        if status != 0:
            # The entry is there and the OS refused it. A background Core cannot
            # answer the prompt, so this is an authorization the user has to
            # give — not a missing credential and not a broken store.
            raise SecretStoreUnavailableError(
                "Operating system secure storage requires user authorization",
                code=SecretStoreErrorCode.AUTHORIZATION_REQUIRED,
            )
        return cast(str, api.cfstr_to_str(data))
    finally:
        api._found.CFRelease(c_void_p(query))
        if data.value:
            api._found.CFRelease(data)


class InMemorySecretStore:
    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    def put(self, secret_ref: str, value: str) -> None:
        if not value:
            raise ValueError("Secret value cannot be empty")
        self._values[secret_ref] = value

    def resolve(self, secret_ref: str) -> str | None:
        return self._values.get(secret_ref)

    def has(self, secret_ref: str) -> bool:
        return secret_ref in self._values

    def delete(self, secret_ref: str) -> None:
        self._values.pop(secret_ref, None)

    def delete_many(self, secret_refs: tuple[str, ...]) -> None:
        for secret_ref in secret_refs:
            self.delete(secret_ref)
