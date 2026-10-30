"""The visibility background task: when it runs, and that it says so.

Three things were wrong with it and none of them had a test: it started even
with the switch off (real work, holding the GIL, for conclusions nobody
reads), it turned any failure into a zero that looks like "nothing was
dirty", and the service level was applied to the caller rather than the
worker.
"""

import asyncio
import sqlite3

import pytest

from one_memory.api import (
    _report_visibility_failure,
)
from one_memory.config import Settings
from one_memory.memory_visibility import ensure_tables, install_triggers, mode


@pytest.fixture
def library(tmp_path):
    database = tmp_path / "data" / "one-memory.sqlite3"
    database.parent.mkdir(parents=True)
    with sqlite3.connect(database) as db:
        ensure_tables(db)
        install_triggers(db)
        db.commit()
    return database


def _app(library):
    from one_memory.api import create_app

    return create_app(Settings(data_dir=library.parent, source_home=library.parent, api_token="t"))


def test_the_switch_off_does_not_start_the_background_walk(library, monkeypatch):
    """A build with the switch off must behave as it did before A.

    Recomputing then would mean walking every memory through the freshness
    check at startup — real work, holding the GIL — for conclusions nothing
    reads. The triggers are still installed, so switching on later finds a
    library already marked.
    """
    monkeypatch.delenv("ONE_MEMORY_READ_VISIBILITY", raising=False)
    assert mode() == "exact"

    async def scenario() -> None:
        app = _app(library)
        async with app.router.lifespan_context(app):
            assert getattr(app.state, "memory_visibility_task", None) is None

    asyncio.run(scenario())


def test_the_switch_on_starts_it(library, monkeypatch):
    monkeypatch.setenv("ONE_MEMORY_READ_VISIBILITY", "derived")
    assert mode() != "exact"

    async def scenario() -> None:
        app = _app(library)
        async with app.router.lifespan_context(app):
            # Started by the recovery that runs after readiness.
            await app.state.recovery_task
            assert app.state.memory_visibility_task is not None

    asyncio.run(scenario())


def test_a_failing_batch_is_reported_not_swallowed(library, monkeypatch):
    """A silent zero is indistinguishable from "nothing was dirty"."""
    events: list[dict[str, object]] = []
    monkeypatch.setattr(
        "one_memory.api.diagnostic_event",
        lambda **kwargs: events.append(kwargs),
    )

    def broken(_path):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr("one_memory.api._visibility_batch", broken)

    async def scenario() -> None:
        from one_memory.api import _run_visibility_recompute

        app = _app(library)
        task = asyncio.ensure_future(_run_visibility_recompute(app))
        await asyncio.sleep(0.05)
        task.cancel()

    asyncio.run(scenario())

    assert any(event.get("event") == "memory_visibility.background_failed" for event in events), (
        events
    )


def test_the_reported_failure_only_uses_registered_detail_keys():
    """Unlisted keys are rejected, which would turn a report into a failure."""
    events: list[dict[str, object]] = []
    import one_memory.api as api_module

    real = api_module.diagnostic_event
    api_module.diagnostic_event = lambda **kwargs: events.append(kwargs)
    try:
        _report_visibility_failure("recompute", sqlite3.OperationalError("locked"))
    finally:
        api_module.diagnostic_event = real

    assert events
    assert set(events[0]["details"]) <= {"phase", "reason"}
