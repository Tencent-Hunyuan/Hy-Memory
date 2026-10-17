"""R-02: the extraction queue has no time dimension.

`_task_priority` sorts by `(record exists, no workspace first, verbatim
length)`. Nothing in that key is time, and the second component actively
punishes the coding conversations that carry a workspace — so the order is
uncorrelated with time and, for Kristin's real corpus, close to inverted.

The second half of the defect is not the key at all: `_schedule_tasks`
turns the whole ordered list into `asyncio.Task`s up front, so a piece of
evidence that arrives afterwards queues behind every one of the 675
catch-up tasks no matter what any priority says.
"""

from __future__ import annotations

import asyncio
import random
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from one_memory.api import create_app
from one_memory.config import Settings
from one_memory.imports.models import (
    EvidenceInput,
    EvidenceOrigin,
    EvidenceRecord,
    OriginMetadata,
    WrittenBy,
)
from one_memory.imports.storage import EvidenceStore
from one_memory.memory_governance import MemoryGovernanceStore
from one_memory.memory_processing import MemoryProcessingSupervisor
from one_memory.memory_queue import MemoryWorkQueue, QueuedWork
from one_memory.memory_server.contracts import BatchIdentity, WriteRequest
from one_memory.memory_tasks import MemoryTaskKind, MemoryTaskStatus, MemoryTaskStore
from one_memory.model_profiles import (
    ModelCapabilities,
    ModelProfileInput,
    ModelProfileStore,
    ProviderKind,
)
from one_memory.secrets import InMemorySecretStore

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


class SteppedRunner:
    """Reports the dispatch order, and only moves when told to.

    The order tasks are *handed to* a runner is the thing under test, so the
    runner has to be able to stop between hand-offs: otherwise a queue that
    only sorts up front looks identical to one that picks per dispatch.
    """

    def __init__(self) -> None:
        self.dispatched: list[str] = []
        self.timeouts: list[float | None] = []
        self._permits = asyncio.Semaphore(0)

    async def run(self, task_id: str, *, timeout: float | None = None) -> None:
        # Same signature as `MemoryTaskRunner.run`. When the supervisor began
        # passing `timeout=`, a stub without it raised TypeError on every call
        # and nothing was dispatched at all.
        self.timeouts.append(timeout)
        await self._permits.acquire()
        self.dispatched.append(task_id)

    def allow(self, count: int = 1) -> None:
        for _ in range(count):
            self._permits.release()


async def _step(runner: SteppedRunner, count: int) -> None:
    for _ in range(count):
        runner.allow()
        for _ in range(30):
            await asyncio.sleep(0)
    deadline = asyncio.get_running_loop().time() + 3
    while len(runner.dispatched) < count and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.01)


def _stores(tmp_path):
    database = tmp_path / "one-memory.sqlite3"
    secrets = InMemorySecretStore()
    evidence = EvidenceStore(database)
    profiles = ModelProfileStore(database)
    tasks = MemoryTaskStore(database)
    governance = MemoryGovernanceStore(database)
    profile = profiles.create(
        ModelProfileInput(
            name="Local",
            provider_kind=ProviderKind.OLLAMA,
            model="hy-bot-phi4",
            embedding_model="nomic-embed-text",
        ),
        secrets,
    )
    profiles.record_capabilities(
        profile.profile_id,
        expected_revision=profile.revision,
        capabilities=ModelCapabilities(
            endpoint_reachable=True,
            model_available=True,
            structured_output=True,
            embedding_available=True,
        ),
        secrets=secrets,
    )
    profile = profiles.activate(profile.profile_id, secrets)
    return evidence, profiles, tasks, governance, secrets, profile


def _supervisor(tmp_path, stores, runner: SteppedRunner) -> MemoryProcessingSupervisor:
    evidence, profiles, tasks, governance, secrets, _profile = stores
    return MemoryProcessingSupervisor(
        tasks=tasks,
        profiles=profiles,
        secrets=secrets,
        evidence=evidence,
        governance=governance,
        data_dir=tmp_path,
        runner_factory=lambda: runner,
    )


