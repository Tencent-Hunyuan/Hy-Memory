"""The memory mode (lite / pro / ultra), evolve and its nightly schedule.

Item 5: the mode is a saved setting (default pro, `ONE_MEMORY_GRAPH_STORE`
still forces ultra), it decides the mode every SDK client is built with, and
a change rebuilds the handles. lite needs no generation model, never calls
`add_extracted`, and keeps every raw. Item 6 (P1-3 / R7): `POST
/api/v2/evolve` is ultra only, one run per user at a time, never beside that
user's write, and loops while the SDK says `should_continue`; the schedule
runs nightly and catches up after sleep. R10: the status carries
`pipeline.mode` and the last evolve.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from one_memory import hy_sdk_runtime, sdk_write_processor
from one_memory.api import _sdk_client_handle, create_app, memory_mode_effects
from one_memory.config import Settings
from one_memory.hy_graph_admin import effective_memory_mode
from one_memory.imports.models import (
    EvidenceContentKind,
    EvidenceInput,
    EvidenceOrigin,
    OriginMetadata,
    WrittenBy,
    materialize_write_input,
)
from one_memory.memory_evolution import (
    EvolveModeError,
    EvolveScheduler,
    EvolveService,
    EvolveStateStore,
    GraphBuildState,
    GraphBuildUser,
    MemoryGraphBackfill,
    evolve_due,
)
from one_memory.memory_server.config_state import ConfigStore, EvolveSchedule
from one_memory.sdk_memory_runtime import SdkMemoryRuntime
from one_memory.secrets import InMemorySecretStore

TOKEN = "memory-mode-test"


# --- the setting and the client mode -----------------------------------------


def test_mode_defaults_to_pro_and_the_env_still_forces_ultra(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("ONE_MEMORY_GRAPH_STORE", raising=False)
    store = ConfigStore(tmp_path)
    assert store.memory_mode() == "pro"
    assert store.memory_mode_explicitly_configured() is False
    assert effective_memory_mode(store.memory_mode()) == "pro"
    store.set_memory_mode("lite")
    assert store.memory_mode_explicitly_configured() is True
    assert ConfigStore(tmp_path).memory_mode() == "lite"
    assert json.loads((tmp_path / "config.json").read_text())["memory_mode"] == "lite"
    monkeypatch.setenv("ONE_MEMORY_GRAPH_STORE", "kuzu")
    assert effective_memory_mode("lite") == "ultra"
    monkeypatch.setenv("ONE_MEMORY_GRAPH_STORE", "none")
    assert effective_memory_mode("lite") == "lite"
    with pytest.raises(ValueError):
        store.set_memory_mode("max")  # type: ignore[arg-type]


def test_old_config_defaults_to_implicit_pro_until_pro_is_explicitly_set(tmp_path) -> None:
    (tmp_path / "config.json").write_text(
        json.dumps({"schema_version": 1, "sources": {}}), encoding="utf-8"
    )
    store = ConfigStore(tmp_path)
    assert store.memory_mode() == "pro"
    assert store.memory_mode_explicitly_configured() is False

    store.set_memory_mode("pro")

    assert store.memory_mode_explicitly_configured() is True
    assert json.loads((tmp_path / "config.json").read_text())["memory_mode"] == "pro"
    assert ConfigStore(tmp_path).memory_mode_explicitly_configured() is True


def test_an_untouched_install_writes_no_new_settings(tmp_path) -> None:
    store = ConfigStore(tmp_path)
    store.set_sync("cursor", True)
    saved = json.loads((tmp_path / "config.json").read_text())
    assert "memory_mode" not in saved and "evolve_schedule" not in saved


def test_acquire_client_builds_the_sdk_client_in_the_given_mode(tmp_path, monkeypatch) -> None:
    built: list[str] = []

    class Client:
        def __init__(self, *, config, mode, runtime, embedding_service):
            built.append(mode)

        def close(self):
            return None

    monkeypatch.setattr(hy_sdk_runtime, "HyMemoryClient", Client)
    monkeypatch.setattr(hy_sdk_runtime, "_acquire_runtime", lambda config, key: object())
    monkeypatch.setattr(hy_sdk_runtime, "_release_runtime", lambda key: None)
    monkeypatch.setattr(hy_sdk_runtime, "inspect_graph_width_mismatch", lambda config: None)
    monkeypatch.delenv("ONE_MEMORY_GRAPH_STORE", raising=False)
    profile = hy_sdk_runtime.builtin_retrieval_snapshot()
    for mode in ("lite", "pro", "ultra"):
        hy_sdk_runtime.acquire_client(profile, api_key=None, data_dir=tmp_path, mode=mode).close()
    hy_sdk_runtime.acquire_client(profile, api_key=None, data_dir=tmp_path).close()
    monkeypatch.setenv("ONE_MEMORY_GRAPH_STORE", "kuzu")
    hy_sdk_runtime.acquire_client(profile, api_key=None, data_dir=tmp_path).close()
    assert built == ["lite", "pro", "ultra", "pro", "ultra"]


def test_the_read_handle_rebuilds_on_a_mode_or_profile_change(monkeypatch, tmp_path) -> None:
    """Reads follow the active profile's embedding (#255) and the memory mode."""
    from one_memory import sdk_read_handle

    modes = ["pro"]
    made: list[_FakeHandle] = []

    def handle_for(profile, *, api_key, data_dir, mode=None, features=()):
        handle = _FakeHandle(mode or "pro", profile.profile_id)
        made.append(handle)
        return handle

    monkeypatch.setattr(sdk_read_handle, "SdkClientHandle", handle_for)
    profiles = _Profiles(identity=None)
    reads = sdk_read_handle.ActiveProfileReadHandle(
        profiles=profiles,  # type: ignore[arg-type]
        secrets=None,  # type: ignore[arg-type]
        data_dir=tmp_path,
        memory_mode=lambda: modes[0],
    )

    async def read():
        return await reads.client()

    async def scenario():
        first = await asyncio.create_task(read())
        assert (first.mode, first.profile_id) == ("pro", "builtin-retrieval")
        modes[0] = "lite"
        # A read still in flight on the old client keeps it open.
        hold = asyncio.Event()
        holding_client = asyncio.Event()

        async def holding():
            await reads.client()
            holding_client.set()
            await hold.wait()

        modes[0] = "pro"
        holder = asyncio.create_task(holding())
        await asyncio.wait_for(holding_client.wait(), 5)
        modes[0] = "lite"
        try:
            assert (await asyncio.create_task(read())).mode == "lite"
            assert not made[0].closed
        finally:
            hold.set()
            await holder
        for _ in range(10):
            await asyncio.sleep(0)
        assert made[0].closed
        assert [item.mode for item in made] == ["pro", "lite"]
        await reads.close()
        assert made[1].closed

    asyncio.run(scenario())


