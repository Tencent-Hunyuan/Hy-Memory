import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from one_memory.imports.access import AuthorizedSourceRoot
from one_memory.imports.jobs import ImportJob, ImportJobStore, ImportStatus
from one_memory.imports.live_sync import PollingLiveSyncSupervisor
from one_memory.imports.live_watch import WATCH_MODE, FileWatchLiveSync
from one_memory.imports.sources.pi import PI_DESCRIPTOR, create_pi_adapter
from one_memory.imports.storage import EvidenceStore
from one_memory.imports.supervisor import ImportSupervisor

# Far enough away that the timer cannot fire during a test: anything that lands
# has to have been driven by the watch, which is the whole point of measuring.
TIMER_OFF = 3600.0


def test_a_new_turn_lands_well_inside_the_ten_second_target(tmp_path: Path) -> None:
    """Measure the real latency, on real files, with the timer switched off.

    The plan's freshness target is ten seconds. A sixty-second poll cannot meet
    it, and shortening the poll pays the discovery cost even when nothing
    changed, so the claim only holds if a write itself wakes the import.
    """

    async def scenario() -> None:
        root, stores = _source(tmp_path)
        evidence, live, imports = stores
        watch = FileWatchLiveSync(
            roots={PI_DESCRIPTOR.source_id: root},
            supervisor=live,
            debounce_seconds=0.1,
            min_interval_seconds=0.1,
        )
        watch.start()
        assert watch.watching == (PI_DESCRIPTOR.source_id,)

        history = imports.create_job(PI_DESCRIPTOR.source_id, operation="history")
        imports.enqueue(history.job_id)
        assert (await imports.wait(history.job_id)).status is ImportStatus.COMPLETED
        baseline = evidence.count()
        assert baseline == 1

        started = time.monotonic()
        _append(root / "session.jsonl", "the turn that has to arrive quickly")
        while time.monotonic() - started < 10:
            if evidence.count() > baseline:
                break
            await asyncio.sleep(0.05)
        elapsed = time.monotonic() - started

        assert evidence.count() == baseline + 1, "追加的轮次没有被监听触发导入"
        assert elapsed < 10, f"延迟 {elapsed:.2f}s 超过十秒硬指标"
        await watch.shutdown()
        await live.shutdown()
        await imports.shutdown()

    asyncio.run(scenario())


def test_start_prime_catches_a_change_that_predates_the_watcher(tmp_path: Path) -> None:
    async def scenario() -> None:
        root, (evidence, live, imports) = _source(tmp_path)
        await live.trigger(PI_DESCRIPTOR.source_id)
        baseline = evidence.count()
        _append(root / "session.jsonl", "written before continuous sync was enabled")
        watch = FileWatchLiveSync(
            roots={PI_DESCRIPTOR.source_id: root},
            supervisor=live,
            debounce_seconds=0.1,
            min_interval_seconds=0.1,
        )
        watch.start(prime=True)
        try:
            deadline = time.monotonic() + 2
            while evidence.count() == baseline and time.monotonic() < deadline:
                await asyncio.sleep(0.02)
            assert evidence.count() == baseline + 1
        finally:
            await watch.shutdown()
            await live.shutdown()
            await imports.shutdown()

    asyncio.run(scenario())


def test_a_run_that_predates_the_change_is_followed_by_a_fresh_one(tmp_path: Path) -> None:
    """Coalescing must not swallow the very write that asked for a scan.

    An import already in flight is handed back as-is, but one that started
    before this write cannot have read it. Polling tolerates that by trying
    again a minute later; a ten-second target cannot.
    """

    async def scenario() -> None:
        root, (_evidence, live, imports) = _source(tmp_path)
        watch = FileWatchLiveSync(
            roots={PI_DESCRIPTOR.source_id: root},
            supervisor=live,
            debounce_seconds=0.1,
            min_interval_seconds=0.1,
        )
        stale = _job(created_at=datetime.now(UTC) - timedelta(seconds=30))
        calls: list[str] = []

        async def trigger(source_id: str) -> ImportJob:
            calls.append(source_id)
            return (
                stale
                if len(calls) == 1
                else stale.model_copy(
                    update={"created_at": datetime.now(UTC), "status": ImportStatus.COMPLETED}
                )
            )

        async def wait(job_id: str) -> ImportJob:
            calls.append(f"wait:{job_id}")
            return stale

        live.trigger = trigger  # type: ignore[method-assign]
        imports.wait = wait  # type: ignore[method-assign]

        await watch.handle_changes({root / "session.jsonl"})

        assert calls == [
            PI_DESCRIPTOR.source_id,
            "wait:already-running",
            PI_DESCRIPTOR.source_id,
        ]
        await imports.shutdown()

    asyncio.run(scenario())