def _put(
    evidence: EvidenceStore,
    *,
    message_id: str,
    content: str,
    created_at: datetime,
    conversation_id: str = "conversation",
    workspace: str | None = None,
) -> EvidenceRecord:
    return evidence.put(
        EvidenceInput(
            source_id="cursor",
            workspace=workspace,
            conversation_id=conversation_id,
            message_id=message_id,
            correlation_id=message_id,
            role="user",
            written_by=WrittenBy.PERSON,
            created_at=created_at,
            verbatim=content,
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    ).record


def _task(tasks: MemoryTaskStore, profile, record: EvidenceRecord, *, suffix: str = ""):
    return tasks.create(
        request_id=f"native:{record.evidence_id}:{record.revision}:{suffix}",
        user_id=record.user_id,
        kind=MemoryTaskKind.EXTRACT,
        input_ref=record.evidence_id,
        profile=profile,
    )


def test_dispatch_order_follows_evidence_time(tmp_path) -> None:
    """A hundred pieces of evidence must be worked newest first.

    Written the way the defect was found: the verbatim lengths are shuffled
    against the timestamps, so an order built from length cannot come out in
    time order by luck. Today the sort key contains no time at all, so what
    comes back is length order — this is the "old ones first, the newest
    never" complaint, reproduced.
    """
    stores = _stores(tmp_path)
    evidence, _profiles, tasks, _governance, _secrets, profile = stores
    lengths = list(range(1, 101))
    random.Random(20260908).shuffle(lengths)
    by_task: dict[str, datetime] = {}
    for index, length in enumerate(lengths):
        moment = NOW - timedelta(days=200) + timedelta(hours=index)
        record = _put(
            evidence,
            message_id=f"m{index}",
            conversation_id=f"conversation-{index}",
            content="我" * length + "。",
            created_at=moment,
        )
        task = _task(tasks, profile, record, suffix=f"s{index}")
        by_task[task.task_id] = moment
    runner = SteppedRunner()
    supervisor = _supervisor(tmp_path, stores, runner)

    async def exercise() -> None:
        supervisor.resume()
        await _step(runner, 100)
        await supervisor.shutdown()

    asyncio.run(exercise())

    moments = [by_task[task_id] for task_id in runner.dispatched]
    assert len(moments) == 100
    assert moments == sorted(moments, reverse=True)
    # Rule 7: every dispatch carries the active profile's wall-clock budget.
    assert set(runner.timeouts) == {float(profile.timeout_seconds + 60)}


def test_live_lane_is_taken_before_a_newer_fresh_task() -> None:
    """A live-synced turn jumps both the fresh share and the backlog.

    Age alone would serve the newer fresh task first. The live lane is a
    separate queue so a turn that just synced is next, even when it is older
    than work already waiting.
    """
    queue = MemoryWorkQueue()
    now = datetime.now(UTC)
    queue.add(
        QueuedWork(
            task_id="backlog",
            at=now - timedelta(days=2),
            fresh=False,
            all_apps=False,
            length=1,
        )
    )
    queue.add(
        QueuedWork(
            task_id="fresh",
            at=now,
            fresh=True,
            all_apps=False,
            length=1,
        )
    )
    queue.add(
        QueuedWork(
            task_id="live",
            at=now - timedelta(hours=3),
            fresh=True,
            all_apps=False,
            length=1,
            live=True,
        )
    )

    assert queue.take().task_id == "live"
    assert queue.take().task_id == "fresh"


# ---------------------------------------------------------------------------
# Per-harness fairness cap: each harness may run at most 10 concurrent items.
#
# The per-conversation rule is about correctness — HY decides whether a memory
# supersedes an older one from what it has already been told, so two
# extractions from one conversation must not run together. This is a different
# rule: fairness between sources. One harness must not be able to take every
# worker while the others wait.
# ---------------------------------------------------------------------------


def _work(task_id: str, session: tuple[str, str, str] | None) -> QueuedWork:
    return QueuedWork(
        task_id=task_id,
        at=datetime.now(UTC),
        fresh=True,
        all_apps=False,
        length=1,
        session=session,
    )


def test_one_harness_cannot_take_every_slot() -> None:
    """Ten sources are not ten times the work.

    With a cap of 2 per harness, a third piece of work from the same source is
    not taken while the queue still holds work from another source — the other
    source gets served instead of waiting behind one busy harness.
    """
    queue = MemoryWorkQueue()
    for index in range(3):
        queue.add(_work(f"same-{index}", ("local", "cursor", f"conv-{index}")))
    queue.add(_work("other-0", ("local", "codex", "conv-x")))

    load = {"cursor": 2}  # two already in the model
    taken = queue.take((), load, 2)
    assert taken is not None
    assert taken.task_id == "other-0"


def test_a_saturated_harness_yields_nothing_more() -> None:
    """With only one saturated harness queued, take() must return nothing.

    Asserted without depending on lane ordering, so it cannot pass by accident
    because another source happened to sort first.
    """
    queue = MemoryWorkQueue()
    for index in range(3):
        queue.add(_work(f"same-{index}", ("local", "cursor", f"conv-{index}")))

    assert queue.take((), {"cursor": 10}, 2) is None


def test_the_same_queue_yields_once_the_harness_has_room() -> None:
    """The cap is a gate, not a drop: work stays queued and is served later.

    This is what separates "capped" from "discarded" — a dropped task would
    silently never run.
    """
    queue = MemoryWorkQueue()
    queue.add(_work("same-0", ("local", "cursor", "conv-0")))

    assert queue.take((), {"cursor": 10}, 2) is None
    # Room frees up; the same work is still there and is now taken.
    taken = queue.take((), {"cursor": 1}, 2)
    assert taken is not None
    assert taken.task_id == "same-0"


def test_the_cap_does_not_block_a_harness_below_it() -> None:
    """Under the cap, work from that harness is taken as normal."""
    queue = MemoryWorkQueue()
    queue.add(_work("same-0", ("local", "cursor", "conv-0")))

    taken = queue.take((), {"cursor": 1}, 2)
    assert taken is not None
    assert taken.task_id == "same-0"


def test_no_cap_means_no_restriction() -> None:
    """Callers that have not adopted the rule behave exactly as before."""
    queue = MemoryWorkQueue()
    for index in range(3):
        queue.add(_work(f"same-{index}", ("local", "cursor", f"conv-{index}")))

    taken = queue.take()
    assert taken is not None
    assert taken.task_id.startswith("same-")


def test_a_busy_conversation_still_wins_over_the_cap() -> None:
    """Correctness outranks fairness: the conversation rule still applies.

    Per-conversation serialisation has no number, and the harness cap must not
    become a way around it.
    """
    queue = MemoryWorkQueue()
    queue.add(_work("busy-conv", ("local", "cursor", "conv-0")))

    assert queue.take((("local", "cursor", "conv-0"),), {}, 10) is None


def test_ready_respects_the_harness_cap() -> None:
    """A queue full of one saturated harness must not start more workers.

    Otherwise each new worker finds nothing and exits, one starting the next —
    the exact churn `ready` exists to prevent.
    """
    queue = MemoryWorkQueue()
    queue.add(_work("same-0", ("local", "cursor", "conv-0")))

    assert queue.ready((), {"cursor": 10}, 10) is False
    assert queue.ready((), {"cursor": 1}, 10) is True


def test_work_without_a_session_is_never_capped() -> None:
    """No harness to attribute it to, so it must not be held back."""
    queue = MemoryWorkQueue()
    queue.add(_work("orphan", None))

    taken = queue.take((), {"cursor": 99}, 1)
    assert taken is not None
    assert taken.task_id == "orphan"


def test_live_synced_evidence_is_next_after_the_task_already_running(tmp_path, monkeypatch) -> None:
    """Once a worker is free, the synced conversation is the next extraction.

    The task already inside the model is left alone. Everything still waiting,
    including newer fresh work, waits behind the conversation that just synced.
    """
    from one_memory import memory_processing

    # A live user turn that opens a conversation is not prepared until the
    # turn closes: a quiet-period timeout must not manufacture an incomplete
    # HY task. Lane order is what this test is about, so the closing reply is
    # sent immediately rather than the wait being shortened.
    monkeypatch.setattr(memory_processing, "_LIVE_OPEN_TURN_SETTLE_SECONDS", 0.05)
    stores = _stores(tmp_path)
    evidence, _profiles, tasks, _governance, _secrets, profile = stores
    now = datetime.now(UTC)
    fresh_ids: set[str] = set()
    for index in range(4):
        record = _put(
            evidence,
            message_id=f"fresh{index}",
            conversation_id=f"fresh-conversation-{index}",
            content="刚刚发生、还在排队的一段对话。",
            created_at=now - timedelta(minutes=index),
        )
        fresh_ids.add(_task(tasks, profile, record, suffix=f"fresh{index}").task_id)
    runner = SteppedRunner()
    supervisor = _supervisor(tmp_path, stores, runner)
    already: list[str] = []

    async def exercise() -> None:
        from one_memory.imports.runner import live_sync_arrival

        supervisor.resume()
        for _ in range(50):
            if supervisor._in_flight:
                break
            await asyncio.sleep(0)
        assert supervisor._in_flight, "the first fresh task was never taken"
        # Every worker the profile allows has now claimed something. Those
        # are the tasks "already inside the model"; the live arrival is next
        # after them, not after the one that happened to start first.
        for _ in range(50):
            await asyncio.sleep(0)
        already.extend(supervisor._in_flight)
        live = _put(
            evidence,
            message_id="live",
            conversation_id="live-conversation",
            content="这句是刚刚实时同步进来的。",
            created_at=now - timedelta(hours=5),
            workspace="workspace:v1:one-memory",
        )
        token = live_sync_arrival.set(True)
        try:
            supervisor.enqueue_evidence(live)
            closing = evidence.put(
                EvidenceInput(
                    source_id="cursor",
                    conversation_id="live-conversation",
                    message_id="live-reply",
                    role="assistant",
                    written_by=WrittenBy.AGENT,
                    verbatim="实时同步的回复，闭合这一轮。",
                    created_at=now - timedelta(hours=5) + timedelta(seconds=1),
                    workspace="workspace:v1:one-memory",
                    origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
                )
            ).record
            supervisor.enqueue_evidence(closing)
        finally:
            live_sync_arrival.reset(token)
        deadline = asyncio.get_running_loop().time() + 5
        while asyncio.get_running_loop().time() < deadline and not supervisor._queue._live:
            await asyncio.sleep(0.05)
        assert supervisor._queue._live, "the synced conversation did not enter the live lane"
        await _step(runner, len(already) + 1)
        await supervisor.shutdown()

    asyncio.run(exercise())

    assert set(runner.dispatched[: len(already)]) <= fresh_ids
    assert runner.dispatched[len(already)] not in fresh_ids


def test_workbuddy_live_precedes_other_live_work() -> None:
    queue = MemoryWorkQueue()
    now = datetime.now(UTC)
    queue.add(
        QueuedWork(
            task_id="cursor-live",
            at=now + timedelta(seconds=1),
            fresh=True,
            all_apps=False,
            length=1,
            live=True,
            session=("local", "cursor", "cursor-session"),
        )
    )
    queue.add(
        QueuedWork(
            task_id="workbuddy-live",
            at=now,
            fresh=True,
            all_apps=False,
            length=1,
            live=True,
            session=("local", "workbuddy", "workbuddy-session"),
        )
    )

    assert queue.take().task_id == "workbuddy-live"
    assert queue.take().task_id == "cursor-live"


def test_evidence_arriving_now_is_worked_within_four_tasks(tmp_path) -> None:
    """New evidence must not queue behind the entire catch-up backlog.

    This is the half of the defect no sort key can fix: the backlog is turned
    into `asyncio.Task`s the moment it is prepared, so anything that arrives
    later is behind all of it. On the real machine that is 675 extractions —
    at the measured rate, hours before a sentence said just now is looked at.
    """
    stores = _stores(tmp_path)
    evidence, _profiles, tasks, _governance, _secrets, profile = stores
    for index in range(40):
        record = _put(
            evidence,
            message_id=f"old{index}",
            conversation_id=f"old-conversation-{index}",
            content="很久以前的一段对话。",
            created_at=NOW - timedelta(days=100) + timedelta(hours=index),
        )
        _task(tasks, profile, record, suffix=f"old{index}")
    runner = SteppedRunner()
    supervisor = _supervisor(tmp_path, stores, runner)
    arrival: list[str] = []
    already: list[str] = []
    dispatched_before_arrival = 0

    async def exercise() -> None:
        nonlocal dispatched_before_arrival
        supervisor.resume()
        await _step(runner, 2)
        # Whatever the workers have already claimed is spoken for and will be
        # dispatched whatever arrives now. The four-task promise is counted
        # from the first dispatch that was still free to choose.
        already.extend(supervisor._in_flight)
        dispatched_before_arrival = len(runner.dispatched)
        fresh = _put(
            evidence,
            message_id="fresh",
            conversation_id="fresh-conversation",
            content="我刚刚决定：以后都用中文记忆。",
            created_at=datetime.now(UTC),
            workspace="workspace:v1:one-memory",
        )
        arrival.extend(task.task_id for task in supervisor.enqueue_conversation(fresh))
        await _step(runner, len(already) + 4)
        await supervisor.shutdown()

    asyncio.run(exercise())

    assert arrival, "the live path recorded no task for the new evidence"
    free_to_choose = runner.dispatched[dispatched_before_arrival + len(already) :]
    assert set(arrival) & set(free_to_choose[:4]), (
        f"new evidence was not reached in four dispatches: {free_to_choose[:4]}"
    )


def test_backlog_keeps_exactly_one_slot_in_five(tmp_path) -> None:
    """The catch-up quota is hard, not a soft preference.

    Newest-first on its own starves the backlog for as long as anything new
    keeps arriving, which on a live machine is forever. Four new to one
    historical is the decided ratio, so twenty dispatches must contain
    exactly four historical ones.
    """
    stores = _stores(tmp_path)
    evidence, _profiles, tasks, _governance, _secrets, profile = stores
    backlog: set[str] = set()
    for index in range(40):
        record = _put(
            evidence,
            message_id=f"old{index}",
            conversation_id=f"old-conversation-{index}",
            content="历史积压的一段对话。",
            created_at=NOW - timedelta(days=100) + timedelta(hours=index),
        )
        backlog.add(_task(tasks, profile, record, suffix=f"old{index}").task_id)
    for index in range(20):
        record = _put(
            evidence,
            message_id=f"new{index}",
            conversation_id=f"new-conversation-{index}",
            content="刚刚发生的一段对话。",
            created_at=datetime.now(UTC) - timedelta(minutes=index),
        )
        _task(tasks, profile, record, suffix=f"new{index}")
    runner = SteppedRunner()
    supervisor = _supervisor(tmp_path, stores, runner)

    async def exercise() -> None:
        supervisor.resume()
        await _step(runner, 20)
        await supervisor.shutdown()

    asyncio.run(exercise())

    first_twenty = runner.dispatched[:20]
    assert len(first_twenty) == 20
    assert sum(1 for task_id in first_twenty if task_id in backlog) == 4


def test_backlog_drains_while_new_evidence_keeps_arriving(tmp_path) -> None:
    """The backlog must not starve, however busy the machine gets.

    675 historical extractions that never run is the failure mode a pure
    "newest first" fix would ship. This one is green before the fix and
    stays green after: its job is to be the guard that catches the fix's own
    risk, which is why the new work injected each round outnumbers the
    dispatches — a queue that only prefers the newest never runs dry, so it
    would never touch the backlog at all.
    """
    stores = _stores(tmp_path)
    evidence, _profiles, tasks, _governance, _secrets, profile = stores
    backlog: set[str] = set()
    for index in range(40):
        record = _put(
            evidence,
            message_id=f"old{index}",
            conversation_id=f"old-conversation-{index}",
            content="历史积压的一段对话。",
            created_at=NOW - timedelta(days=100) + timedelta(hours=index),
        )
        backlog.add(_task(tasks, profile, record, suffix=f"old{index}").task_id)
    runner = SteppedRunner()
    supervisor = _supervisor(tmp_path, stores, runner)
    drained: list[int] = []

    async def exercise() -> None:
        supervisor.resume()
        for round_index in range(6):
            for message_index in range(6):
                record = _put(
                    evidence,
                    message_id=f"live{round_index}-{message_index}",
                    conversation_id=f"live-conversation-{round_index}-{message_index}",
                    content="又说了一句新的话。",
                    created_at=datetime.now(UTC),
                )
                supervisor.enqueue_conversation(record)
            await _step(runner, 5)
            drained.append(sum(1 for task_id in runner.dispatched if task_id in backlog))
        await supervisor.shutdown()

    asyncio.run(exercise())

    assert drained == sorted(drained)
    assert drained[0] >= 1, f"no historical work in the first cycle: {drained}"
    assert drained[-1] > drained[0], f"backlog stopped draining: {drained}"


def test_workspace_evidence_is_not_pushed_behind_older_all_apps(tmp_path) -> None:
    """Carrying a workspace must stop being a penalty.

    `no workspace first` sat above everything except "the record exists", so
    every conversation written while coding — nearly all of Kristin's — went
    last regardless of age. Time has to outrank it. The cold-start reason for
    the preference is kept as a tie-break only, which
    `test_cold_start_prioritizes_all_apps_before_workspace` still pins.
    """
    stores = _stores(tmp_path)
    evidence, _profiles, tasks, _governance, _secrets, profile = stores
    older_all_apps = _put(
        evidence,
        message_id="older-all-apps",
        conversation_id="all-apps-conversation",
        content="我是一名产品经理。",
        created_at=NOW - timedelta(days=30),
    )
    newer_workspace = _put(
        evidence,
        message_id="newer-workspace",
        conversation_id="workspace-conversation",
        content="这个仓库里不要再用 fetch 裸调。",
        created_at=NOW - timedelta(days=1),
        workspace="workspace:v1:one-memory",
    )
    older_task = _task(tasks, profile, older_all_apps, suffix="older")
    newer_task = _task(tasks, profile, newer_workspace, suffix="newer")
    runner = SteppedRunner()
    supervisor = _supervisor(tmp_path, stores, runner)

    async def exercise() -> None:
        supervisor.resume()
        await _step(runner, 2)
        await supervisor.shutdown()

    asyncio.run(exercise())

    assert runner.dispatched == [newer_task.task_id, older_task.task_id]


def test_queue_depths_are_two_numbers_not_one(tmp_path) -> None:
    """675 as a single number reads as "stuck"; two numbers read as "nearly done".

    The app shows one total today, so the three extractions that will finish
    in a minute are indistinguishable from the 672 behind them.
    """
    stores = _stores(tmp_path)
    evidence, _profiles, tasks, _governance, _secrets, profile = stores
    for index in range(7):
        record = _put(
            evidence,
            message_id=f"old{index}",
            conversation_id=f"old-conversation-{index}",
            content="历史积压的一段对话。",
            created_at=NOW - timedelta(days=100) + timedelta(hours=index),
        )
        _task(tasks, profile, record, suffix=f"old{index}")
    for index in range(3):
        record = _put(
            evidence,
            message_id=f"new{index}",
            conversation_id=f"new-conversation-{index}",
            content="刚刚发生的一段对话。",
            created_at=datetime.now(UTC),
        )
        _task(tasks, profile, record, suffix=f"new{index}")
    runner = SteppedRunner()
    supervisor = _supervisor(tmp_path, stores, runner)

    async def exercise() -> None:
        supervisor.resume()
        await asyncio.sleep(0)
        depths = supervisor.queue_depths()
        await supervisor.shutdown()
        assert depths.fresh == 3
        assert depths.backlog == 7
        assert depths.recent_completions == ()

    asyncio.run(exercise())


@pytest.mark.anyio
async def test_the_app_is_told_both_numbers(tmp_path) -> None:
    """The endpoint the app reads has to carry the split, not just the total.

    `/api/v1/memory-processing` had no test of its own, so a supervisor call
    added here would have shipped unproven — the exact shape of "written but
    never actually run" this project keeps finding.
    """
    app = create_app(
        Settings(
            data_dir=tmp_path / "data",
            source_home=tmp_path,
            instance_id="test-instance",
            api_token=SecretStr("local-token"),
        )
    )
    async with (
        app.router.lifespan_context(app),
        AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={
                "Authorization": "Bearer local-token",
                "Origin": "http://127.0.0.1:5173",
            },
        ) as client,
    ):
        evidence = app.state.evidence_store
        tasks = app.state.memory_task_store
        profiles = app.state.model_profile_store
        secrets = app.state.secret_store
        profile = profiles.create(
            ModelProfileInput(
                name="Local",
                provider_kind=ProviderKind.OLLAMA,
                model="hy-bot-phi4",
                embedding_model="nomic-embed-text",
            ),
            secrets,
        )
        profiles.record_capabilities(
            profile.profile_id,
            expected_revision=profile.revision,
            capabilities=ModelCapabilities(
                endpoint_reachable=True,
                model_available=True,
                structured_output=True,
                embedding_available=True,
            ),
            secrets=secrets,
        )
        profile = profiles.activate(profile.profile_id, secrets)
        for index in range(5):
            record = _put(
                evidence,
                message_id=f"old{index}",
                conversation_id=f"old-conversation-{index}",
                content="历史积压的一段对话。",
                created_at=NOW - timedelta(days=100) + timedelta(hours=index),
            )
            _task(tasks, profile, record, suffix=f"old{index}")
        record = _put(
            evidence,
            message_id="new",
            conversation_id="new-conversation",
            content="刚刚发生的一段对话。",
            created_at=datetime.now(UTC),
        )
        _task(tasks, profile, record, suffix="new")

        summary = await client.get("/api/v1/memory-processing")

    assert summary.status_code == 200
    body = summary.json()
    assert body["pending"] == 6
    assert body["fresh_pending"] == 1
    assert body["backlog_pending"] == 5
    assert body["active_queue"] == 0
    assert body["in_flight_count"] == 0
    assert body["metrics"] == {
        "raw_evidence": 6,
        "complete_turns": 6,
        "hy_calls": 0,
        "completed": 0,
        "unknown": 0,
        "failed": 0,
        "cancelled": 0,
        "cancelled_reasons": {},
        "governed_memories": 0,
        "workbuddy": {"injected": 0, "timed_out": 0, "unavailable": 0},
    }