def test_read_handle_can_bind_one_caller_mode_snapshot(monkeypatch, tmp_path) -> None:
    from one_memory import sdk_read_handle

    callback_reads = 0

    def current_mode() -> str:
        nonlocal callback_reads
        callback_reads += 1
        return "pro"

    monkeypatch.setattr(
        sdk_read_handle,
        "SdkClientHandle",
        lambda profile, **kwargs: _FakeHandle(kwargs.get("mode") or "pro", profile.profile_id),
    )
    reads = sdk_read_handle.ActiveProfileReadHandle(
        profiles=_Profiles(identity=None),  # type: ignore[arg-type]
        secrets=None,  # type: ignore[arg-type]
        data_dir=tmp_path,
        memory_mode=current_mode,
    )

    async def scenario():
        client = await reads.client_for_mode("lite")
        assert client.mode == "lite"
        await reads.close()

    asyncio.run(scenario())
    assert callback_reads == 0


class _FakeHandle:
    def __init__(self, mode: str, profile_id: str = "p") -> None:
        self.mode = mode
        self.profile_id = profile_id
        self.closed = False

    async def client(self):
        return self

    async def close(self):
        self.closed = True


class _Profiles:
    def __init__(self, identity=("profile-a", 1)) -> None:
        self.identity = identity

    def active_identity(self):
        return self.identity

    def snapshot(self, profile_id, revision):
        return SimpleNamespace(profile_id=profile_id, has_api_key=False)

    def resolve_snapshot_secret(self, profile_id, revision, secrets):
        return None


def _coordinator(monkeypatch, modes, profiles=None):
    opened: list[_FakeHandle] = []

    def handle(profile, **kwargs):
        value = _FakeHandle(kwargs.get("mode") or "pro", profile.profile_id)
        opened.append(value)
        return value

    monkeypatch.setattr(sdk_write_processor, "SdkClientHandle", handle)
    coordinator = sdk_write_processor.SdkWriteCoordinator(
        profiles=profiles or _Profiles(),  # type: ignore[arg-type]
        secrets=None,  # type: ignore[arg-type]
        data_dir=None,
        transport_factory=lambda _profile: None,
        sedimentation_enabled=lambda: True,
        memory_mode=lambda: modes[0],
    )
    return coordinator, opened


def test_a_mode_change_retires_idle_write_handles_and_keeps_a_busy_one(monkeypatch) -> None:
    modes = ["pro"]
    coordinator, opened = _coordinator(monkeypatch, modes)

    async def scenario():
        async with coordinator.use_client() as client:
            assert client.mode == "pro"
            modes[0] = "ultra"
            await coordinator.retire_stale_modes()
            # Still in use: closed only when this use ends (#218).
            assert not opened[0].closed
        assert opened[0].closed
        async with coordinator.use_client() as client:
            assert client.mode == "ultra"
        modes[0] = "lite"
        await coordinator.retire_stale_modes()
        assert opened[1].closed
        await coordinator.close()

    asyncio.run(scenario())


def test_lite_needs_no_generation_model(monkeypatch) -> None:
    modes = ["lite"]
    coordinator, opened = _coordinator(monkeypatch, modes, _Profiles(identity=None))

    async def scenario():
        async with coordinator.use_client() as client:
            assert client.mode == "lite"
            assert client.profile_id == hy_sdk_runtime.BUILTIN_RETRIEVAL_PROFILE_ID
        modes[0] = "pro"
        with pytest.raises(RuntimeError, match="model_profile_not_active"):
            async with coordinator.use_client():
                pass
        await coordinator.close()

    asyncio.run(scenario())


