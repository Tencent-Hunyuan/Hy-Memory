"""A D-Bus keyring read must be bounded, not block the whole process (#375)."""

from __future__ import annotations

import io
import json
import subprocess
from types import SimpleNamespace

import keyring
import pytest

from one_memory import keychain_reader
from one_memory import secrets as secret_module
from one_memory.secrets import (
    KeyringSecretStore,
    SecretStoreErrorCode,
    SecretStoreUnavailableError,
)


def _backend(module: str):
    return type("Backend", (), {"__module__": module})()


def _install_keyring(monkeypatch: pytest.MonkeyPatch, module: str, *, direct_value=None):
    def get_password(service, reference):
        if direct_value is None:
            raise AssertionError("a SecretService read must not happen in-process")
        return direct_value

    fake = SimpleNamespace(
        get_keyring=lambda: _backend(module),
        get_password=get_password,
    )
    monkeypatch.setattr(secret_module, "keyring", fake)
    return fake


def test_secret_service_reads_go_through_the_bounded_child(monkeypatch) -> None:
    _install_keyring(monkeypatch, "keyring.backends.SecretService.SecretServiceKeyring")
    calls: list[str] = []

    def bounded(reference: str) -> str:
        calls.append(reference)
        return "recovered"

    monkeypatch.setattr(secret_module, "_bounded_keychain_read", bounded)

    assert KeyringSecretStore().resolve("profile-ref") == "recovered"
    assert calls == ["profile-ref"]


def test_a_blocked_secret_service_read_times_out_instead_of_hanging(monkeypatch) -> None:
    _install_keyring(monkeypatch, "keyring.backends.SecretService.SecretServiceKeyring")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired(command, kwargs["timeout"])
        ),
    )

    with pytest.raises(SecretStoreUnavailableError) as error:
        KeyringSecretStore().resolve("profile-ref")

    assert error.value.code is SecretStoreErrorCode.READ_TIMEOUT


def test_other_backends_still_read_directly(monkeypatch) -> None:
    _install_keyring(monkeypatch, "keyring.backends.Windows.WinVaultKeyring", direct_value="direct")
    monkeypatch.setattr(
        secret_module,
        "_bounded_keychain_read",
        lambda ref: (_ for _ in ()).throw(AssertionError("unexpected bounded read")),
    )

    assert KeyringSecretStore().resolve("profile-ref") == "direct"


def test_child_reader_uses_keyring_off_darwin(monkeypatch) -> None:
    captured: list[tuple[str, str]] = []
    monkeypatch.setattr(
        keyring,
        "get_password",
        lambda service, reference: captured.append((service, reference)) or "child-secret",
    )
    stdout = io.StringIO()
    fake_sys = SimpleNamespace(
        platform="linux",
        stdin=io.StringIO("profile-ref"),
        stdout=stdout,
    )
    monkeypatch.setattr(keychain_reader, "sys", fake_sys)

    assert keychain_reader.main() == 0
    assert json.loads(stdout.getvalue()) == {"value": "child-secret"}
    assert captured[0][1] == "profile-ref"


def test_child_reader_rejects_an_oversized_reference(monkeypatch) -> None:
    stdout = io.StringIO()
    monkeypatch.setattr(
        keychain_reader,
        "sys",
        SimpleNamespace(
            platform="linux",
            stdin=io.StringIO("x" * 2049),
            stdout=stdout,
        ),
    )

    assert keychain_reader.main() == 1
    assert stdout.getvalue() == ""


SECRET_SERVICE = "keyring.backends.SecretService.SecretServiceKeyring"


def _install_write_keyring(monkeypatch: pytest.MonkeyPatch, module: str) -> list[str]:
    direct: list[str] = []
    fake = SimpleNamespace(
        get_keyring=lambda: _backend(module),
        get_password=lambda service, reference: direct.append("get") or "value",
        set_password=lambda service, reference, value: direct.append("set"),
        delete_password=lambda service, reference: direct.append("delete"),
        errors=SimpleNamespace(PasswordDeleteError=KeyError),
    )
    monkeypatch.setattr(secret_module, "keyring", fake)
    return direct