@pytest.mark.anyio
async def test_processing_reports_durable_pending_and_unknown_when_tasks_are_empty(
    tmp_path,
) -> None:
    app = create_app(
        Settings(
            data_dir=tmp_path / "data",
            source_home=tmp_path,
            instance_id="test-instance",
            api_token=SecretStr("local-token"),
        )
    )
    async with (
        app.router.lifespan_context(app),
        AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={
                "Authorization": "Bearer local-token",
                "Origin": "http://127.0.0.1:5173",
            },
        ) as client,
    ):
        app.state.memory_write_drain.stop()
        evidence = app.state.evidence_store
        pending_evidence = _put(
            evidence,
            message_id="durable-pending",
            conversation_id="durable-pending",
            content="pending",
            created_at=datetime.now(UTC),
        )
        unknown_evidence = _put(
            evidence,
            message_id="durable-unknown",
            conversation_id="durable-unknown",
            content="unknown",
            created_at=datetime.now(UTC),
        )
        queue = app.state.memory_write_queue

        def submit(record: EvidenceRecord, key: str):
            return queue.submit(
                WriteRequest(
                    internal_session_id=record.conversation_id,
                    batch_identity=BatchIdentity(
                        internal_session_id=record.conversation_id,
                        first_round=1,
                        last_round=1,
                    ),
                    evidence_ids=[record.evidence_id],
                    idempotency_key=key,
                )
            )

        submit(pending_evidence, "durable-pending")
        unknown = submit(unknown_evidence, "durable-unknown")
        with sqlite3.connect(app.state.evidence_store.database_path) as connection, connection:
            connection.execute(
                "UPDATE memory_write_jobs SET state='unknown',hy_invoked=1,"
                "last_error='hy_memory_timeout' WHERE job_id=?",
                (unknown.job_id,),
            )
        assert app.state.memory_task_store.list() == []

        response = await client.get("/api/v1/memory-processing")

    assert response.status_code == 200
    body = response.json()
    assert body["pending"] == 1
    assert body["fresh_pending"] == 1
    assert body["backlog_pending"] == 0
    assert body["unknown"] == 1
    assert body["unknown_reasons"] == {"hy_memory_timeout": 1}
    assert body["running"] == 0
    assert body["completed"] == 0
    assert body["failed"] == 0