def _record(text: str, *, extracted: bool, user_id: str = "local", role: str = "user"):
    record = materialize_write_input(
        EvidenceInput(
            user_id=user_id,
            source_id="one-memory-manual" if extracted else "hermes",
            conversation_id="session-1",
            message_id=str(uuid4()),
            role=role,
            written_by=WrittenBy.PERSON,
            created_at=datetime(2026, 10, 1, tzinfo=UTC),
            verbatim=text,
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    )
    if extracted:
        record = record.model_copy(update={"content_kind": EvidenceContentKind.MEMORY_CONTENT})
    return record


class _WritingSdk:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def add(self, messages, **kwargs):
        self.calls.append(("add", messages))
        return {"success": True, "raw_ids": ["raw-1"]}

    def add_extracted(self, contents, **kwargs):
        self.calls.append(("add_extracted", contents))
        return {"success": True, "memory_ids": ["m-1"]}


class _SdkHandle:
    def __init__(self, sdk, mode="pro") -> None:
        self.sdk = sdk
        self.mode = mode

    async def client(self):
        return self.sdk

    async def close(self):
        return None


async def _noop(_requires_generation: bool) -> None:
    return None


def test_a_note_in_lite_is_stored_as_raw_and_never_calls_add_extracted() -> None:
    sdk = _WritingSdk()
    note = (_record("I prefer green tea", extracted=True),)

    async def scenario():
        await sdk_write_processor.SdkWriteProcessor(_SdkHandle(sdk), lite=True).process_sdk_job(
            note, request_id="r-lite", before_hy_invocation=_noop
        )
        await sdk_write_processor.SdkWriteProcessor(_SdkHandle(sdk)).process_sdk_job(
            note, request_id="r-pro", before_hy_invocation=_noop
        )

    asyncio.run(scenario())
    assert sdk.calls == [
        ("add", [{"role": "user", "content": "I prefer green tea"}]),
        ("add_extracted", ["I prefer green tea"]),
    ]


def test_a_lite_write_ignores_the_model_sedimentation_pause(monkeypatch) -> None:
    sdk = _WritingSdk()
    coordinator = sdk_write_processor.SdkWriteCoordinator(
        profiles=_Profiles(identity=None),  # type: ignore[arg-type]
        secrets=None,  # type: ignore[arg-type]
        data_dir=None,
        transport_factory=lambda _profile: None,
        sedimentation_enabled=lambda: False,
        memory_mode=lambda: "lite",
    )
    monkeypatch.setattr(
        sdk_write_processor, "SdkClientHandle", lambda profile, **kwargs: _SdkHandle(sdk, "lite")
    )
    turn = (
        _record("hello", extracted=False),
        _record("hi there", extracted=False, role="assistant"),
    )
    receipt = asyncio.run(
        coordinator.process_sdk_job(turn, request_id="r-1", before_hy_invocation=_noop)
    )
    assert receipt.raw_ids == ("raw-1",)
    assert [name for name, _ in sdk.calls] == ["add"]


def test_a_lite_write_ignores_the_model_sedimentation_switch(monkeypatch) -> None:
    sdk = _WritingSdk()
    coordinator = sdk_write_processor.SdkWriteCoordinator(
        profiles=_Profiles(identity=None),  # type: ignore[arg-type]
        secrets=None,  # type: ignore[arg-type]
        data_dir=None,
        transport_factory=lambda _profile: None,
        sedimentation_enabled=lambda: False,
        memory_mode=lambda: "lite",
    )
    monkeypatch.setattr(
        sdk_write_processor, "SdkClientHandle", lambda profile, **kwargs: _SdkHandle(sdk, "lite")
    )
    admitted = 0

    async def admit(_requires_generation: bool) -> None:
        nonlocal admitted
        admitted += 1

    receipt = asyncio.run(
        coordinator.process_sdk_job(
            (_record("still stored as raw", extracted=False),),
            request_id="r-off-lite",
            before_hy_invocation=admit,
        )
    )
    assert receipt.raw_ids == ("raw-1",)
    assert admitted == 1
    assert [name for name, _ in sdk.calls] == ["add"]


def test_no_raw_is_ever_deleted_in_any_mode() -> None:
    """Owner decision: MemHub keeps every raw; there is no retention window."""
    from one_memory import sdk_memory_runtime

    for name in ("prune_expired_raw", "start_raw_retention", "_raw_retention_loop"):
        assert not hasattr(SdkMemoryRuntime, name), name
    assert not hasattr(sdk_memory_runtime, "RAW_RETENTION_DAYS")


def test_mode_switch_effects() -> None:
    assert memory_mode_effects("lite", "pro") == (
        "earlier_raw_not_extracted",
        "lite_search_stops",
    )
    assert memory_mode_effects("ultra", "pro") == ("higher_mode_memories_hidden", "evolve_stopped")
    assert memory_mode_effects("pro", "ultra", graph_backfill=True) == (
        "existing_memories_added_to_graph",
    )
    assert memory_mode_effects("pro", "ultra", graph_backfill=False) == (
        "graph_backfill_already_running",
    )
    assert memory_mode_effects("pro", "lite") == ("higher_mode_memories_hidden",)
    assert memory_mode_effects("pro", "pro") == ()


# --- evolve ---------------------------------------------------------------------


class _EvolveSdk:
    """`evolve` answers `should_continue` for the first rounds, then stops."""

    def __init__(self, continuing: int = 2, *, gate: asyncio.Event | None = None) -> None:
        self.calls: list[dict] = []
        self.continuing = continuing
        self.gate = gate
        self.graph_builds: list[str] = []
        self.active = 0
        self.peak = 0

    def evolve(self, user_id, agent_id="default_agent", max_batches=None):
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            self.calls.append(
                {"user_id": user_id, "agent_id": agent_id, "max_batches": max_batches}
            )
            more = len([call for call in self.calls if call["user_id"] == user_id]) <= (
                self.continuing
            )
            return {
                "success": True,
                "request_id": f"evolve-{len(self.calls)}",
                "tasks_processed": 2,
                "results": {"schemas": ["private text"]},
                "cross_domain_sweeper": {"merged": 1},
                "should_continue": more,
                "continuation_reason": "remaining_clusters" if more else None,
                "remaining_clusters": 3 if more else 0,
                "remaining_actionable_clusters": 1 if more else 0,
                "remaining_fresh_facts": 0,
                "cycle_status": "partial" if more else "complete",
                "batch_failures": [],
                "elapsed_ms": 5.0,
            }
        finally:
            self.active -= 1

    def build_memory_graph(self, user_id, *, agent_id=None, limit=10000, clear_first=False):
        self.graph_builds.append(user_id)
        return {"success": True, "memories_scanned": 4, "nodes": 4}


class _Coordinator:
    """The real per-user exclusivity over a stand-in client."""

    def __init__(self, sdk, monkeypatch) -> None:
        self.inner, _ = _coordinator(monkeypatch, ["ultra"])
        self.sdk = sdk

    def exclusive_for_evolve(self, user_id):
        return self.inner.exclusive_for_evolve(user_id)

    @asynccontextmanager
    async def use_client(self):
        yield self.sdk


def test_evolve_refuses_outside_ultra(tmp_path, monkeypatch) -> None:
    service = EvolveService(
        coordinator=_Coordinator(_EvolveSdk(), monkeypatch),
        state=EvolveStateStore(tmp_path),
        memory_mode=lambda: "pro",
    )

    async def scenario():
        with pytest.raises(EvolveModeError):
            service.start("local")

    asyncio.run(scenario())


def test_evolve_loops_while_should_continue_and_records_counts_only(tmp_path, monkeypatch) -> None:
    sdk = _EvolveSdk(continuing=2)
    state = EvolveStateStore(tmp_path)
    service = EvolveService(
        coordinator=_Coordinator(sdk, monkeypatch), state=state, memory_mode=lambda: "ultra"
    )

    async def scenario():
        started = service.start("wife", max_batches=2)
        assert started.state == "running"
        done = await service.wait(started.run_id)
        assert done is not None
        return done

    done = asyncio.run(scenario())
    assert done.state == "succeeded" and done.rounds == 3 and done.tasks_processed == 6
    assert done.cycle_status == "complete" and done.should_continue is False
    assert {call["agent_id"] for call in sdk.calls} == {"all-apps"}
    assert {call["max_batches"] for call in sdk.calls} == {2}
    saved = (tmp_path / "evolve-state.json").read_text()
    assert "private text" not in saved
    assert json.loads(saved)["users"]["wife"]["last_result"]["rounds"] == 3


def test_evolve_stops_at_the_round_cap(tmp_path, monkeypatch) -> None:
    sdk = _EvolveSdk(continuing=100)
    service = EvolveService(
        coordinator=_Coordinator(sdk, monkeypatch),
        state=EvolveStateStore(tmp_path),
        memory_mode=lambda: "ultra",
        max_rounds=4,
    )

    async def scenario():
        return await service.wait(service.start("local").run_id)

    done = asyncio.run(scenario())
    assert done is not None and done.rounds == 4 and done.capped is True
    assert done.state == "succeeded"


def test_one_run_per_user_and_never_beside_that_users_write(tmp_path, monkeypatch) -> None:
    sdk = _EvolveSdk(continuing=0)
    coordinator = _Coordinator(sdk, monkeypatch)
    service = EvolveService(
        coordinator=coordinator, state=EvolveStateStore(tmp_path), memory_mode=lambda: "ultra"
    )
    writing = asyncio.Event()
    release = asyncio.Event()

    async def fake_write(self, records, *, request_id, before_hy_invocation):
        writing.set()
        await release.wait()
        return sdk_write_processor.SdkWriteReceipt()

    monkeypatch.setattr(sdk_write_processor.SdkWriteProcessor, "process_sdk_job", fake_write)
    record = (_record("hello", extracted=False, user_id="wife"),)

    async def scenario():
        inner = coordinator.inner
        write = asyncio.create_task(
            inner.process_sdk_job(record, request_id="w", before_hy_invocation=_noop)
        )
        await writing.wait()
        first = service.start("wife")
        again = service.start("wife")
        assert again.run_id == first.run_id
        other = service.start("local")
        await service.wait(other.run_id)
        # The other user is not held; wife's run waits for wife's write.
        assert sdk.calls and {call["user_id"] for call in sdk.calls} == {"local"}
        assert inner.evolving("wife")
        with pytest.raises(RuntimeError, match="memory_evolve_in_progress"):
            await inner.process_sdk_job(record, request_id="w2", before_hy_invocation=_noop)
        release.set()
        await write
        done = await service.wait(first.run_id)
        assert done is not None and done.state == "succeeded"
        assert not inner.evolving("wife")
        assert sdk.peak == 1

    asyncio.run(scenario())


def test_a_failed_round_records_its_code(tmp_path, monkeypatch) -> None:
    class Failing(_EvolveSdk):
        def evolve(self, user_id, agent_id="default_agent", max_batches=None):
            return {
                "success": False,
                "error_code": "schema_llm_failed",
                "failure_kind": "llm_error",
                "error_message": "the provider said something private",
            }

    state = EvolveStateStore(tmp_path)
    service = EvolveService(
        coordinator=_Coordinator(Failing(), monkeypatch), state=state, memory_mode=lambda: "ultra"
    )
    done = asyncio.run(service.wait(_start(service, "local")))
    assert done is not None and done.state == "failed"
    assert done.error_code == "schema_llm_failed" and done.failure_kind == "llm_error"
    assert state.user("local").last_error == "schema_llm_failed"
    assert "private" not in (tmp_path / "evolve-state.json").read_text()


def _start(service: EvolveService, user_id: str) -> str:
    async def begin() -> str:
        return service.start(user_id).run_id

    loop = asyncio.new_event_loop()
    try:
        run_id = loop.run_until_complete(begin())
        loop.run_until_complete(service.wait(run_id))
    finally:
        loop.close()
    return run_id


# --- the nightly schedule -------------------------------------------------------

LOCAL = datetime.now().astimezone().tzinfo


def _at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=LOCAL)