def test_waiting_out_a_stale_run_gives_up_instead_of_looping_forever(tmp_path: Path) -> None:
    """The retry must be bounded, because the watch loop is shared.

    If a source somehow always answers with a run older than the change, looping
    until it wins would stop every other source from ever being triggered. The
    timer is the backstop, so giving up is the cheaper failure.
    """

    async def scenario() -> None:
        root, (_evidence, live, imports) = _source(tmp_path)
        watch = FileWatchLiveSync(
            roots={PI_DESCRIPTOR.source_id: root},
            supervisor=live,
            debounce_seconds=0.1,
            min_interval_seconds=0.1,
        )
        stale = _job(created_at=datetime.now(UTC) - timedelta(seconds=30))
        triggers = 0

        async def trigger(source_id: str) -> ImportJob:
            nonlocal triggers
            triggers += 1
            return stale

        async def wait(job_id: str) -> ImportJob:
            return stale

        live.trigger = trigger  # type: ignore[method-assign]
        imports.wait = wait  # type: ignore[method-assign]

        await asyncio.wait_for(
            watch.handle_changes({root / "session.jsonl"}),
            timeout=5,
        )

        assert triggers == 3
        await imports.shutdown()

    asyncio.run(scenario())


def test_a_burst_from_a_host_in_use_does_not_scan_on_every_write(tmp_path: Path) -> None:
    """Rate limiting is why watching costs less than a short poll, not more.

    A host being actively used writes continuously. Scanning each time would
    spend more time discovering files than reading new ones. A cooldown must
    keep one trailing scan instead of dropping the last change until the timer.
    """

    async def scenario() -> None:
        root, (_evidence, live, imports) = _source(tmp_path)
        watch = FileWatchLiveSync(
            roots={PI_DESCRIPTOR.source_id: root},
            supervisor=live,
            debounce_seconds=0.1,
            min_interval_seconds=0.15,
        )
        triggered: list[str] = []

        async def trigger(source_id: str) -> None:
            triggered.append(source_id)
            return None

        live.trigger = trigger  # type: ignore[method-assign]

        first = await watch.handle_changes({root / "session.jsonl"})
        second = asyncio.create_task(watch.handle_changes({root / "session.jsonl"}))
        try:
            await asyncio.sleep(0.02)
            assert first == (PI_DESCRIPTOR.source_id,)
            assert triggered == [PI_DESCRIPTOR.source_id]
            assert await second == (PI_DESCRIPTOR.source_id,)
            assert triggered == [PI_DESCRIPTOR.source_id] * 2
        finally:
            await watch.shutdown()
            await imports.shutdown()

    asyncio.run(scenario())


def test_only_granted_existing_roots_of_live_sync_sources_are_watched(tmp_path: Path) -> None:
    """Watching an absent directory fails the whole watch set, not just that root.

    Six of the ten sources are not installed on a typical machine, and a source
    can support live sync without this user having granted it, so the watch list
    is narrower than the capability list.
    """

    async def scenario() -> None:
        root, (_evidence, live, imports) = _source(tmp_path)
        watch = FileWatchLiveSync(
            roots={
                PI_DESCRIPTOR.source_id: root,
                "gone": tmp_path / "not-installed",
                "not-live-sync": root,
            },
            supervisor=live,
            debounce_seconds=0.1,
            min_interval_seconds=0.1,
        )

        assert watch.watching == (PI_DESCRIPTOR.source_id,)
        # A path under no watched root belongs to nobody and must not trigger.
        assert watch.source_for(tmp_path / "elsewhere/session.jsonl") is None
        assert watch.source_for(root / "nested/session.jsonl") == PI_DESCRIPTOR.source_id
        assert await watch.handle_changes({tmp_path / "elsewhere/session.jsonl"}) == ()
        await imports.shutdown()

    asyncio.run(scenario())