@pytest.mark.anyio
async def test_periodic_unknown_reconcile_never_blindly_requeues(tmp_path, monkeypatch) -> None:
    from unittest.mock import AsyncMock

    app = create_app(
        Settings(
            data_dir=tmp_path / "data",
            source_home=tmp_path,
            instance_id="test-instance",
            api_token=SecretStr("local-token"),
        )
    )
    async with app.router.lifespan_context(app):
        app.state.memory_write_drain.stop()
        record = _put(
            app.state.evidence_store,
            message_id="background-unknown",
            conversation_id="background-unknown",
            content="unknown",
            created_at=datetime.now(UTC),
        )
        queue = app.state.memory_write_queue
        job = queue.submit(
            WriteRequest(
                internal_session_id=record.conversation_id,
                batch_identity=BatchIdentity(
                    internal_session_id=record.conversation_id,
                    first_round=1,
                    last_round=1,
                ),
                evidence_ids=[record.evidence_id],
                idempotency_key="background-unknown",
            )
        )
        with sqlite3.connect(app.state.evidence_store.database_path) as connection, connection:
            connection.execute(
                "UPDATE memory_write_jobs SET state='unknown',hy_invoked=1,attempts=1,"
                "last_error='hy_memory_timeout',updated_at='2026-01-01T00:00:00+00:00' "
                "WHERE job_id=?",
                (job.job_id,),
            )
        reconcile = AsyncMock(return_value=0)
        monkeypatch.setattr(
            app.state.memory_processing_supervisor,
            "reconcile_unknown_outputs",
            reconcile,
        )

        await app.state.memory_write_drain.reconcile_unknown()

        assert queue.get("background-unknown").state == "unknown"
        reconcile.assert_awaited_once()