def test_evolve_is_due_after_the_slot_once_a_day_and_catches_up_after_sleep() -> None:
    schedule = EvolveSchedule()
    anchor = _at(1, 12)
    # Never ran: not at noon on the day ultra was enabled, then at 03:00 next day.
    assert not evolve_due(_at(1, 13), schedule, None, anchor)
    assert not evolve_due(_at(2, 2, 59), schedule, None, anchor)
    assert evolve_due(_at(2, 3, 0), schedule, None, anchor)
    # Ran at 03:00: not again that day, due again the next night.
    assert not evolve_due(_at(2, 22), schedule, _at(2, 3, 1), anchor)
    assert evolve_due(_at(3, 3, 5), schedule, _at(2, 3, 1), anchor)
    # Asleep at 03:00: a run older than the slot is due on wake, the same day.
    assert evolve_due(_at(3, 9), schedule, _at(2, 3, 1), anchor)
    # Last run more than 24h ago is due even before today's slot.
    assert evolve_due(_at(4, 1), schedule, _at(2, 3, 1), anchor)
    assert not evolve_due(_at(3, 1), schedule, _at(2, 3, 1), anchor)
    # Moved and turned off.
    assert evolve_due(_at(2, 23, 30), EvolveSchedule(time="23:30"), _at(2, 3), anchor)
    assert not evolve_due(_at(3, 9), EvolveSchedule(enabled=False), _at(1, 3), anchor)