def test_a_root_reached_through_a_symlink_is_still_recognised(tmp_path: Path) -> None:
    """The watcher reports real paths; a granted root may not be one.

    On macOS a root under `/var` comes back as `/private/var`, so attributing a
    change by comparing the two spellings finds no owner, nothing is triggered,
    and watching is silently dead for that source. This test has to build the
    symlink itself: pytest hands out an already-resolved `tmp_path`, so it would
    otherwise pass no matter which spelling the code compares.
    """

    async def scenario() -> None:
        root, (_evidence, live, imports) = _source(tmp_path)
        link = tmp_path / "link-to-pi"
        link.symlink_to(root)
        watch = FileWatchLiveSync(
            roots={PI_DESCRIPTOR.source_id: link},
            supervisor=live,
            debounce_seconds=0.1,
            min_interval_seconds=0.1,
        )
        triggered: list[str] = []

        async def trigger(source_id: str) -> None:
            triggered.append(source_id)
            return None

        live.trigger = trigger  # type: ignore[method-assign]

        # The change arrives spelled the real way, the grant the linked way.
        assert watch.source_for(root / "session.jsonl") == PI_DESCRIPTOR.source_id
        assert await watch.handle_changes({root / "session.jsonl"}) == (PI_DESCRIPTOR.source_id,)
        assert triggered == [PI_DESCRIPTOR.source_id]
        assert watch.failures == {}
        await imports.shutdown()

    asyncio.run(scenario())


def test_health_states_that_watching_is_layered_on_the_timer(tmp_path: Path) -> None:
    """The mode has to name both, because the timer remains the backstop."""

    async def scenario() -> None:
        _root, (_evidence, live, imports) = _source(tmp_path, mode=WATCH_MODE)

        assert live.health(PI_DESCRIPTOR.source_id).mode == "watch_and_polling"
        assert live.health("unknown-source").mode == "watch_and_polling"
        live.set_watched_sources((PI_DESCRIPTOR.source_id,))
        assert live._poll_interval(PI_DESCRIPTOR.source_id) == max(live.interval_seconds, 15 * 60)
        assert live._poll_interval("unknown-source") == live.interval_seconds
        await imports.shutdown()

    asyncio.run(scenario())


def test_slow_source_does_not_delay_another_source_in_the_same_change_batch(tmp_path: Path) -> None:
    async def scenario() -> None:
        root, (_evidence, live, imports) = _source(tmp_path)
        slow_root = tmp_path / "a-slow"
        slow_root.mkdir()
        live.source_ids = ("slow", PI_DESCRIPTOR.source_id)
        watch = FileWatchLiveSync(
            roots={"slow": slow_root, PI_DESCRIPTOR.source_id: root},
            supervisor=live,
            debounce_seconds=0.01,
            min_interval_seconds=0.01,
        )
        slow_started, fast_started, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def trigger(source_id: str) -> None:
            if source_id == "slow":
                slow_started.set()
                await release.wait()
            else:
                fast_started.set()

        live.trigger = trigger  # type: ignore[method-assign]
        handling = asyncio.create_task(
            watch.handle_changes({slow_root / "slow.jsonl", root / "session.jsonl"})
        )
        try:
            await asyncio.wait_for(slow_started.wait(), 0.5)
            try:
                await asyncio.wait_for(fast_started.wait(), 0.3)
            except TimeoutError:
                pytest.fail("slow source blocked a different source in the same watch batch")
        finally:
            release.set()
            await handling
            await watch.shutdown()
            await imports.shutdown()

    asyncio.run(scenario())


def test_watch_keeps_consuming_batches_while_another_source_is_busy(
    tmp_path: Path, monkeypatch
) -> None:
    async def scenario() -> None:
        root, (_evidence, live, imports) = _source(tmp_path)
        slow_root = tmp_path / "slow"
        slow_root.mkdir()
        live.source_ids = ("slow", PI_DESCRIPTOR.source_id)
        watch = FileWatchLiveSync(
            roots={"slow": slow_root, PI_DESCRIPTOR.source_id: root},
            supervisor=live,
            debounce_seconds=0.01,
            min_interval_seconds=0.01,
        )
        slow_started, fast_started, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def changes(*_args, **_kwargs):
            yield {(2, str(slow_root / "slow.jsonl"))}
            await slow_started.wait()
            yield {(2, str(root / "session.jsonl"))}
            await asyncio.Event().wait()

        async def trigger(source_id: str) -> None:
            if source_id == "slow":
                slow_started.set()
                await release.wait()
            else:
                fast_started.set()

        monkeypatch.setattr("one_memory.imports.live_watch.awatch", changes)
        live.trigger = trigger  # type: ignore[method-assign]
        watch.start()
        try:
            await asyncio.wait_for(slow_started.wait(), 0.5)
            try:
                await asyncio.wait_for(fast_started.wait(), 0.3)
            except TimeoutError:
                pytest.fail("watch collector stopped consuming later source changes")
        finally:
            release.set()
            await watch.shutdown()
            await imports.shutdown()

    asyncio.run(scenario())


