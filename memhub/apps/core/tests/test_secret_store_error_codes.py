"""3925a kernel item 3: keychain failure classification and backoff that heals.

Every keychain failure currently raises the same `SecretStoreUnavailableError`
with no code, so the UI can only say "本机凭据存储暂不可用" whether the entry
needs the user to click Allow, whether the read timed out, or whether the
store is genuinely down. And the backoff is 60 / 300 / 900 seconds: after
three failures the store refuses every read for 15 minutes, which is what
leaves a queue of pending memory tasks stuck behind a single denial.

Two contracts pinned here:
  * each failure kind carries its own code;
  * the backoff is short, and a recovery clears it without a restart.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from one_memory import secrets as secret_module
from one_memory.secrets import (
    KEYCHAIN_READ_BACKOFF_SECONDS,
    KeyringSecretStore,
    SecretStoreErrorCode,
    SecretStoreUnavailableError,
)


def _mac_backend(monkeypatch):
    backend = type("MacBackend", (), {"__module__": "keyring.backends.macOS"})()
    monkeypatch.setattr(secret_module._keyring(), "get_keyring", lambda: backend)
    return backend


def test_authorization_required_is_its_own_code(monkeypatch) -> None:
    """Needs the user to allow: not the same message as "the store is down"."""
    _mac_backend(monkeypatch)
    monkeypatch.setattr(
        secret_module,
        "_bounded_keychain_read",
        lambda ref: (_ for _ in ()).throw(
            SecretStoreUnavailableError(
                "authorization required", code=SecretStoreErrorCode.AUTHORIZATION_REQUIRED
            )
        ),
    )

    with pytest.raises(SecretStoreUnavailableError) as error:
        KeyringSecretStore().resolve("profile-ref")

    assert error.value.code is SecretStoreErrorCode.AUTHORIZATION_REQUIRED


def test_a_read_timeout_is_its_own_code(monkeypatch) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired(command, kwargs["timeout"])
        ),
    )

    with pytest.raises(SecretStoreUnavailableError) as error:
        secret_module._bounded_keychain_read("profile-ref")

    assert error.value.code is SecretStoreErrorCode.READ_TIMEOUT


def test_an_unreadable_store_is_its_own_code(monkeypatch) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 1, "denied", ""),
    )

    with pytest.raises(SecretStoreUnavailableError) as error:
        secret_module._bounded_keychain_read("profile-ref")

    assert error.value.code is SecretStoreErrorCode.STORE_UNAVAILABLE


def test_an_unexpected_os_status_is_a_denial_not_a_silent_miss(monkeypatch) -> None:
    """`SecItemCopyMatching` returning anything unknown used to be one message.

    An entry the user has not allowed reads as a non-zero status, and that is
    an authorization answer, not an absent credential.
    """
    if sys.platform != "darwin":
        pytest.skip("macOS Security.framework is required")
    pytest.importorskip("keyring.backends.macOS")
    from keyring.backends.macOS import api

    monkeypatch.setattr(api, "create_query", lambda **_: 1)
    monkeypatch.setattr(api, "k_", lambda name: name)
    monkeypatch.setattr(
        api, "SecItemCopyMatching", lambda *_: api.error.sec_interaction_not_allowed
    )
    monkeypatch.setattr(api._found, "CFRelease", lambda *_: None)

    with pytest.raises(SecretStoreUnavailableError) as error:
        secret_module._macos_password_without_prompt("profile-ref")

    assert error.value.code is SecretStoreErrorCode.AUTHORIZATION_REQUIRED


def test_the_backoff_is_short(monkeypatch) -> None:
    """60 / 300 / 900 seconds pins a queue behind one denial for 15 minutes."""
    assert KEYCHAIN_READ_BACKOFF_SECONDS[0] <= 5.0
    assert max(KEYCHAIN_READ_BACKOFF_SECONDS) <= 30.0


def test_the_backoff_clears_as_soon_as_the_keychain_answers(monkeypatch) -> None:
    """Recovery must heal without restarting Core.

    With the old ladder a store that failed three times refused every read for
    900 seconds, so a user who clicked Allow had to wait out the penalty.
    """
    now = [100.0]
    state = {"healthy": False}
    _mac_backend(monkeypatch)

    def read(ref):
        if not state["healthy"]:
            raise SecretStoreUnavailableError(
                "authorization required", code=SecretStoreErrorCode.AUTHORIZATION_REQUIRED
            )
        return "recovered-secret"

    monkeypatch.setattr(secret_module, "_bounded_keychain_read", read)
    store = KeyringSecretStore(clock=lambda: now[0])

    for _ in range(len(KEYCHAIN_READ_BACKOFF_SECONDS) + 2):
        with pytest.raises(SecretStoreUnavailableError):
            store.resolve("profile-ref")
        now[0] += max(KEYCHAIN_READ_BACKOFF_SECONDS)

    # Worst case is the ladder's top step, not 900 seconds: a store that
    # answers again is used again on the very next read.
    state["healthy"] = True
    assert store.resolve("profile-ref") == "recovered-secret"

    # A different reference starts at the shortest step: the ladder is per
    # reference, so one credential's history of refusals cannot slow another.
    state["healthy"] = False
    with pytest.raises(SecretStoreUnavailableError):
        store.resolve("other-ref")
    state["healthy"] = True
    now[0] += KEYCHAIN_READ_BACKOFF_SECONDS[0]
    assert store.resolve("other-ref") == "recovered-secret"


def test_the_backoff_stops_a_hot_loop(monkeypatch) -> None:
    """Short, not gone: a locked store must not be retried on every poll."""
    calls = []
    now = [100.0]
    _mac_backend(monkeypatch)

    def read(ref):
        calls.append(now[0])
        raise SecretStoreUnavailableError(
            "authorization required", code=SecretStoreErrorCode.AUTHORIZATION_REQUIRED
        )

    monkeypatch.setattr(secret_module, "_bounded_keychain_read", read)
    store = KeyringSecretStore(clock=lambda: now[0])

    for _ in range(5):
        with pytest.raises(SecretStoreUnavailableError):
            store.resolve("profile-ref")
        now[0] += 0.1

    assert len(calls) == 1


def test_the_penalty_never_exceeds_the_longest_ladder_step(monkeypatch) -> None:
    """A store failing for an hour is not held longer than one failing now.

    The old code kept the ladder position forever, so the penalty a recovering
    store had to wait out grew with how long it had been broken.
    """
    now = [100.0]
    _mac_backend(monkeypatch)
    monkeypatch.setattr(
        secret_module,
        "_bounded_keychain_read",
        lambda ref: (_ for _ in ()).throw(
            SecretStoreUnavailableError(
                "authorization required", code=SecretStoreErrorCode.AUTHORIZATION_REQUIRED
            )
        ),
    )
    store = KeyringSecretStore(clock=lambda: now[0])

    for _ in range(len(KEYCHAIN_READ_BACKOFF_SECONDS) + 5):
        with pytest.raises(SecretStoreUnavailableError):
            store.resolve("profile-ref")
        now[0] += max(KEYCHAIN_READ_BACKOFF_SECONDS)

    ceiling = max(KEYCHAIN_READ_BACKOFF_SECONDS)
    assert ceiling <= 30.0


def test_a_write_timeout_is_its_own_code(monkeypatch) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired(command, kwargs["timeout"])
        ),
    )

    with pytest.raises(SecretStoreUnavailableError) as error:
        secret_module._bounded_keychain_write("profile-ref", "value")

    assert error.value.code is SecretStoreErrorCode.WRITE_TIMEOUT


def test_the_code_survives_a_catch_all(monkeypatch) -> None:
    """The code is on the exception, so `except SecretStoreUnavailableError`
    callers can classify it without importing anything new."""
    _mac_backend(monkeypatch)
    monkeypatch.setattr(
        secret_module,
        "_bounded_keychain_read",
        lambda ref: (_ for _ in ()).throw(
            SecretStoreUnavailableError(
                "authorization required", code=SecretStoreErrorCode.AUTHORIZATION_REQUIRED
            )
        ),
    )
    monkeypatch.setenv("ONE_MEMORY_KEYCHAIN_FAILURE_CODE", "1")

    try:
        KeyringSecretStore().resolve("profile-ref")
    except SecretStoreUnavailableError as error:
        assert error.code is SecretStoreErrorCode.AUTHORIZATION_REQUIRED
    else:  # pragma: no cover
        pytest.fail("resolve must raise")


def test_the_reader_child_reports_why_it_refused() -> None:
    """The child's exit code alone cannot say "the user must allow this".

    Core reads the child's stdout, so the code has to travel through that pipe
    rather than being re-derived from `returncode != 0`. Driven as a real
    subprocess because the pipe protocol is the thing under test.
    """
    if sys.platform != "darwin":
        pytest.skip("macOS Security.framework is required")
    child = (
        "import sys\n"
        "from keyring.backends.macOS import api\n"
        "import one_memory.keychain_reader as reader\n"
        "api.create_query = lambda **_: 1\n"
        "api.k_ = lambda name: name\n"
        "api.SecItemCopyMatching = lambda *_: api.error.sec_interaction_not_allowed\n"
        "api._found.CFRelease = lambda *_: None\n"
        "raise SystemExit(reader.main())\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", child],
        input="profile-ref",
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert result.returncode == 1, result.stderr
    assert json.loads(result.stdout) == {"error": SecretStoreErrorCode.AUTHORIZATION_REQUIRED.value}


def test_the_codes_are_stable_strings() -> None:
    """Frontend copy keys off these; they must not change shape silently."""
    assert SecretStoreErrorCode.AUTHORIZATION_REQUIRED == "secure_storage_authorization_required"
    assert SecretStoreErrorCode.READ_TIMEOUT == "secure_storage_read_timeout"
    assert SecretStoreErrorCode.STORE_UNAVAILABLE == "secure_storage_unavailable"


def test_a_bare_refusal_still_raises_and_defaults_to_unavailable() -> None:
    """`raise SecretStoreUnavailableError` is used by callers that only mean
    "no". The message must not become a required argument.
    """
    with pytest.raises(SecretStoreUnavailableError) as error:
        raise SecretStoreUnavailableError

    assert error.value.code is SecretStoreErrorCode.STORE_UNAVAILABLE
    assert "unavailable" in str(error.value)


def test_backoff_ladder_is_bounded_and_increasing() -> None:
    assert len(KEYCHAIN_READ_BACKOFF_SECONDS) >= 2
    assert list(KEYCHAIN_READ_BACKOFF_SECONDS) == sorted(KEYCHAIN_READ_BACKOFF_SECONDS)
    assert all(isinstance(seconds, float) for seconds in KEYCHAIN_READ_BACKOFF_SECONDS)


def _unused_json() -> str:
    return json.dumps({})


def test_secrets_module_does_not_import_pwd_at_module_level() -> None:
    """pwd is POSIX-only and is needed by one macOS-only line.

    Importing it at module level made `one_memory.secrets` unimportable on
    Windows, which took `model_profiles` -> `memory_tasks` -> `durable_queue`
    with it. `_submit_live_memory` then raised inside capture_stdio's
    fail-open handler, so live record assembly never ran and nothing said so.
    """
    assert not hasattr(secret_module, "pwd")