def test_the_scheduler_runs_each_due_user_once_in_ultra_only(tmp_path, monkeypatch) -> None:
    sdk = _EvolveSdk(continuing=0)
    state = EvolveStateStore(tmp_path)
    modes = ["pro"]
    clock = [_at(1, 12)]
    service = EvolveService(
        coordinator=_Coordinator(sdk, monkeypatch),
        state=state,
        memory_mode=lambda: modes[0],
        now=lambda: clock[0],
    )
    schedule = [EvolveSchedule()]

    async def users():
        return ("local", "wife")

    scheduler = EvolveScheduler(
        service=service,
        schedule=lambda: schedule[0],
        users_with_memories=users,
        now=lambda: clock[0],
    )

    async def scenario():
        assert await scheduler.tick() == ()  # pro: nothing
        modes[0] = "ultra"
        assert await scheduler.tick() == ()  # anchored at noon
        clock[0] = _at(2, 3, 2)
        assert await scheduler.tick() == ("local", "wife")
        assert await scheduler.tick() == ()  # once a night
        clock[0] = _at(3, 8)  # slept through 03:00
        assert await scheduler.tick() == ("local", "wife")
        schedule[0] = EvolveSchedule(enabled=False)
        clock[0] = _at(5, 8)
        assert await scheduler.tick() == ()

    asyncio.run(scenario())
    assert [call["user_id"] for call in sdk.calls] == ["local", "wife", "local", "wife"]
    assert state.user("wife").last_result["trigger"] == "scheduled"


def test_scheduled_evolve_obeys_the_shared_lifecycle_gate(tmp_path, monkeypatch) -> None:
    sdk = _EvolveSdk(continuing=0)
    state = EvolveStateStore(tmp_path)
    state.anchor(_at(1, 12))
    open_gate = [False]
    service = EvolveService(
        coordinator=_Coordinator(sdk, monkeypatch),
        state=state,
        memory_mode=lambda: "ultra",
        now=lambda: _at(2, 3, 2),
    )

    async def users():
        return ("local",)

    async def scenario():
        scheduler = EvolveScheduler(
            service=service,
            schedule=EvolveSchedule,
            users_with_memories=users,
            lifecycle_lock=asyncio.Lock(),
            lifecycle_open=lambda: open_gate[0],
            now=lambda: _at(2, 3, 2),
        )
        assert await scheduler.tick() == ()
        assert sdk.calls == []
        open_gate[0] = True
        assert await scheduler.tick() == ("local",)

    asyncio.run(scenario())


def test_the_graph_backfill_runs_once_per_user(tmp_path, monkeypatch) -> None:
    sdk = _EvolveSdk()
    state = EvolveStateStore(tmp_path)
    backfill = MemoryGraphBackfill(coordinator=_Coordinator(sdk, monkeypatch), state=state)

    async def scenario():
        backfill.start(["local", "wife", "local"])
        await backfill.wait()

    asyncio.run(scenario())
    assert sdk.graph_builds == ["local", "wife"]
    built = state.graph_build()
    assert built.state == "succeeded"
    assert {user: value.state for user, value in built.users.items()} == {
        "local": "succeeded",
        "wife": "succeeded",
    }


def test_an_interrupted_graph_backfill_resumes_only_unfinished_users(tmp_path, monkeypatch) -> None:
    sdk = _EvolveSdk()
    state = EvolveStateStore(tmp_path)
    state.set_graph_build(
        GraphBuildState(
            state="running",
            started_at=_at(1, 3),
            users={
                "local": GraphBuildUser(state="succeeded", memories_scanned=4, nodes=4),
                "wife": GraphBuildUser(state="running"),
            },
        )
    )
    backfill = MemoryGraphBackfill(coordinator=_Coordinator(sdk, monkeypatch), state=state)

    async def scenario():
        resumed = backfill.recover(["local", "wife"])
        assert resumed.state == "running"
        assert resumed.users["local"].state == "succeeded"
        assert resumed.users["wife"].state == "pending"
        await backfill.wait()

    asyncio.run(scenario())
    assert sdk.graph_builds == ["wife"]
    assert state.graph_build().state == "succeeded"


def test_graph_backfill_waits_for_embedding_maintenance_to_finish(tmp_path, monkeypatch) -> None:
    sdk = _EvolveSdk()
    state = EvolveStateStore(tmp_path)
    gate = [False]
    checked = asyncio.Event()

    def lifecycle_open() -> bool:
        checked.set()
        return gate[0]

    backfill = MemoryGraphBackfill(
        coordinator=_Coordinator(sdk, monkeypatch),
        state=state,
        lifecycle_open=lifecycle_open,
    )

    async def scenario():
        backfill.start(["local"])
        await checked.wait()
        assert sdk.graph_builds == []
        assert state.graph_build().state == "running"
        assert state.graph_build().users["local"].state == "pending"
        gate[0] = True
        await backfill.wait()

    asyncio.run(scenario())
    assert sdk.graph_builds == ["local"]
    assert state.graph_build().state == "succeeded"


# --- the HTTP surface -----------------------------------------------------------


def application(tmp_path: Path):
    settings = Settings(
        data_dir=tmp_path / "data",
        source_home=tmp_path,
        api_token=SecretStr(TOKEN),
        imports_enabled=False,
        automatic_memory_processing=False,
        live_sync_enabled=False,
    )
    return create_app(settings, secret_store=InMemorySecretStore())