def test_a_failing_source_does_not_stop_another_source_in_the_same_batch(
    tmp_path: Path,
) -> None:
    """A raising source must be counted, not propagated into the shared batch.

    The workers share one `handle_changes` await, so an exception escaping one
    of them would abandon the others mid-scan and take the watch loop with it.
    """

    async def scenario() -> None:
        root, (_evidence, live, imports) = _source(tmp_path)
        broken_root = tmp_path / "broken"
        broken_root.mkdir()
        live.source_ids = ("broken", PI_DESCRIPTOR.source_id)
        watch = FileWatchLiveSync(
            roots={"broken": broken_root, PI_DESCRIPTOR.source_id: root},
            supervisor=live,
            debounce_seconds=0.01,
            min_interval_seconds=0.01,
        )
        healthy_started = asyncio.Event()

        async def trigger(source_id: str) -> None:
            if source_id == "broken":
                raise OSError("this source's root went away")
            healthy_started.set()

        live.trigger = trigger  # type: ignore[method-assign]
        try:
            handled = await asyncio.wait_for(
                watch.handle_changes({broken_root / "broken.jsonl", root / "session.jsonl"}),
                1,
            )
            assert set(handled) == {"broken", PI_DESCRIPTOR.source_id}
            assert healthy_started.is_set()
            assert watch.failures == {"broken": 1}
        finally:
            await watch.shutdown()
            await imports.shutdown()

    asyncio.run(scenario())


def test_rate_limited_last_write_gets_one_trailing_scan_without_the_timer(tmp_path: Path) -> None:
    async def scenario() -> None:
        root, (_evidence, live, imports) = _source(tmp_path)
        watch = FileWatchLiveSync(
            roots={PI_DESCRIPTOR.source_id: root},
            supervisor=live,
            debounce_seconds=0.01,
            min_interval_seconds=0.12,
        )
        calls: list[float] = []

        async def trigger(_source_id: str) -> None:
            calls.append(time.monotonic())

        live.trigger = trigger  # type: ignore[method-assign]
        try:
            await watch.handle_changes({root / "session.jsonl"})
            assert len(calls) == 1
            # All writes arrive within one cooldown; the final one must not be lost.
            await asyncio.wait_for(
                asyncio.gather(
                    *(watch.handle_changes({root / "session.jsonl"}) for _ in range(20))
                ),
                1,
            )
            assert len(calls) == 2, "the trailing change was dropped or scanned per write"
            assert calls[1] - calls[0] >= 0.11
        finally:
            await watch.shutdown()
            await imports.shutdown()

    asyncio.run(scenario())


def test_shutdown_cancels_a_pending_trailing_scan(tmp_path: Path) -> None:
    async def scenario() -> None:
        root, (_evidence, live, imports) = _source(tmp_path)
        watch = FileWatchLiveSync(
            roots={PI_DESCRIPTOR.source_id: root},
            supervisor=live,
            debounce_seconds=0.01,
            min_interval_seconds=0.15,
        )
        calls: list[str] = []

        async def trigger(source_id: str) -> None:
            calls.append(source_id)

        live.trigger = trigger  # type: ignore[method-assign]
        await watch.handle_changes({root / "session.jsonl"})
        delayed = asyncio.create_task(watch.handle_changes({root / "session.jsonl"}))
        await asyncio.sleep(0.03)
        try:
            assert not delayed.done(), "there must be a pending trailing scan to cancel"
            await watch.shutdown()
            await asyncio.gather(delayed, return_exceptions=True)
            await asyncio.sleep(0.18)
            assert calls == [PI_DESCRIPTOR.source_id]
        finally:
            await watch.shutdown()
            await imports.shutdown()

    asyncio.run(scenario())


@pytest.mark.parametrize("status", [ImportStatus.PAUSED, ImportStatus.INTERRUPTED])
def test_watch_does_not_wait_or_resume_a_paused_job(tmp_path: Path, status) -> None:
    async def scenario() -> None:
        root, (_evidence, live, imports) = _source(tmp_path)
        watch = FileWatchLiveSync(roots={PI_DESCRIPTOR.source_id: root}, supervisor=live)
        calls: list[str] = []

        async def trigger(source_id):
            calls.append(source_id)
            return _job(created_at=datetime.now(UTC) - timedelta(minutes=1)).model_copy(
                update={"status": status}
            )

        async def wait(_job_id):
            pytest.fail("watch must not wait or force-resume a paused job")

        live.trigger = trigger  # type: ignore[method-assign]
        imports.wait = wait  # type: ignore[method-assign]
        try:
            await watch.handle_changes({root / "session.jsonl"})
            assert calls == [PI_DESCRIPTOR.source_id]
        finally:
            await watch.shutdown()
            await imports.shutdown()

    asyncio.run(scenario())


