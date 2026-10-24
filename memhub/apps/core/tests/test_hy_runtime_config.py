"""Pinned public SDK identity and diagnostic logging policy."""

import logging
import re
from pathlib import Path

from one_memory.hy_runtime_config import (
    HY_MEMORY_DISTRIBUTION,
    HY_MEMORY_VERSION,
    apply_diagnostic_log_level,
    hy_memory_version,
)

DOCUMENT = Path(__file__).resolve().parents[3] / "MEMORY.md"


def test_the_installed_sdk_is_the_pinned_one():
    assert hy_memory_version() == HY_MEMORY_VERSION
    assert HY_MEMORY_DISTRIBUTION == "hy-memory"


def test_the_pin_matches_the_lock_file_and_its_wheel():
    lock = (DOCUMENT.parent / "apps/core/uv.lock").read_text(encoding="utf-8")
    assert re.search(
        rf'\[\[package\]\]\nname = "{HY_MEMORY_DISTRIBUTION}"\n'
        rf'version = "{HY_MEMORY_VERSION}"',
        lock,
    )
    assert f"hy_memory-{HY_MEMORY_VERSION}-" in lock
    assert f'specifier = "=={HY_MEMORY_VERSION}"' in lock


def test_info_level_suppresses_noisy_third_party_success_logs():
    names = ("", "one_memory", "httpx", "httpcore", "watchfiles", "jieba")
    previous = {name: logging.getLogger(name).level for name in names}
    try:
        apply_diagnostic_log_level("info")
        assert logging.getLogger("one_memory").level == logging.INFO
        for name in ("httpx", "httpcore", "watchfiles", "jieba"):
            assert logging.getLogger(name).level == logging.WARNING
        apply_diagnostic_log_level("debug")
        for name in ("one_memory", "httpx", "httpcore", "watchfiles", "jieba"):
            assert logging.getLogger(name).level == logging.DEBUG
    finally:
        for name, level in previous.items():
            logging.getLogger(name).setLevel(level)