def client_for(app) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )


class _ReadSdk(_EvolveSdk):
    def memory_stats(self, *, user_id, statuses=None):
        return {"total": 1, "scenes": {}}


def run_app(tmp_path, monkeypatch, scenario) -> None:
    monkeypatch.delenv("ONE_MEMORY_GRAPH_STORE", raising=False)

    async def main() -> None:
        app = application(tmp_path)
        sdk = _ReadSdk(continuing=1)

        @asynccontextmanager
        async def write_client():
            yield sdk

        async with app.router.lifespan_context(app):
            monkeypatch.setattr(app.state.sdk_write_coordinator, "use_client", write_client)
            # Model writes wait for the startup scan of embedding spaces, as
            # they would behind the App's loading screen.
            for _ in range(500):
                if app.state.embedding_switch.writes_open():
                    break
                await asyncio.sleep(0.01)
            async with client_for(app) as client:
                await scenario(app, sdk, client)

    asyncio.run(main())


@pytest.mark.fresh_sdk
def test_mode_api_get_put_and_its_effects(tmp_path, monkeypatch) -> None:
    async def scenario(app, sdk, client):
        current = (await client.get("/api/v1/settings/memory-mode")).json()
        assert current["mode"] == current["configured_mode"] == "pro"
        assert current["explicitly_configured"] is False
        assert current["forced_by_env"] is False
        assert app.state.sdk_write_coordinator.memory_mode() == "pro"
        assert (
            await client.put("/api/v1/sedimentation", json={"enabled": False})
        ).status_code == 200
        from one_memory.api import _sedimentation_claim_enabled

        app.state.history_import_gate_released = True
        assert not _sedimentation_claim_enabled(app, "one-memory-manual")

        app.state.config_store.set_sedimentation_enabled(False)
        lite = await client.put("/api/v1/settings/memory-mode", json={"mode": "lite"})
        assert lite.status_code == 200, lite.text
        body = lite.json()
        assert body["mode"] == "lite" and body["previous_mode"] == "pro"
        assert body["explicitly_configured"] is True
        assert body["effects"] == ["higher_mode_memories_hidden"]
        resume = getattr(app.state, "sedimentation_resume", None)
        assert resume is not None
        await asyncio.wait_for(asyncio.shield(resume), 5)
        assert app.state.sdk_write_coordinator.memory_mode() == "lite"
        assert _sdk_client_handle(app).memory_mode() == "lite"
        # lite claims queued work without a generation model.
        assert app.state.model_profile_store.active_identity() is None
        assert _sedimentation_claim_enabled(app, "one-memory-manual")

        monkeypatch.setattr(
            app.state.model_profile_store,
            "active_identity",
            lambda: ("active-profile", 1),
        )
        ultra = await client.put("/api/v1/settings/memory-mode", json={"mode": "ultra"})
        assert ultra.status_code == 200, ultra.text
        assert "existing_memories_added_to_graph" in ultra.json()["effects"]
        await app.state.memory_graph_backfill.wait()
        assert sdk.graph_builds == ["local"]
        reread = (await client.get("/api/v1/settings/memory-mode")).json()
        assert reread["graph_build"]["state"] == "succeeded"

        for bad in ({"mode": "max"}, {"mode": "pro", "x": 1}):
            assert (await client.put("/api/v1/settings/memory-mode", json=bad)).status_code == 422
        assert (
            await client.get("/api/v1/settings/memory-mode", params={"user_id": "x"})
        ).status_code == 422

        monkeypatch.setenv("ONE_MEMORY_GRAPH_STORE", "kuzu")
        await client.put("/api/v1/settings/memory-mode", json={"mode": "pro"})
        forced = (await client.get("/api/v1/settings/memory-mode")).json()
        assert forced["mode"] == "ultra" and forced["configured_mode"] == "pro"
        assert forced["forced_by_env"] is True

    run_app(tmp_path, monkeypatch, scenario)


@pytest.mark.fresh_sdk
def test_mode_plan_and_put_share_validation_and_effects(tmp_path, monkeypatch) -> None:
    async def scenario(app, _sdk, client):
        plan = await client.post(
            "/api/v1/settings/memory-mode/plan",
            json={"mode": "lite"},
        )
        assert plan.status_code == 200
        body = plan.json()
        assert body == {
            "current": "pro",
            "target": "lite",
            "effective_target": "lite",
            "capabilities": {
                "content_plane": "raw",
                "generation_required": False,
                "graph_enabled": False,
                "evolve_available": False,
            },
            "effects": ["higher_mode_memories_hidden"],
            "warnings": [],
            "blockers": [],
        }
        changed = await client.put("/api/v1/settings/memory-mode", json={"mode": "lite"})
        assert changed.status_code == 200
        assert changed.json()["effects"] == body["effects"]

        missing_model = (
            await client.post(
                "/api/v1/settings/memory-mode/plan",
                json={"mode": "pro"},
            )
        ).json()
        assert missing_model["blockers"] == ["active_model_required"]
        assert missing_model["effects"] == []
        refused = await client.put("/api/v1/settings/memory-mode", json={"mode": "pro"})
        assert refused.status_code == 409
        assert refused.json()["detail"] == missing_model["blockers"][0]

        monkeypatch.setenv("ONE_MEMORY_GRAPH_STORE", "kuzu")
        forced = (
            await client.post(
                "/api/v1/settings/memory-mode/plan",
                json={"mode": "lite"},
            )
        ).json()
        assert forced["effective_target"] == "ultra"
        assert forced["warnings"] == ["forced_by_env", "effective_no_change"]

    run_app(tmp_path, monkeypatch, scenario)