def test_failed_watch_releases_workers_and_can_restart(tmp_path: Path, monkeypatch) -> None:
    async def scenario() -> None:
        root, (_evidence, live, imports) = _source(tmp_path)
        watch = FileWatchLiveSync(roots={PI_DESCRIPTOR.source_id: root}, supervisor=live)
        worker_started, worker_stopped, restarted = (
            asyncio.Event(),
            asyncio.Event(),
            asyncio.Event(),
        )
        rounds = 0

        async def changes(*_args, **_kwargs):
            nonlocal rounds
            rounds += 1
            yield {(2, str(root / "session.jsonl"))}
            if rounds == 1:
                await worker_started.wait()
                raise OSError("watched root became unavailable")
            await asyncio.Event().wait()

        async def trigger(_source_id):
            if rounds == 1:
                worker_started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    worker_stopped.set()
            else:
                restarted.set()

        monkeypatch.setattr("one_memory.imports.live_watch.awatch", changes)
        live.trigger = trigger  # type: ignore[method-assign]
        watch.start()
        first = watch._task
        assert first is not None
        try:
            await asyncio.wait_for(first, 1)
            assert worker_stopped.is_set()
            assert watch.stopped_unexpectedly
            assert watch._workers == {}
            assert live.health(PI_DESCRIPTOR.source_id).mode == "polling"
            watch.start()
            second = watch._task
            watch.start()
            assert second is not first and watch._task is second
            await asyncio.wait_for(restarted.wait(), 0.5)
            assert not watch.stopped_unexpectedly
        finally:
            await watch.shutdown()
            await imports.shutdown()

    asyncio.run(scenario())


def _source(
    tmp_path: Path,
    *,
    mode: str = "polling",
) -> tuple[Path, tuple[EvidenceStore, PollingLiveSyncSupervisor, ImportSupervisor]]:
    root = tmp_path / "pi"
    root.mkdir(exist_ok=True)
    session = root / "session.jsonl"
    if not session.exists():
        session.write_text(
            json.dumps({"type": "session", "id": "session", "cwd": "/tmp/work/atlas"}) + "\n",
            encoding="utf-8",
        )
        _append(session, "the turn that already existed")
    database = tmp_path / "one-memory.sqlite3"
    evidence = EvidenceStore(database)
    jobs = ImportJobStore(database)
    grant = AuthorizedSourceRoot.grant(
        source_id=PI_DESCRIPTOR.source_id,
        root=root,
        user_confirmed=True,
        home=tmp_path,
    )
    imports = ImportSupervisor(
        adapters={PI_DESCRIPTOR.source_id: create_pi_adapter(grant)},
        evidence_store=evidence,
        job_store=jobs,
    )
    live = PollingLiveSyncSupervisor(
        source_ids=(PI_DESCRIPTOR.source_id,),
        import_supervisor=imports,
        job_store=jobs,
        interval_seconds=TIMER_OFF,
        mode=mode,
    )
    return root, (evidence, live, imports)


def _append(session: Path, text: str) -> None:
    record = {
        "type": "message",
        "id": f"message-{abs(hash(text))}",
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }
    with session.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def _job(*, created_at: datetime) -> ImportJob:
    return ImportJob(
        job_id="already-running",
        user_id="local",
        source_id=PI_DESCRIPTOR.source_id,
        status=ImportStatus.RUNNING,
        phase=None,
        checkpoint=None,
        discovered=0,
        discovered_targets=0,
        emitted=0,
        stored=0,
        revised=0,
        merged=0,
        skipped=0,
        errors=0,
        created_at=created_at,
        updated_at=datetime.now(UTC),
    )


def test_attributing_changed_paths_happens_off_the_event_loop(tmp_path: Path, monkeypatch) -> None:
    """Resolving a host's batch of changed paths blocked the loop for seconds."""
    import threading

    async def scenario() -> None:
        root, (_evidence, live, imports) = _source(tmp_path)
        watch = FileWatchLiveSync(
            roots={PI_DESCRIPTOR.source_id: root},
            supervisor=live,
            debounce_seconds=0.1,
            min_interval_seconds=0.1,
        )
        loop_thread = threading.current_thread()
        resolved_on: list[threading.Thread] = []
        original = watch.source_for

        def recorded(path: Path) -> str | None:
            resolved_on.append(threading.current_thread())
            return original(path)

        monkeypatch.setattr(watch, "source_for", recorded)

        assert await watch.handle_changes({tmp_path / "elsewhere/session.jsonl"}) == ()
        assert resolved_on and loop_thread not in resolved_on
        await imports.shutdown()

    asyncio.run(scenario())
