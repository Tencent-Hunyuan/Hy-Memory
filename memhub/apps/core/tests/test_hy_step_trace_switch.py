"""Rule 15: the per-step HY trace is off unless the debug tier asks for it.

hy memory app's lesson (§六) is that the step-level pipeline log is a
debugging instrument, not a product asset: left on, it grows without bound
and every event is a write the product does not need. One Memory already has
a master switch (`ONE_MEMORY_DIAGNOSTICS`), but the master switch answers a
different question — "is there a diagnostic log at all" — and turning it on
for an incident currently also turns on one `hy.step.completed` event per
step per write plus a summary. Those are the highest-frequency events in the
system, and they are only readable by an Agent working out where a write
spent its time.

So they need their own tier: the master switch stays coarse, and the step
trace is emitted only when the debug tier is asked for as well.
"""

import json

from one_memory.diagnostic_log import (
    configure_diagnostics,
    diagnostics_enabled,
    reset_diagnostics,
)
from one_memory.hy_step_timing import HyStepTimer, hy_step_trace_requested


def _events(path):
    """Events in the log, or none when the log was never made.

    Off means no file at all, so a test that asserts "nothing was written"
    has to accept a missing file rather than demand an empty one.
    With the master switch off the receipt even points at the directory
    rather than at a file that was never made, so that case is accepted too.
    """
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_step_trace_is_not_requested_by_the_plain_diagnostic_switch(monkeypatch) -> None:
    """`ONE_MEMORY_DIAGNOSTICS=1` alone must not turn on per-step tracing.

    That is the whole point of the tier: an operator enabling diagnostics to
    investigate, say, a host install failure must not also start paying for
    one event per HY step on every write.
    """
    monkeypatch.setenv("ONE_MEMORY_DIAGNOSTICS", "1")
    monkeypatch.delenv("ONE_MEMORY_HY_STEP_TRACE", raising=False)
    assert hy_step_trace_requested() is False


def test_step_trace_turns_on_when_the_debug_tier_asks(monkeypatch) -> None:
    for value in ("1", "true", "yes", "on", "debug"):
        monkeypatch.setenv("ONE_MEMORY_HY_STEP_TRACE", value)
        assert hy_step_trace_requested() is True, value


def test_a_plain_zero_does_not_enable_the_step_trace(monkeypatch) -> None:
    """Same rule as the master switch: only affirmative spellings turn it on."""
    for value in ("0", "false", "no", "off", ""):
        monkeypatch.setenv("ONE_MEMORY_HY_STEP_TRACE", value)
        assert hy_step_trace_requested() is False, value


def test_master_switch_off_means_no_step_trace_even_if_debug_asked(tmp_path, monkeypatch) -> None:
    """The debug tier is a refinement of the master switch, not a bypass."""
    monkeypatch.delenv("ONE_MEMORY_DIAGNOSTICS", raising=False)
    monkeypatch.setenv("ONE_MEMORY_HY_STEP_TRACE", "1")
    receipt = configure_diagnostics(tmp_path, session_id="tier-1")
    try:
        timer = HyStepTimer()
        with timer.step("permission_check"):
            pass
        timer.emit(request_id="r", task_id="t")
    finally:
        reset_diagnostics()
    # Master switch off: nothing is written at all, and the tier must not
    # conjure a log into existence. Asserted on the directory rather than on
    # an empty read, because a receipt pointing at a directory that has no
    # file is exactly what "off" looks like here.
    assert not receipt.path.is_file()


def test_the_debug_tier_does_not_turn_the_master_switch_on(tmp_path, monkeypatch) -> None:
    """Asking for the step trace alone must leave diagnostics off.

    This is the mutation the previous assertion could not catch: a gate
    reading only `hy_step_trace_requested()` writes nothing when the master
    switch is off either, so only the explicit `diagnostics_enabled()`
    predicate separates it from a bypass.
    """
    monkeypatch.delenv("ONE_MEMORY_DIAGNOSTICS", raising=False)
    monkeypatch.setenv("ONE_MEMORY_HY_STEP_TRACE", "1")
    configure_diagnostics(tmp_path, session_id="tier-1b")
    try:
        assert diagnostics_enabled() is False
    finally:
        reset_diagnostics()


def test_step_trace_emits_when_both_tiers_are_on(tmp_path, monkeypatch, caplog) -> None:
    monkeypatch.setenv("ONE_MEMORY_DIAGNOSTICS", "1")
    monkeypatch.setenv("ONE_MEMORY_HY_STEP_TRACE", "1")
    caplog.set_level("DEBUG")
    receipt = configure_diagnostics(tmp_path, session_id="tier-2")
    try:
        timer = HyStepTimer()
        with timer.step("permission_check"):
            pass
        timer.emit(request_id="r", task_id="t")
    finally:
        reset_diagnostics()
    events = _events(receipt.path)
    assert [event["event"] for event in events] == ["hy.step.completed", "hy.steps.summary"]


def test_step_trace_stays_off_when_only_the_master_switch_is_on(tmp_path, monkeypatch) -> None:
    """The regression this tier exists to prevent.

    Diagnostics on for an unrelated reason must not silently start emitting
    one event per HY step.
    """
    monkeypatch.setenv("ONE_MEMORY_DIAGNOSTICS", "1")
    monkeypatch.delenv("ONE_MEMORY_HY_STEP_TRACE", raising=False)
    receipt = configure_diagnostics(tmp_path, session_id="tier-3")
    try:
        timer = HyStepTimer()
        with timer.step("permission_check"):
            pass
        with timer.step("embedding", items=2):
            pass
        timer.emit(request_id="r", task_id="t")
    finally:
        reset_diagnostics()
    events = _events(receipt.path)
    assert [event["event"] for event in events] == []