def _timeout(command, **kwargs):
    raise subprocess.TimeoutExpired(command, kwargs["timeout"])


def test_secret_service_writes_and_deletes_go_through_the_bounded_child(monkeypatch) -> None:
    direct = _install_write_keyring(monkeypatch, SECRET_SERVICE)
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(
        secret_module, "_bounded_keychain_write", lambda ref, value: calls.append(("put", ref))
    )
    monkeypatch.setattr(
        secret_module, "_bounded_keychain_delete", lambda ref: calls.append(("delete", ref))
    )
    monkeypatch.setattr(
        secret_module,
        "_bounded_keychain_delete_many",
        lambda refs: calls.append(("delete_many", refs)),
    )
    store = KeyringSecretStore()

    store.put("profile-ref", "secret")
    store.delete("profile-ref")
    store.delete_many(("a", "b"))

    assert calls == [("put", "profile-ref"), ("delete", "profile-ref"), ("delete_many", ("a", "b"))]
    assert direct == []


@pytest.mark.parametrize(
    ("operation", "code"),
    [
        (lambda store: store.put("profile-ref", "secret"), SecretStoreErrorCode.WRITE_TIMEOUT),
        (lambda store: store.delete("profile-ref"), SecretStoreErrorCode.STORE_UNAVAILABLE),
        (lambda store: store.delete_many(("a", "b")), SecretStoreErrorCode.STORE_UNAVAILABLE),
    ],
    ids=["put", "delete", "delete_many"],
)
def test_a_blocked_secret_service_write_or_delete_times_out(monkeypatch, operation, code) -> None:
    direct = _install_write_keyring(monkeypatch, SECRET_SERVICE)
    monkeypatch.setattr(subprocess, "run", _timeout)

    with pytest.raises(SecretStoreUnavailableError) as error:
        operation(KeyringSecretStore())

    assert error.value.code is code
    assert "timed out" in str(error.value)
    assert direct == []


def test_other_backends_still_write_and_delete_directly(monkeypatch) -> None:
    direct = _install_write_keyring(monkeypatch, "keyring.backends.Windows.WinVaultKeyring")
    monkeypatch.setattr(subprocess, "run", _timeout)
    store = KeyringSecretStore()

    store.put("profile-ref", "secret")
    store.delete("profile-ref")
    store.delete_many(("a",))

    assert direct == ["set", "delete", "delete"]


def test_child_writer_and_deleter_use_keyring_off_darwin(monkeypatch) -> None:
    from one_memory import keychain_deleter, keychain_writer

    captured: list[tuple[str, str]] = []
    monkeypatch.setattr(
        keychain_writer.keyring,
        "set_password",
        lambda service, reference, value: captured.append(("set", reference)),
    )
    monkeypatch.setattr(
        keychain_deleter.keyring,
        "delete_password",
        lambda service, reference: captured.append(("delete", reference)),
    )

    def run(module, stdin: str, argv: list[str]) -> tuple[int, str]:
        stdout = io.StringIO()
        monkeypatch.setattr(
            module,
            "sys",
            SimpleNamespace(platform="linux", stdin=io.StringIO(stdin), stdout=stdout, argv=argv),
        )
        return module.main(), stdout.getvalue()

    assert run(keychain_writer, json.dumps({"reference": "ref", "value": "v"}), ["writer"]) == (
        0,
        '{"stored": true}',
    )
    assert run(keychain_deleter, "ref", ["deleter"]) == (0, '{"deleted": true}')
    assert run(keychain_deleter, json.dumps({"references": ["a", "b"]}), ["deleter", "--many"]) == (
        0,
        '{"deleted": 2}',
    )
    assert captured == [("set", "ref"), ("delete", "ref"), ("delete", "a"), ("delete", "b")]