@pytest.mark.fresh_sdk
def test_mode_plan_reports_all_blockers_without_planned_effects(tmp_path, monkeypatch) -> None:
    async def scenario(app, _sdk, client):
        changed = await client.put("/api/v1/settings/memory-mode", json={"mode": "lite"})
        assert changed.status_code == 200
        switch = app.state.embedding_switch
        switch._scan_hold = True
        hold = asyncio.Event()
        task = asyncio.create_task(hold.wait())
        app.state.memory_graph_backfill._task = task
        monkeypatch.setattr("one_memory.api.importlib.util.find_spec", lambda _name: None)
        try:
            plan = (
                await client.post(
                    "/api/v1/settings/memory-mode/plan",
                    json={"mode": "ultra"},
                )
            ).json()
            assert plan["blockers"] == [
                "embedding_maintenance_in_progress",
                "active_model_required",
                "memory_lifecycle_busy",
                "graph_store_unavailable",
            ]
            assert plan["effects"] == []
        finally:
            switch._scan_hold = False
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            app.state.memory_graph_backfill._task = None

    run_app(tmp_path, monkeypatch, scenario)


@pytest.mark.fresh_sdk
def test_lite_startup_readmits_jobs_while_model_sedimentation_is_off(tmp_path, monkeypatch) -> None:
    store = ConfigStore(tmp_path / "data")
    store.set_memory_mode("lite")
    store.set_sedimentation_enabled(False)
    resumed = asyncio.Event()
    original = SdkMemoryRuntime.resume_allowed

    async def resume_allowed(runtime):
        await original(runtime)
        resumed.set()

    monkeypatch.setattr(SdkMemoryRuntime, "resume_allowed", resume_allowed)

    async def scenario(app, _sdk, _client):
        await asyncio.wait_for(resumed.wait(), 5)
        assert app.state.config_store.sedimentation_enabled() is False
        assert app.state.config_store.memory_mode() == "lite"

    run_app(tmp_path, monkeypatch, scenario)


@pytest.mark.fresh_sdk
def test_embedding_maintenance_blocks_mode_put_and_evolve_but_not_mode_get(
    tmp_path, monkeypatch
) -> None:
    async def scenario(app, _sdk, client):
        switch = app.state.embedding_switch

        switch._scan_hold = True
        current = await client.get("/api/v1/settings/memory-mode")
        assert current.status_code == 200
        assert current.json()["explicitly_configured"] is False
        for response in (
            await client.put("/api/v1/settings/memory-mode", json={"mode": "pro"}),
            await client.post("/api/v2/evolve", json={}),
        ):
            assert response.status_code == 409
            assert response.json()["detail"] == "embedding_maintenance_in_progress"
        assert app.state.config_store.memory_mode_explicitly_configured() is False

        switch._scan_hold = False
        switch._open_cache = True
        assert (await client.get("/api/v1/settings/memory-mode")).status_code == 200
        for response in (
            await client.put("/api/v1/settings/memory-mode", json={"mode": "lite"}),
            await client.post("/api/v2/evolve", json={}),
        ):
            assert response.status_code == 409
            assert response.json()["detail"] == "embedding_maintenance_in_progress"

        switch._open_cache = False
        explicitly_pro = await client.put("/api/v1/settings/memory-mode", json={"mode": "pro"})
        assert explicitly_pro.status_code == 200
        assert explicitly_pro.json()["explicitly_configured"] is True
        assert explicitly_pro.json()["effects"] == []

    run_app(tmp_path, monkeypatch, scenario)


@pytest.mark.fresh_sdk
def test_graph_backfill_blocks_mode_change_and_manual_evolve(tmp_path, monkeypatch) -> None:
    async def scenario(app, _sdk, client):
        hold = asyncio.Event()
        task = asyncio.create_task(hold.wait())
        app.state.memory_graph_backfill._task = task
        try:
            for response in (
                await client.put("/api/v1/settings/memory-mode", json={"mode": "lite"}),
                await client.post("/api/v2/evolve", json={}),
            ):
                assert response.status_code == 409
                assert response.json()["detail"] == "memory_lifecycle_busy"
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            app.state.memory_graph_backfill._task = None

    run_app(tmp_path, monkeypatch, scenario)


@pytest.mark.fresh_sdk
def test_evolve_api_is_ultra_only_async_and_scoped_per_user(tmp_path, monkeypatch) -> None:
    async def scenario(app, sdk, client):
        refused = await client.post("/api/v2/evolve", json={})
        assert refused.status_code == 409
        assert refused.json()["detail"] == "memory_mode_not_ultra"

        monkeypatch.setattr(
            app.state.model_profile_store,
            "active_identity",
            lambda: ("active-profile", 1),
        )
        changed = await client.put("/api/v1/settings/memory-mode", json={"mode": "ultra"})
        assert changed.status_code == 200, changed.text
        await app.state.memory_graph_backfill.wait()
        started = await client.post(
            "/api/v2/evolve", json={"user_id": "wife", "agent_id": "all-apps", "max_batches": 2}
        )
        assert started.status_code == 202, started.text
        run = started.json()
        assert run["state"] == "running" and run["user_id"] == "wife"
        await app.state.evolve_service.wait(run["run_id"])
        path = f"/api/v2/evolve/{run['run_id']}"
        assert (await client.get(path)).status_code == 404
        done = (await client.get(path, params={"user_id": "wife"})).json()
        assert done["state"] == "succeeded" and done["rounds"] == 2
        assert sdk.calls[0] == {"user_id": "wife", "agent_id": "all-apps", "max_batches": 2}

        overview = (await client.get("/api/v2/evolve", params={"user_id": "wife"})).json()
        assert set(overview["users"]) == {"wife"}
        assert overview["schedule"] == {"enabled": True, "time": "03:00"}

        processing = (await client.get("/api/v1/memory-processing")).json()
        pipeline = processing["pipeline"]
        assert pipeline["mode"] == "ultra"
        assert pipeline["last_evolve_at"] is not None
        assert pipeline["evolve"]["users"]["wife"]["last_result"]["state"] == "succeeded"

        for bad in (
            {"user_id": "a:b"},
            {"max_batches": 0},
            {"from_beginning": True},
        ):
            assert (await client.post("/api/v2/evolve", json=bad)).status_code == 422

        moved = await client.put(
            "/api/v1/settings/evolve-schedule", json={"time": "04:30", "enabled": False}
        )
        assert moved.json() == {"enabled": False, "time": "04:30"}
        assert (
            await client.put("/api/v1/settings/evolve-schedule", json={"time": "25:00"})
        ).status_code == 422

    run_app(tmp_path, monkeypatch, scenario)