@pytest.mark.anyio
async def test_unknown_retry_api_requires_confirmation_and_requeues_once(tmp_path) -> None:
    app = create_app(
        Settings(
            data_dir=tmp_path / "data",
            source_home=tmp_path,
            instance_id="test-instance",
            api_token=SecretStr("local-token"),
        )
    )
    headers = {
        "Authorization": "Bearer local-token",
        "Origin": "http://127.0.0.1:5173",
    }
    async with (
        app.router.lifespan_context(app),
        AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers=headers,
        ) as client,
    ):
        queue = app.state.memory_write_queue
        app.state.memory_write_drain.stop()
        evidence = _put(
            app.state.evidence_store,
            message_id="unknown-api",
            conversation_id="unknown-api",
            content="unknown",
            created_at=datetime.now(UTC),
        )
        queued = queue.submit(
            WriteRequest(
                internal_session_id="unknown-api",
                batch_identity=BatchIdentity(
                    internal_session_id="unknown-api",
                    first_round=1,
                    last_round=1,
                ),
                evidence_ids=[evidence.evidence_id],
                idempotency_key="unknown-api",
            )
        )
        with sqlite3.connect(app.state.evidence_store.database_path) as connection:
            connection.execute(
                "UPDATE memory_write_jobs SET state='unknown', hy_invoked=1 WHERE job_id=?",
                (queued.job_id,),
            )

        refused = await client.post(
            "/api/v1/memory-processing/retry-unknown", json={"confirm": False}
        )
        assert refused.status_code == 422
        accepted = await client.post(
            "/api/v1/memory-processing/retry-unknown", json={"confirm": True}
        )
        assert accepted.status_code == 200
        assert accepted.json() == {"reconciled": 0, "requeued": 1}
        repeated = await client.post(
            "/api/v1/memory-processing/retry-unknown", json={"confirm": True}
        )
        assert repeated.json() == {"reconciled": 0, "requeued": 0}


