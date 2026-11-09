"""A D-Bus keyring read must be bounded, not block the whole process (#375)."""

from __future__ import annotations

import io
import json
import subprocess
import sys
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
    _install_keyring(
        monkeypatch, "keyring.backends.Windows.WinVaultKeyring", direct_value="direct"
    )
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