@pytest.mark.fresh_sdk
def test_a_job_written_in_lite_stays_done_and_a_pending_job_takes_the_new_mode(
    tmp_path, monkeypatch
) -> None:
    """lite to pro reprocesses nothing: the mode is read when a job runs."""
    from one_memory.memory_server.durable_queue import DurableWriteDrain, execute_job

    modes = ["lite"]
    seen: list[str] = []

    class Sdk:
        def __init__(self, mode):
            self.mode = mode

        def add(self, messages, **kwargs):
            seen.append(self.mode)
            return {"success": True, "raw_ids": [f"raw-{len(seen)}"]}

    monkeypatch.setattr(
        sdk_write_processor,
        "SdkClientHandle",
        lambda profile, **kwargs: _SdkHandle(Sdk(kwargs["mode"]), kwargs["mode"]),
    )
    coordinator = sdk_write_processor.SdkWriteCoordinator(
        profiles=_Profiles(),  # type: ignore[arg-type]
        secrets=None,  # type: ignore[arg-type]
        data_dir=None,
        transport_factory=lambda _profile: None,
        sedimentation_enabled=lambda: True,
        memory_mode=lambda: modes[0],
    )

    async def never(_job_id):
        return None

    async def drain_once(queue) -> int:
        drain = DurableWriteDrain(queue, execute=never, lease=timedelta(seconds=30))
        claimed = drain.claim(limit=8)
        for job_id in claimed:
            await execute_job(
                job_id,
                queue=queue,
                supervisor=coordinator,
                evidence=None,  # type: ignore[arg-type]
            )
        return len(claimed)

    async def scenario(app, _sdk, client):
        queue = app.state.memory_write_queue
        for request_id in ("turn-lite", "turn-pro"):
            written = await client.post(
                "/api/v2/memory-writes",
                json={
                    "request_id": request_id,
                    "session_id": f"s-{request_id}",
                    "source_id": "one-memory-manual",
                    "messages": [
                        {"role": "user", "content": request_id},
                        {"role": "assistant", "content": "ok"},
                    ],
                },
            )
            assert written.status_code == 202, written.text
            if request_id == "turn-lite":
                assert await drain_once(queue) == 1
                modes[0] = "pro"
                await coordinator.retire_stale_modes()
        assert await drain_once(queue) == 1
        assert await drain_once(queue) == 0
        assert queue.get("turn-lite").state == queue.get("turn-pro").state == "completed"
        assert seen == ["lite", "pro"]
        await coordinator.close()

    run_app(tmp_path, monkeypatch, scenario)


@pytest.mark.fresh_sdk
def test_paused_model_still_writes_a_lite_job_through_the_durable_boundary(
    tmp_path, monkeypatch
) -> None:
    from one_memory.api import _admit_pipeline_invocation
    from one_memory.memory_server.durable_queue import DurableWriteDrain, execute_job

    seen: list[str] = []

    class Sdk:
        mode = "lite"

        def add(self, messages, **kwargs):
            seen.append("add")
            return {"success": True, "raw_ids": ["raw-lite"]}

        def add_extracted(self, contents, **kwargs):
            seen.append("add_extracted")
            raise AssertionError("lite must never call add_extracted")

    monkeypatch.setattr(
        sdk_write_processor,
        "SdkClientHandle",
        lambda profile, **kwargs: _SdkHandle(Sdk(), "lite"),
    )
    coordinator = sdk_write_processor.SdkWriteCoordinator(
        profiles=_Profiles(identity=None),  # type: ignore[arg-type]
        secrets=None,  # type: ignore[arg-type]
        data_dir=None,
        transport_factory=lambda _profile: None,
        sedimentation_enabled=lambda: False,
        memory_mode=lambda: "lite",
    )

    async def never(_job_id):
        return None

    async def scenario(app, _sdk, client):
        app.state.memory_write_drain.stop()
        if app.state.memory_write_drain_task is not None:
            await app.state.memory_write_drain_task
            app.state.memory_write_drain_task = None
        await client.put("/api/v1/sedimentation", json={"enabled": False})
        await client.put("/api/v1/settings/memory-mode", json={"mode": "lite"})
        app.state.history_import_gate_released = True
        written = await client.post(
            "/api/v2/memory-writes",
            json={
                "request_id": "paused-lite-turn",
                "session_id": "paused-lite-session",
                "source_id": "one-memory-manual",
                "messages": [
                    {"role": "user", "content": "保留原始会话"},
                    {"role": "assistant", "content": "已保留"},
                ],
            },
        )
        assert written.status_code == 202
        queue = app.state.memory_write_queue
        drain = DurableWriteDrain(queue, execute=never, lease=timedelta(seconds=30))
        [job_id] = drain.claim(limit=1)
        await execute_job(
            job_id,
            queue=queue,
            supervisor=coordinator,
            evidence=None,  # type: ignore[arg-type]
            admit_hy_invocation=lambda source_id, requires_generation, mark: (
                _admit_pipeline_invocation(
                    app,
                    source_id,
                    requires_generation,
                    mark,
                )
            ),
        )
        current = queue.get("paused-lite-turn")
        assert current is not None and current.state == "completed"
        assert current.sdk_raw_ids == ("raw-lite",)
        assert seen == ["add"]
        await coordinator.close()

    run_app(tmp_path, monkeypatch, scenario)
