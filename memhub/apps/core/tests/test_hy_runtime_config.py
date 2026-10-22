"""Keep the documented runtime policy synchronized with the pinned public SDK.

Every release must keep the runtime aligned with the documented policy.
Checking that contract automatically prevents a silently flipped rerank flag
from going unnoticed.
"""

import logging
import re
from pathlib import Path

import pytest

from one_memory.hy_runtime_config import (
    HY_MEMORY_BEHAVIOR_ENV,
    HY_MEMORY_DISTRIBUTION,
    HY_MEMORY_MODE,
    HY_MEMORY_READER,
    HY_MEMORY_VERSION,
    apply_diagnostic_log_level,
    hy_memory_version,
)

DOCUMENT = Path(__file__).resolve().parents[3] / "MEMORY.md"
ROW = re.compile(r"\|\s*`([A-Z_0-9]+)`\s*\|\s*`([^`]+)`\s*\|")


def documented() -> dict[str, str]:
    return dict(ROW.findall(DOCUMENT.read_text(encoding="utf-8")))


def test_every_documented_setting_is_the_one_the_runtime_applies():
    table = documented()
    assert table, "the behaviour table disappeared from MEMORY.md"
    assert {
        name: (value, HY_MEMORY_BEHAVIOR_ENV.get(name))
        for name, value in table.items()
        if HY_MEMORY_BEHAVIOR_ENV.get(name) != value
    } == {}


def test_no_setting_reaches_hy_undocumented():
    assert set(HY_MEMORY_BEHAVIOR_ENV) - set(documented()) == set()


@pytest.mark.parametrize(
    "name,expected",
    [
        ("MEMORY_LOCAL_RERANK_ENABLED", "false"),
        ("MEMORY_GRAPH_RERANK_ENABLED", "false"),
        ("MEMORY_MODE", HY_MEMORY_MODE),
        ("HY_MEMORY_READER", HY_MEMORY_READER),
    ],
)
def test_the_decisions_the_user_made_by_name_are_still_in_force(name, expected):
    """Rerank off and ultra/hybrid were decided explicitly, not derived."""
    assert HY_MEMORY_BEHAVIOR_ENV[name] == expected


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
