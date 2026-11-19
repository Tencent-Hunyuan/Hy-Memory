# -*- coding: utf-8 -*-
"""Regression: an invalid numeric env value must not crash RecallConfig.

hierarchy_bundle_novel_floor_margin was parsed with a bare float(raw) while
every other numeric field uses the tolerant _get_env_float helper. A single
malformed env value raised ValueError and prevented client startup.
"""

import pytest

from hy_memory.config import RecallConfig

ENV_KEY = "MEMORY_HIERARCHY_BUNDLE_NOVEL_FLOOR_MARGIN"


def test_invalid_float_env_does_not_crash(monkeypatch):
    monkeypatch.setenv(ENV_KEY, "not-a-number")
    config = RecallConfig()
    assert config.hierarchy_bundle_novel_floor_margin is None


def test_valid_float_env_still_parses(monkeypatch):
    monkeypatch.setenv(ENV_KEY, "0.25")
    config = RecallConfig()
    assert config.hierarchy_bundle_novel_floor_margin == 0.25
