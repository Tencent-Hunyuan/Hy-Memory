"""Secure-storage helpers must be importable on non-POSIX hosts."""

from __future__ import annotations

import os

import pytest

from one_memory import secrets as secrets_module


def test_importing_secrets_does_not_require_pwd() -> None:
    # On Windows `pwd` does not exist; a module-level import used to make the
    # whole Core (and every test importing it) fail at collection time.
    assert hasattr(secrets_module, "SecretStore")


def test_keychain_environment_works_without_pwd(monkeypatch: pytest.MonkeyPatch) -> None:
    # Simulate the Windows import path on any platform. With the old code this
    # raised AttributeError on `pwd.getpwuid`.
    monkeypatch.setattr(secrets_module, "pwd", None, raising=False)

    environment = secrets_module._keychain_environment()

    assert environment["HOME"] == os.environ["HOME"] if "HOME" in os.environ else True
    assert isinstance(environment, dict)


def test_keychain_environment_still_pins_home_on_posix(monkeypatch: pytest.MonkeyPatch) -> None:
    if secrets_module.pwd is None or not hasattr(os, "getuid"):
        pytest.skip("POSIX pwd/getuid only")
    monkeypatch.setenv("HOME", "/wrong/home")

    environment = secrets_module._keychain_environment()

    assert environment["HOME"] == secrets_module.pwd.getpwuid(os.getuid()).pw_dir
