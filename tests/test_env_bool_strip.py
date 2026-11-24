# -*- coding: utf-8 -*-
"""Regression: _get_env_bool should tolerate surrounding whitespace.

value.lower() compares the raw value, so " true " / "yes " are read as False
while the string helpers elsewhere strip first. This is the shared parser for
all boolean config flags.
"""

from hy_memory.config import _get_env_bool

KEY = "MEMORY_TEST_ENV_BOOL_STRIP"


def test_whitespace_true(monkeypatch):
    monkeypatch.setenv(KEY, " true ")
    assert _get_env_bool(KEY, False) is True


def test_whitespace_yes(monkeypatch):
    monkeypatch.setenv(KEY, "yes ")
    assert _get_env_bool(KEY, False) is True


def test_plain_false(monkeypatch):
    monkeypatch.setenv(KEY, "false")
    assert _get_env_bool(KEY, True) is False