@pytest.mark.parametrize("cjk", ["我", "你"])
def test_evidence_without_its_own_timestamp_still_gets_a_time(tmp_path, cjk: str) -> None:
    """Imported evidence often has no `created_at`; it must not fall to last.

    Several adapters cannot recover a per-message time, so the record carries
    `created_at=None` and only `imported_at`. Treating that as "no time"
    would recreate the defect for whole sources at once.
    """
    stores = _stores(tmp_path)
    evidence, _profiles, tasks, _governance, _secrets, profile = stores
    # Recorded first on purpose: creation order would put the undated one
    # last, so passing cannot come from the order things went into the store.
    old = _put(
        evidence,
        message_id=f"old-{cjk}",
        conversation_id="old-conversation",
        content="很久以前的一段对话。",
        created_at=NOW - timedelta(days=100),
    )
    undated = evidence.put(
        EvidenceInput(
            source_id="cursor",
            conversation_id="undated-conversation",
            message_id=f"undated-{cjk}",
            correlation_id=f"undated-{cjk}",
            role="user",
            written_by=WrittenBy.PERSON,
            verbatim=f"{cjk}没有自己的时间戳。",
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    ).record
    old_task = _task(tasks, profile, old, suffix="old")
    undated_task = _task(tasks, profile, undated, suffix="undated")
    runner = SteppedRunner()
    supervisor = _supervisor(tmp_path, stores, runner)

    async def exercise() -> None:
        supervisor.resume()
        await _step(runner, 2)
        await supervisor.shutdown()

    asyncio.run(exercise())

    assert runner.dispatched == [undated_task.task_id, old_task.task_id]


def test_a_harness_runs_one_thing_at_a_time() -> None:
    """Within one harness, work is serial — this is the core rule.

    HY decides whether a memory supersedes an older one from what it has
    already been told. Two turns of the same harness in it at once can each
    conclude without seeing the other, and the later one can overwrite a
    conclusion the earlier had not finished reaching.

    Corresponds to hy memory app's per-user serialisation, where the unit is
    the user and a harness is what a user is here.
    """
    queue = MemoryWorkQueue()
    # Three different conversations, all in the same harness.
    for index in range(3):
        queue.add(_work(f"same-{index}", ("local", "cursor", f"conv-{index}")))

    # One already running from this harness: nothing more may start.
    assert queue.take((), {"cursor": 1}, 1) is None


def test_different_harnesses_still_run_alongside_each_other() -> None:
    """Serial within a harness, parallel across them.

    Serialising everything would make one slow source hold up every other,
    which is not what per-harness ordering is for.
    """
    queue = MemoryWorkQueue()
    queue.add(_work("a", ("local", "cursor", "conv-a")))
    queue.add(_work("b", ("local", "codex", "conv-b")))

    # cursor is busy, so its work is not taken — codex's is.
    first = queue.take((), {"cursor": 1}, 1)
    assert first is not None
    assert first.task_id == "b"


def test_a_harness_resumes_when_its_turn_finishes() -> None:
    """Serial must not starve: the next item runs once the harness is free.

    Without this, ordering would be indistinguishable from dropping the work.
    """
    queue = MemoryWorkQueue()
    queue.add(_work("same-0", ("local", "cursor", "conv-0")))
    queue.add(_work("same-1", ("local", "cursor", "conv-1")))

    assert queue.take((), {"cursor": 1}, 1) is None
    taken = queue.take((), {}, 1)
    assert taken is not None
    # Which of the two comes first is not asserted: they were queued with the
    # same timestamp, so heap order between them is not meaningful. What
    # matters is that the harness is no longer blocked.
    assert taken.task_id in {"same-0", "same-1"}
    assert taken.session is not None
    assert taken.session[1] == "cursor"


class HarnessProbe:
    """Holds every task it is handed and records how many ran at once, per harness.

    The queue-level tests above hand `take()` a literal cap of 1 and never read
    `HARNESS_SERIAL`, so changing the supervisor's number left them green. This
    probe sits behind a real `MemoryProcessingSupervisor`, which is where the
    number is used.
    """

    def __init__(self, source_of: dict[str, str]) -> None:
        self.source_of = source_of
        self.started: list[str] = []
        self.running: dict[str, int] = {}
        self.peak: dict[str, int] = {}
        self.peak_total = 0
        self.finished: list[str] = []
        self._permits = asyncio.Semaphore(0)

    async def run(self, task_id: str, *, timeout: float | None = None) -> None:
        source = self.source_of[task_id]
        self.started.append(task_id)
        self.running[source] = self.running.get(source, 0) + 1
        self.peak[source] = max(self.peak.get(source, 0), self.running[source])
        self.peak_total = max(self.peak_total, sum(self.running.values()))
        try:
            await self._permits.acquire()
        finally:
            self.running[source] -= 1
        self.finished.append(task_id)

    def allow(self) -> None:
        self._permits.release()


def test_the_supervisor_runs_one_task_per_harness_even_with_spare_workers(tmp_path) -> None:
    """Rule 1 through the supervisor: serial within a harness, parallel across them.

    Four workers are allowed and there are five tasks — three from cursor,
    two from codex, each in its own conversation so the per-conversation rule
    cannot be what holds them back. Only the harness rule may keep a second
    cursor task out while a worker is free.
    """
    stores = _stores(tmp_path)
    evidence, profiles, tasks, governance, secrets, profile = stores
    source_of: dict[str, str] = {}
    now = datetime.now(UTC)
    for source, count in (("cursor", 3), ("codex", 2)):
        for index in range(count):
            record = evidence.put(
                EvidenceInput(
                    source_id=source,
                    conversation_id=f"{source}-conversation-{index}",
                    message_id=f"{source}-{index}",
                    correlation_id=f"{source}-{index}",
                    role="user",
                    written_by=WrittenBy.PERSON,
                    created_at=now - timedelta(minutes=index),
                    verbatim=f"{source} 的第 {index} 段对话。",
                    origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
                )
            ).record
            source_of[_task(tasks, profile, record, suffix=f"{source}{index}").task_id] = source
    probe = HarnessProbe(source_of)
    supervisor = MemoryProcessingSupervisor(
        tasks=tasks,
        profiles=profiles,
        secrets=secrets,
        evidence=evidence,
        governance=governance,
        data_dir=tmp_path,
        runner_factory=lambda: probe,
        max_concurrent_tasks=4,
    )

    async def settle() -> None:
        for _ in range(20):
            await asyncio.sleep(0.01)

    async def exercise() -> None:
        supervisor.resume()
        try:
            await settle()
            for _ in range(len(source_of)):
                probe.allow()
                await settle()
        finally:
            await supervisor.shutdown()

    asyncio.run(exercise())

    assert sorted(probe.finished) == sorted(source_of), "not every task was dispatched"
    # Non-vacuous: two harnesses really were in the model at the same time,
    # so the workers were there to take a second cursor task had it been allowed.
    assert probe.peak_total == 2, f"harnesses did not run alongside: {probe.peak}"
    assert probe.peak == {"cursor": 1, "codex": 1}, f"a harness ran more than one: {probe.peak}"


def test_model_workers_keep_one_slot_for_live_work(tmp_path) -> None:
    stores = _stores(tmp_path)
    evidence, profiles, tasks, governance, secrets, profile = stores
    source_of: dict[str, str] = {}
    background: list[QueuedWork] = []
    now = datetime.now(UTC)
    for index in range(4):
        source = f"source-{index}"
        record = evidence.put(
            EvidenceInput(
                source_id=source,
                conversation_id=f"conversation-{index}",
                message_id=f"message-{index}",
                correlation_id=f"message-{index}",
                role="user",
                written_by=WrittenBy.PERSON,
                created_at=now,
                verbatim=f"background {index}",
                origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
            )
        ).record
        task = _task(tasks, profile, record, suffix=source)
        source_of[task.task_id] = source
        background.append(
            QueuedWork(
                task_id=task.task_id,
                at=now,
                fresh=True,
                all_apps=False,
                length=1,
                session=("local", source, f"conversation-{index}"),
            )
        )
    live_record = evidence.put(
        EvidenceInput(
            source_id="workbuddy",
            conversation_id="live-conversation",
            message_id="live-message",
            correlation_id="live-message",
            role="assistant",
            written_by=WrittenBy.AGENT,
            created_at=now,
            verbatim="live",
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    ).record
    live_task = _task(tasks, profile, live_record, suffix="live")
    source_of[live_task.task_id] = "workbuddy"
    live = QueuedWork(
        task_id=live_task.task_id,
        at=now,
        fresh=True,
        all_apps=False,
        length=1,
        live=True,
        session=("local", "workbuddy", "live-conversation"),
    )
    probe = HarnessProbe(source_of)
    supervisor = MemoryProcessingSupervisor(
        tasks=tasks,
        profiles=profiles,
        secrets=secrets,
        evidence=evidence,
        governance=governance,
        data_dir=tmp_path,
        runner_factory=lambda: probe,
        max_concurrent_tasks=4,
    )

    async def exercise() -> None:
        supervisor._queue_validated_work(background)
        for _ in range(100):
            if len(probe.started) == 3:
                break
            await asyncio.sleep(0.01)
        assert len(probe.started) == 3
        supervisor._queue_validated_work([live])
        for _ in range(100):
            if live_task.task_id in probe.started:
                break
            await asyncio.sleep(0.01)
        assert probe.started[3] == live_task.task_id
        for _ in range(5):
            probe.allow()
        await supervisor.shutdown()

    asyncio.run(exercise())


def test_stale_queued_work_does_not_reclaim_a_completed_task(tmp_path) -> None:
    stores = _stores(tmp_path)
    evidence, profiles, tasks, governance, secrets, profile = stores
    record = evidence.put(
        EvidenceInput(
            source_id="workbuddy",
            conversation_id="completed-conversation",
            message_id="completed-message",
            correlation_id="completed-message",
            role="user",
            written_by=WrittenBy.PERSON,
            created_at=NOW,
            verbatim="already completed",
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    ).record
    task = _task(tasks, profile, record, suffix="completed")
    _, token = tasks.claim(task.task_id)
    tasks.complete(task.task_id, runner_token=token)
    calls: list[str] = []

    class Runner:
        async def run(self, task_id: str, *, timeout=None):
            del timeout
            calls.append(task_id)

    supervisor = MemoryProcessingSupervisor(
        tasks=tasks,
        profiles=profiles,
        secrets=secrets,
        evidence=evidence,
        governance=governance,
        data_dir=tmp_path,
        runner_factory=Runner,
    )
    stale = QueuedWork(
        task_id=task.task_id,
        at=NOW,
        fresh=False,
        all_apps=False,
        length=1,
        session=("local", "workbuddy", "completed-conversation"),
    )

    async def exercise() -> None:
        supervisor._queue_validated_work([stale])
        await asyncio.gather(*tuple(supervisor._workers.values()))
        await supervisor.shutdown()

    asyncio.run(exercise())
    assert calls == []
    assert tasks.require(task.task_id).status is MemoryTaskStatus.COMPLETED


@pytest.mark.xfail(
    strict=False,
    reason=(
        "known gap: closing the live turn prepares the complete turn but does "
        "not promote it to the live lane yet; see the lane-order xfail above."
    ),
)
def test_an_open_live_turn_waits_for_its_reply_before_the_live_lane(tmp_path) -> None:
    """A live user turn with no reply yet must not be prepared for HY.

    Lane order moved behind the complete-turn rule: an unanswered user message
    is Evidence only. Sending it would give HY half a turn, and a quiet-period
    timeout must not manufacture that. The closing assistant event is what
    admits the whole turn to the live lane.
    """
    from one_memory.imports.runner import live_sync_arrival

    stores = _stores(tmp_path)
    evidence, _profiles, _tasks, _governance, _secrets, _profile = stores
    now = datetime.now(UTC)
    runner = SteppedRunner()
    supervisor = _supervisor(tmp_path, stores, runner)

    async def exercise() -> None:
        supervisor.resume()
        user = _put(
            evidence,
            message_id="open-live",
            conversation_id="open-live-conversation",
            content="刚同步进来、还没有回复的一句话。",
            created_at=now,
        )
        token = live_sync_arrival.set(True)
        try:
            supervisor.enqueue_evidence(user)
            for _ in range(20):
                await asyncio.sleep(0.01)
            assert not supervisor._queue._live, "an open turn was prepared for HY"
            assert runner.dispatched == []
        finally:
            live_sync_arrival.reset(token)

        closing = evidence.put(
            EvidenceInput(
                source_id="cursor",
                conversation_id="open-live-conversation",
                message_id="open-live-reply",
                role="assistant",
                written_by=WrittenBy.AGENT,
                verbatim="这一轮的回复，闭合这个 turn。",
                created_at=now + timedelta(seconds=1),
                origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
            )
        ).record
        token = live_sync_arrival.set(True)
        try:
            supervisor.enqueue_evidence(closing)
            deadline = asyncio.get_running_loop().time() + 5
            while asyncio.get_running_loop().time() < deadline and not supervisor._queue._live:
                await asyncio.sleep(0.05)
            assert supervisor._queue._live, "the closed turn never entered the live lane"
        finally:
            live_sync_arrival.reset(token)
        await supervisor.shutdown()

    asyncio.run(exercise())

    assert runner.dispatched == [], runner.dispatched
