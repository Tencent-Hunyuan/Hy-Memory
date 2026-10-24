"""The startup dialog can only act on the code it is handed.

`sdk_migration_backfill_failed` covered a full disk, a missing model profile and
a Coding classification gap alike, so every one of them reached the person as
the same unactionable sentence. These tests pin the names that replace it.
"""

from __future__ import annotations

import errno
from pathlib import Path

import pytest

from one_memory.cli import cutover_failure_code, migration_failure_code


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


def test_a_coding_inventory_gap_is_not_reported_as_a_generic_failure() -> None:
    assert (
        migration_failure_code(_Coded("hy_sdk_productivity_variant_invalid"))
        == "sdk_migration_coding_inventory_failed"
    )


def test_the_legacy_working_fact_failure_keeps_its_own_name() -> None:
    error = _Coded("hy_sdk_memory_import_failed")
    error.__cause__ = ValueError("Invalid memory layer: working_fact")

    assert migration_failure_code(error) == "sdk_migration_working_fact_conflict"


def test_migration_state_codes_pass_through_because_they_are_already_actionable() -> None:
    assert (
        migration_failure_code(_Coded("sdk_data_plane_migration_item_conflict"))
        == "sdk_data_plane_migration_item_conflict"
    )
    assert (
        migration_failure_code(_Coded("sdk_migration_staging_invalid"))
        == "sdk_migration_staging_invalid"
    )


def test_unknown_failures_keep_their_message_out_of_the_code() -> None:
    error = RuntimeError("/Users/someone/private/path blew up")
    assert migration_failure_code(error) == "sdk_migration_backfill_failed"
    assert (
        migration_failure_code(error, fallback="sdk_migration_preflight_failed")
        == "sdk_migration_preflight_failed"
    )


def test_a_non_code_attribute_never_becomes_a_code() -> None:
    class Odd(RuntimeError):
        code = 17

    assert migration_failure_code(Odd("x")) == "sdk_migration_backfill_failed"


def test_the_chain_survives_a_cycle() -> None:
    first = RuntimeError("first")
    second = RuntimeError("second")
    first.__cause__ = second
    second.__cause__ = first

    assert migration_failure_code(first) == "sdk_migration_backfill_failed"


@pytest.mark.parametrize(
    "code",
    [
        "sdk_migration_disk_space_insufficient",
        "sdk_migration_working_fact_conflict",
        "sdk_migration_coding_inventory_failed",
        "sdk_migration_work_pending",
    ],
)
def test_the_codes_the_desktop_translates_stay_in_sync(code: str) -> None:
    """Desktop maps these exact names to sentences; renaming one blinds it."""
    desktop = Path(__file__).resolve().parents[2] / "desktop" / "src" / "stable-runtime.ts"
    assert desktop.is_file(), desktop
    assert f'"{code}"' in desktop.read_text(encoding="utf-8")


def test_cutover_work_counts_do_not_become_the_dialog_text() -> None:
    """The blocker code carries a JSON body; the person gets one name."""
    blocked = _Coded('sdk_data_plane_cutover_work_pending:{"memory_tasks":591}')

    assert cutover_failure_code(blocked) == "sdk_migration_work_pending"
    assert (
        cutover_failure_code(_Coded("sdk_data_plane_cutover_missing"))
        == "sdk_data_plane_cutover_missing"
    )
    assert (
        cutover_failure_code(RuntimeError("/Users/someone/private"))
        == "sdk_migration_cutover_failed"
    )
