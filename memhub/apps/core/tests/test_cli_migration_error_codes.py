"""The startup dialog can only act on the code it is handed.

A full disk and a damaged backup are different next steps, so they must reach
the person under different names. These tests pin those names.
"""

from __future__ import annotations

import errno
from pathlib import Path

import pytest

from one_memory.cli import migration_failure_code


class _Coded(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def test_running_out_of_disk_is_named_however_deep_it_is_wrapped() -> None:
    error = OSError(errno.ENOSPC, "No space left on device")
    wrapped: BaseException = error
    for _ in range(3):
        try:
            raise RuntimeError("wrapper") from wrapped
        except RuntimeError as caught:
            wrapped = caught

    assert migration_failure_code(wrapped) == "sdk_migration_disk_space_insufficient"
    # The explicit context chain counts too: `raise ... from None` discards the
    # cause but Python still keeps the original as __context__.
    try:
        try:
            raise OSError(errno.ENOSPC, "No space left on device")
        except OSError:
            raise RuntimeError("sdk_snapshot_failed") from None
    except RuntimeError as caught:
        assert migration_failure_code(caught) == "sdk_migration_disk_space_insufficient"


def test_quota_exhaustion_counts_as_no_space(tmp_path: Path) -> None:
    quota = getattr(errno, "EDQUOT", errno.ENOSPC)
    assert (
        migration_failure_code(OSError(quota, "Disk quota exceeded"))
        == "sdk_migration_disk_space_insufficient"
    )


def test_migration_state_codes_pass_through_because_they_are_already_actionable() -> None:
    assert (
        migration_failure_code(_Coded("sdk_data_plane_migration_bundle_conflict"))
        == "sdk_data_plane_migration_bundle_conflict"
    )
    assert (
        migration_failure_code(_Coded("sdk_migration_swap_state_ambiguous"))
        == "sdk_migration_swap_state_ambiguous"
    )


def test_unknown_failures_keep_their_message_out_of_the_code() -> None:
    error = RuntimeError("/Users/someone/private/path blew up")
    assert migration_failure_code(error) == "sdk_migration_preflight_failed"
    assert (
        migration_failure_code(error, fallback="sdk_migration_bundle_copy_failed")
        == "sdk_migration_bundle_copy_failed"
    )


def test_a_non_code_attribute_never_becomes_a_code() -> None:
    class Odd(RuntimeError):
        code = 17

    assert migration_failure_code(Odd("x")) == "sdk_migration_preflight_failed"


def test_the_chain_survives_a_cycle() -> None:
    first = RuntimeError("first")
    second = RuntimeError("second")
    first.__cause__ = second
    second.__cause__ = first

    assert migration_failure_code(first) == "sdk_migration_preflight_failed"


@pytest.mark.parametrize(
    "code",
    [
        "sdk_migration_disk_space_insufficient",
        "sdk_migration_preflight_failed",
        "sdk_migration_bundle_copy_failed",
        "sdk_migration_cutover_failed",
    ],
)
def test_the_codes_the_desktop_translates_stay_in_sync(code: str) -> None:
    """Desktop maps these exact names to sentences; renaming one blinds it."""
    desktop = Path(__file__).resolve().parents[2] / "desktop" / "src" / "stable-runtime.ts"
    assert desktop.is_file(), desktop
    assert f'"{code}"' in desktop.read_text(encoding="utf-8")
