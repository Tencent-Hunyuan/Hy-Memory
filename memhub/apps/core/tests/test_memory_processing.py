import asyncio
import json
import logging
import sqlite3
import threading
import time
from datetime import UTC, datetime

import pytest

from one_memory.diagnostic_log import configure_diagnostics, reset_diagnostics
from one_memory.hy_memory_bridge import (
    HyGraphNode,
    HyGraphSnapshot,
    HyMemoryBridgeError,
    HyMemoryIngestResult,
    HyMemoryOutput,
)
from one_memory.imports.adapters import (
    ScanCursorTouch,
    ScannedEvidence,
    SourceCapabilities,
    SourceCursor,
    SourceDescriptor,
    StorageKind,
)
from one_memory.imports.grants import SourceGrantStore
from one_memory.imports.jobs import ImportJobStore, ImportStatus
from one_memory.imports.models import (
    EvidenceContentKind,
    EvidenceInput,
    EvidenceOrigin,
    OriginMetadata,
    WrittenBy,
)
from one_memory.imports.runner import ImportRunner, live_sync_arrival
from one_memory.imports.sources.registry import RootCandidateKind
from one_memory.imports.storage import EvidenceStore
from one_memory.memory_governance import (
    MemoryDraft,
    MemoryGovernanceStore,
    MemoryKind,
    MemoryOrigin,
    MemoryStatus,
)
from one_memory.memory_processing import (
    UNIFIED_HY_AGENT_ID,
    MemoryProcessingSupervisor,
    MemoryTaskRunner,
    _conversation_chunks,
    _hy_agent_id,
    _is_hy_input_turn,
    _report_stale_known_reasons,
    _shared_source_lock,
    memory_task_timeout_seconds,
)
from one_memory.memory_queue import classify
from one_memory.memory_server.config_state import ConfigStore
from one_memory.memory_tasks import MemoryTaskKind, MemoryTaskStatus, MemoryTaskStore
from one_memory.model_profiles import (
    ModelCapabilities,
    ModelProfileInput,
    ModelProfilePatch,
    ModelProfileStore,
    ProviderKind,
)
from one_memory.secrets import InMemorySecretStore
from one_memory.service_levels import (
    ServiceLevel,
    ServiceLevelScheduler,
    ServiceResource,
    service_level_context,
)
from tests.person_door import as_person


@pytest.fixture(autouse=True)
def _segment_at_the_legacy_window(monkeypatch):
    """These cases exercise segmenting a single over-ceiling message (D74).

    The ceiling is now 60,000 characters of what HY reads; pin it to the old
    12,000 so the 12,001-character fixtures still cross it.
    """
    import one_memory.memory_processing as memory_processing

    monkeypatch.setattr(memory_processing, "_WHOLE_TURN_CHARACTERS", 12_000)


def test_stale_known_reason_diagnostics_never_fail_dispatch(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ONE_MEMORY_DIAGNOSTICS", "1")
    root = logging.getLogger()
    previous = root.level
    root.setLevel(logging.DEBUG)
    receipt = configure_diagnostics(tmp_path)
    try:
        _report_stale_known_reasons(
            "task-stale-context",
            {"revision_changed": 2, "not_confirmed": 1},
        )
    finally:
        reset_diagnostics()
        root.setLevel(previous)

    records = [json.loads(line) for line in receipt.path.read_text().splitlines()]
    assert [(row["details"]["reason"], row["details"]["count"]) for row in records] == [
        ("not_confirmed", 1),
        ("revision_changed", 2),
    ]


class FakeBridge:
    def __init__(
        self,
        *,
        outputs: tuple[HyMemoryOutput, ...] | None = None,
        profile: tuple[HyMemoryOutput, ...] = (),
        graph_nodes: tuple[HyGraphNode, ...] = (),
        fail: bool = False,
    ) -> None:
        self.outputs = outputs
        # Empty by default, which is what HY reports until it has learnt
        # something about the person. Its own writes never mention the profile,
        # so a bridge that only answered `ingest` looked complete while the
        # entire layer was missing.
        self.profile = profile
        # System 2's own output lives in the graph, not in what a write
        # returns, which is why it never reached governance.
        self.graph_nodes = graph_nodes
        self.graph_reads: list[tuple[str, str]] = []
        self.fail = fail
        self.requests = []
        self.extracted_contents: list[tuple[str, ...]] = []
        self.profile_reads: list[tuple[str, str]] = []
        self.digest_calls: list[tuple[str, str]] = []
        self.stored_agent_ids: tuple[str, ...] = ()
        self.closed = False
        self.search_result_ids: tuple[str, ...] = ()

    async def ingest(self, request) -> HyMemoryIngestResult:
        self.requests.append(request)
        if self.fail:
            raise HyMemoryBridgeError("hy_memory_unavailable")
        return HyMemoryIngestResult(
            success=True,
            memories=self.outputs
            if self.outputs is not None
            else (
                HyMemoryOutput(
                    memory_id="hy-memory-1",
                    content="用户偏好本地优先的记忆处理。",
                    layer="L0_BASIC_INFO",
                ),
            ),
        )

    async def ingest_extracted(self, request, contents) -> HyMemoryIngestResult:
        self.requests.append(request)
        self.extracted_contents.append(tuple(contents))
        if self.fail:
            raise HyMemoryBridgeError("hy_memory_unavailable")
        return HyMemoryIngestResult(
            success=True,
            memories=tuple(
                HyMemoryOutput(
                    memory_id=f"hy-imported-{index}",
                    content=content,
                    layer="L2_FACT",
                )
                for index, content in enumerate(contents)
            ),
        )

    async def close(self) -> None:
        self.closed = True

    async def outputs_for_request(self, **_kwargs) -> tuple[HyMemoryOutput, ...]:
        return self.outputs or ()

    async def search_scores(self, *_args, **_kwargs) -> tuple[object, ...]:
        return tuple((memory_id, 0.9) for memory_id in self.search_result_ids)

    async def profile_memories(
        self,
        *,
        user_id: str,
        agent_id: str,
    ) -> tuple[HyMemoryOutput, ...]:
        self.profile_reads.append((user_id, agent_id))
        return self.profile

    async def digest(self, *, user_id: str, agent_id: str) -> dict[str, object]:
        self.digest_calls.append((user_id, agent_id))
        return {"success": True, "tasks_processed": 1}

    async def graph_snapshot(
        self,
        *,
        user_id: str,
        agent_id: str,
        limit: int = 300,
    ) -> HyGraphSnapshot:
        self.graph_reads.append((user_id, agent_id))
        return HyGraphSnapshot(nodes=self.graph_nodes, edges=())

    async def list_agent_ids(self, *, user_id: str, limit: int = 1_000) -> tuple[str, ...]:
        return self.stored_agent_ids


def _put(
    store: EvidenceStore,
    *,
    message_id: str,
    role: str,
    written_by: WrittenBy,
    content: str,
    conversation_id: str = "conversation",
    workspace: str | None = None,
    content_kind: EvidenceContentKind = EvidenceContentKind.RAW_CONVERSATION,
    created_at: datetime | None = None,
    source_id: str = "cursor",
):
    return store.put(
        EvidenceInput(
            source_id=source_id,
            workspace=workspace,
            conversation_id=conversation_id,
            message_id=message_id,
            correlation_id=message_id,
            content_kind=content_kind,
            role=role,
            written_by=written_by,
            verbatim=content,
            created_at=created_at,
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    ).record


@pytest.fixture(autouse=True)
def _diagnostics_on(monkeypatch):
    """Several tests here read the pipeline log, so the log is on.

    Diagnostics are off unless asked; a test that reads the log has to ask.
    """
    monkeypatch.setenv("ONE_MEMORY_DIAGNOSTICS", "1")


def _setup(tmp_path):
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


def _task(tasks, profile, record, *, suffix: str = ""):
    return tasks.create(
        request_id=f"native:{record.evidence_id}:{record.revision}:{suffix}",
        user_id=record.user_id,
        kind=MemoryTaskKind.EXTRACT,
        input_ref=record.evidence_id,
        profile=profile,
    )


def _runner(tmp_path, stores, bridge):
    evidence, profiles, tasks, governance, secrets, _profile = stores
    return MemoryTaskRunner(
        tasks=tasks,
        profiles=profiles,
        secrets=secrets,
        evidence=evidence,
        governance=governance,
        data_dir=tmp_path,
        bridge_factory=lambda _profile, _secret, _data_dir: bridge,
    )


def test_memory_task_logs_six_step_durations_without_content(tmp_path) -> None:
    stores = _setup(tmp_path)
    evidence, _profiles, tasks, _governance, _secrets, profile = stores
    marker = "private-task-content-marker"
    record = _put(
        evidence,
        message_id="step-log",
        role="user",
        written_by=WrittenBy.PERSON,
        content=marker,
    )
    task = _task(tasks, profile, record)
    root = logging.getLogger()
    previous = root.level
    root.setLevel(logging.DEBUG)
    receipt = configure_diagnostics(tmp_path)
    try:
        asyncio.run(_runner(tmp_path, stores, FakeBridge()).run(task.task_id))
    finally:
        reset_diagnostics()
        root.setLevel(previous)
    written = receipt.path.read_text()
    events = [json.loads(line) for line in written.splitlines()]
    started = next(event for event in events if event["event"] == "memory_task.started")
    assert started["level"] == "info"
    steps = [event for event in events if event["event"] == "memory_task.step_completed"]
    assert [event["details"]["phase"] for event in steps] == ["1", "2", "3", "4", "5", "6"]
    finished = next(event for event in events if event["event"] == "memory_task.finished")
    assert finished["level"] == "info"
    assert finished["details"] == {
        "attempt": 1,
        "pending_count": 0,
        "result_count": 1,
    }
    assert tasks.processing_metrics()["hy_calls"] == 1
    assert all(event["duration_ms"] >= 0 for event in steps)
    assert marker not in written


def test_assistant_turns_reach_hy_so_references_can_be_resolved(tmp_path) -> None:
    """助手那一侧要进抽取器，否则「就按你说的那个」指的是什么，没人知道。

    HY 的抽取 prompt 自己就是为双向设计的：示例 1 明写「旧消息只用于指代消解」，
    示例 2 叫「浓缩 agent 的长篇回复」。我们过去只喂用户那一侧，
    等于把它这两条能力饿死了。
    """
    stores = _setup(tmp_path)
    evidence, _profiles, tasks, _governance, _secrets, profile = stores
    first = _put(
        evidence,
        message_id="u1",
        role="user",
        written_by=WrittenBy.PERSON,
        content="前端网络请求应该怎么统一？",
        workspace="workspace:v1:one-memory",
        created_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
    )
    _put(
        evidence,
        message_id="a1",
        role="assistant",
        written_by=WrittenBy.AGENT,
        content="建议统一走 axios 封装，不要裸调 fetch。",
        workspace="workspace:v1:one-memory",
        created_at=datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC),
    )
    _put(
        evidence,
        message_id="u2",
        role="user",
        written_by=WrittenBy.PERSON,
        content="就按你说的那个来。",
        workspace="workspace:v1:one-memory",
        created_at=datetime(2026, 1, 1, 0, 0, 2, tzinfo=UTC),
    )
    task = _task(tasks, profile, first)
    bridge = FakeBridge()

    asyncio.run(_runner(tmp_path, stores, bridge).run(task.task_id))

    # One turn per request; the next turn reaches HY with this one as history.
    assert [(turn.role, turn.content) for turn in bridge.requests[0].turns] == [
        ("user", "前端网络请求应该怎么统一？"),
        ("assistant", "建议统一走 axios 封装，不要裸调 fetch。"),
    ]


def test_secrets_in_assistant_output_are_redacted_too(tmp_path) -> None:
    """助手那一侧现在也会被送出去，所以脱敏必须一样管到它。"""
    stores = _setup(tmp_path)
    evidence, _profiles, tasks, _governance, _secrets, profile = stores
    user = _put(
        evidence,
        message_id="u1",
        role="user",
        written_by=WrittenBy.PERSON,
        content="帮我登录一下。",
        created_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
    )
    _put(
        evidence,
        message_id="a1",
        role="assistant",
        written_by=WrittenBy.AGENT,
        content="执行：docker login --username kristinhe --password exposed-token-value",
        created_at=datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC),
    )
    task = _task(tasks, profile, user)
    bridge = FakeBridge()

    asyncio.run(_runner(tmp_path, stores, bridge).run(task.task_id))

    sent = [turn.content for turn in bridge.requests[0].turns]
    assert "exposed-token-value" not in " ".join(sent)
    assert "<redacted-secret>" in sent[1]


def test_agent_owned_memories_land_usable_but_marked_as_the_assistant(tmp_path) -> None:
    """助手输出里的事实也直接可用，但来源标成助手。

    不再有「待确认」这道关：它让助手那侧的内容永远进不了召回。能区分的是
    origin —— 召回和事项据此知道这句话是 AI 说的，不是本人的判断。
    """
    stores = _setup(tmp_path)
    evidence, _profiles, tasks, governance, _secrets, profile = stores
    user = _put(
        evidence,
        message_id="u1",
        role="user",
        written_by=WrittenBy.PERSON,
        content="帮我写一篇远程办公的短文。",
    )
    _put(
        evidence,
        message_id="a1",
        role="assistant",
        written_by=WrittenBy.AGENT,
        content="好的，开头是……结尾是自主性带来更高产出。",
    )
    task = _task(tasks, profile, user)
    bridge = FakeBridge(
        outputs=(
            HyMemoryOutput(
                memory_id="hy-user-1",
                content="用户需要一篇论述远程办公提升效率的短文。",
                owner="user",
            ),
            HyMemoryOutput(
                memory_id="hy-agent-1",
                content="assistant 为用户写了一篇短文，论点是远程办公提升效率。",
                owner="agent",
            ),
        )
    )

    asyncio.run(_runner(tmp_path, stores, bridge).run(task.task_id))

    landed = {item.content: item for item in governance.list(user_id="local")}
    assert set(landed) == {
        "用户需要一篇论述远程办公提升效率的短文。",
        "assistant 为用户写了一篇短文，论点是远程办公提升效率。",
    }
    person = landed["用户需要一篇论述远程办公提升效率的短文。"]
    agent = landed["assistant 为用户写了一篇短文，论点是远程办公提升效率。"]
    assert person.origin is MemoryOrigin.EXTRACTED
    assert person.status is MemoryStatus.CONFIRMED
    assert agent.origin is MemoryOrigin.AGENT
    assert agent.status is MemoryStatus.CONFIRMED


def test_an_agent_owned_memory_alone_is_still_usable(tmp_path) -> None:
    """一批里只有助手那侧的记忆，也照样可用，不等人确认。"""
    stores = _setup(tmp_path)
    evidence, _profiles, tasks, governance, _secrets, profile = stores
    user = _put(
        evidence,
        message_id="u1",
        role="user",
        written_by=WrittenBy.PERSON,
        content="帮我排一个七天日本行程。",
    )
    _put(
        evidence,
        message_id="a1",
        role="assistant",
        written_by=WrittenBy.AGENT,
        content="东京—京都—奈良—大阪，关西机场离境。",
    )
    task = _task(tasks, profile, user)
    bridge = FakeBridge(
        outputs=(
            HyMemoryOutput(
                memory_id="hy-agent-1",
                content="assistant 为用户制定了七天日本行程。",
                owner="agent",
            ),
        )
    )

    asyncio.run(_runner(tmp_path, stores, bridge).run(task.task_id))

    only = governance.list(user_id="local")[0]
    assert only.origin is MemoryOrigin.AGENT
    assert only.status is MemoryStatus.CONFIRMED


def test_memories_without_an_owner_still_land(tmp_path) -> None:
    """正向对照：没标 owner 的照旧写下来，否则上面那条测的只是「什么都不写」。"""
    stores = _setup(tmp_path)
    evidence, _profiles, tasks, governance, _secrets, profile = stores
    user = _put(
        evidence,
        message_id="u1",
        role="user",
        written_by=WrittenBy.PERSON,
        content="周末不排评审会。",
    )
    task = _task(tasks, profile, user)
    bridge = FakeBridge(
        outputs=(HyMemoryOutput(memory_id="hy-1", content="用户周末不安排评审会。"),)
    )

    asyncio.run(_runner(tmp_path, stores, bridge).run(task.task_id))

    assert [item.content for item in governance.list(user_id="local")] == ["用户周末不安排评审会。"]


def test_assistant_only_window_is_usable_and_attributed_to_the_assistant(tmp_path) -> None:
    """D04: keep project reports without attributing them to the person."""
    stores = _setup(tmp_path)
    evidence, _profiles, tasks, governance, _secrets, profile = stores
    first = _put(
        evidence,
        message_id="a1",
        role="assistant",
        written_by=WrittenBy.AGENT,
        content="我先总结一下刚才的方案。",
    )
    _put(
        evidence,
        message_id="a2",
        role="assistant",
        written_by=WrittenBy.AGENT,
        content="另外还有三个待办。",
    )
    task = _task(tasks, profile, first)
    bridge = FakeBridge()

    asyncio.run(_runner(tmp_path, stores, bridge).run(task.task_id))

    assert len(bridge.requests) == 1
    assert [turn.role for turn in bridge.requests[0].turns] == ["assistant", "assistant"]
    memories = governance.list(user_id="local")
    assert memories
    assert all(memory.origin is MemoryOrigin.AGENT for memory in memories)
    assert all(memory.status is MemoryStatus.CONFIRMED for memory in memories)


def test_what_lands_in_governance_still_comes_from_the_person(tmp_path) -> None:
    stores = _setup(tmp_path)
    evidence, _profiles, tasks, governance, _secrets, profile = stores
    user = _put(
        evidence,
        message_id="u1",
        role="user",
        written_by=WrittenBy.PERSON,
        content="这个交互太绕了，我更喜欢一步完成。",
        workspace="workspace:v1:one-memory",
    )
    _put(
        evidence,
        message_id="a1",
        role="assistant",
        written_by=WrittenBy.AGENT,
        content="明白，你偏好更直接的单步流程。",
        workspace="workspace:v1:one-memory",
    )
    task = _task(tasks, profile, user)
    bridge = FakeBridge()

    completed = asyncio.run(_runner(tmp_path, stores, bridge).run(task.task_id))

    assert completed.status is MemoryTaskStatus.COMPLETED
    assert bridge.extracted_contents == []
    request = bridge.requests[0]
    assert request.agent_id.startswith(f"{UNIFIED_HY_AGENT_ID}:scope:")
    assert request.agent_id != UNIFIED_HY_AGENT_ID
    # 助手那一句现在也送出去了，但落库的仍旧只有一条 —— FakeBridge 默认
    # 不标 owner，所以这里测的是「多送不等于多记」。
    assert [(turn.role, turn.content) for turn in request.turns] == [
        ("user", user.verbatim),
        ("assistant", "明白，你偏好更直接的单步流程。"),
    ]
    assert len(governance.list(user_id="local")) == 1
    memory = governance.list(user_id="local")[0]
    assert memory.status is MemoryStatus.CONFIRMED
    assert memory.content == "用户偏好本地优先的记忆处理。"
    assert memory.scopes == ("all-apps", "workspace:v1:one-memory")
    # 出处是这条记忆所在的那一段来回，不只是被抽中的那一句。
    # 右栏点开要能读懂，而「明白，你偏好更直接的单步流程」正是读懂它的一半。
    assert len(memory.evidence_ids) == 2
    assert user.evidence_id in memory.evidence_ids


def test_raw_conversation_secrets_are_redacted_before_hy(tmp_path) -> None:
    stores = _setup(tmp_path)
    evidence, _profiles, tasks, _governance, _secrets, profile = stores
    user = _put(
        evidence,
        message_id="u1",
        role="user",
        written_by=WrittenBy.PERSON,
        content="docker login --username kristinhe --password exposed-token-value",
    )
    task = _task(tasks, profile, user)
    bridge = FakeBridge()

    asyncio.run(_runner(tmp_path, stores, bridge).run(task.task_id))

    assert bridge.requests[0].turns[0].content == (
        "docker login --username kristinhe --password <redacted-secret>"
    )
    assert user.verbatim == ("docker login --username kristinhe --password exposed-token-value")


def test_hy_output_is_not_rejected_by_one_memory_policy(tmp_path) -> None:
    stores = _setup(tmp_path)
    evidence, _profiles, tasks, governance, _secrets, profile = stores
    user = _put(
        evidence,
        message_id="u1",
        role="user",
        written_by=WrittenBy.PERSON,
        content="请帮我审查这份方案。",
    )
    output = HyMemoryOutput(
        memory_id="hy-l1",
        content="方案审查体现了用户重视可验证交付。",
        layer="L0_BASIC_INFO",
    )
    task = _task(tasks, profile, user)

    asyncio.run(
        _runner(
            tmp_path,
            stores,
            FakeBridge(outputs=(output,)),
        ).run(task.task_id)
    )

    memory = governance.list(user_id="local")[0]
    assert memory.memory_id == "hy-l1"
    assert memory.status is MemoryStatus.CONFIRMED
    assert memory.content == output.content


def test_hy_output_secret_is_redacted_before_governance_projection(tmp_path) -> None:
    stores = _setup(tmp_path)
    evidence, _profiles, tasks, governance, _secrets, profile = stores
    user = _put(
        evidence,
        message_id="u1",
        role="user",
        written_by=WrittenBy.PERSON,
        content="Remember the registry setup.",
    )
    task = _task(tasks, profile, user)
    output = HyMemoryOutput(
        memory_id="hy-secret",
        content="Registry token=hallucinated-secret-value",
        layer="L0_BASIC_INFO",
    )

    asyncio.run(
        _runner(
            tmp_path,
            stores,
            FakeBridge(outputs=(output,)),
        ).run(task.task_id)
    )

    assert governance.list(user_id="local")[0].content == ("Registry token=<redacted-secret>")


def test_workspace_intention_reaches_every_app_and_keeps_its_project(tmp_path) -> None:
    stores = _setup(tmp_path)
    evidence, _profiles, tasks, governance, _secrets, profile = stores
    user = _put(
        evidence,
        message_id="u1",
        role="user",
        written_by=WrittenBy.PERSON,
        content="明天完成 One Memory 的完整链路。",
        workspace="workspace:v1:one-memory",
    )
    task = _task(tasks, profile, user)
    output = HyMemoryOutput(
        memory_id="hy-intention",
        content="用户计划明天完成 One Memory 的完整链路。",
        layer="L7_INTENTION",
    )

    asyncio.run(
        _runner(
            tmp_path,
            stores,
            FakeBridge(outputs=(output,)),
        ).run(task.task_id)
    )

    memory = governance.list(user_id="local")[0]
    assert memory.scopes == ("all-apps", "workspace:v1:one-memory")


def test_what_hy_learns_about_the_person_reaches_every_app(tmp_path) -> None:
    """The profile layer arrives by being read back, not by being returned.

    HY writes basic info on its own path and the ids never appear in the
    ingest response, so for as long as this was not read back, the profile
    existed only inside HY. On this machine that was 483 confirmed memories
    all scoped to one project and nothing at all for a host that does not say
    which project it is in — recall then came back empty by construction.
    """
    stores = _setup(tmp_path)
    evidence, _profiles, tasks, governance, _secrets, profile = stores
    user = _put(
        evidence,
        message_id="u1",
        role="user",
        written_by=WrittenBy.PERSON,
        content="我是产品出身，叫 Kristin。",
        workspace="workspace:v1:one-memory",
    )
    task = _task(tasks, profile, user)
    bridge = FakeBridge(
        outputs=(
            HyMemoryOutput(
                memory_id="hy-fact",
                content="用户偏好本地优先的记忆处理。",
                layer="L2_FACT",
            ),
        ),
        profile=(
            HyMemoryOutput(
                memory_id="hy-profile",
                content="The user's name is Kristin, occupation is 产品出身.",
                layer="L0_BASIC_INFO",
            ),
        ),
    )

    asyncio.run(_runner(tmp_path, stores, bridge).run(task.task_id))

    memories = {memory.memory_id: memory for memory in governance.list(user_id="local")}
    assert memories["hy-fact"].scopes == ("all-apps", "workspace:v1:one-memory")
    # The person is the same person in every app, so the profile is readable
    # from all of them — and still from the project, which is where a host
    # that does name its project will look.
    assert memories["hy-profile"].scopes == (
        "all-apps",
        "workspace:v1:one-memory",
    )
    assert bridge.profile_reads == [("local", bridge.requests[0].agent_id)]


def test_the_same_unchanged_profile_is_not_recorded_again(tmp_path) -> None:
    """Every task reads the profile, and it rarely changes between two.

    Read back per task rather than reported by the write, the same basic info
    comes back for as long as HY has learnt nothing new. Recorded each time it
    would mean one memory per conversation saying the same thing.
    """
    stores = _setup(tmp_path)
    evidence, _profiles, tasks, governance, _secrets, profile = stores
    same_profile = (
        HyMemoryOutput(
            memory_id="hy-profile",
            content="The user's name is Kristin.",
            layer="L0_BASIC_INFO",
        ),
    )
    for index in range(2):
        record = _put(
            evidence,
            message_id=f"u{index}",
            role="user",
            written_by=WrittenBy.PERSON,
            content=f"第 {index} 轮。",
            conversation_id=f"conversation-{index}",
        )
        task = _task(tasks, profile, record)
        asyncio.run(
            _runner(
                tmp_path,
                stores,
                FakeBridge(outputs=(), profile=same_profile),
            ).run(task.task_id)
        )

    profiles = [
        memory for memory in governance.list(user_id="local") if memory.memory_id == "hy-profile"
    ]
    assert len(profiles) == 1
    # Both conversations are why we believe it, so both are cited.
    assert len(profiles[0].evidence_ids) == 2


def test_the_profile_layers_are_the_ones_hy_calls_profile(tmp_path) -> None:
    """Pin our layer list to HY's, rather than keeping a second opinion.

    HY decides what a profile is; its client holds
    `_PROFILE_LAYERS = {"l0_basic_info", "l6_schema"}`. Ours drifted from that
    once already, to a layer that does not exist.
    """
    from one_memory.memory_processing import _memory_scopes

    stores = _setup(tmp_path)
    evidence = stores[0]
    record = _put(
        evidence,
        message_id="u1",
        role="user",
        written_by=WrittenBy.PERSON,
        content="任意内容。",
        workspace="workspace:v1:atlas",
    )
    chunk = (evidence.list_conversation(record.evidence_id)[0],)

    for layer in ("L0_BASIC_INFO", "L6_SCHEMA"):
        assert _memory_scopes(chunk, layer=layer)[0] == "all-apps", layer


@pytest.mark.parametrize(
    "layer", [None, "L1_RAW", "L2_FACT", "L3_SUMMARY", "L4_IDENTITY", "L7_INTENTION"]
)
def test_a_memory_from_one_app_is_readable_from_every_app(tmp_path, layer) -> None:
    """Cross-app recall is the product: a fact learned in one app reaches the others.

    The workspace scope stays alongside, so a caller scoped to that project
    still reads it; `all-apps` is a literal scope, not a wildcard.
    """
    from one_memory.memory_processing import _memory_scopes

    stores = _setup(tmp_path)
    evidence = stores[0]
    record = _put(
        evidence,
        message_id="u1",
        role="user",
        written_by=WrittenBy.PERSON,
        content="任意内容。",
        workspace="workspace:v1:atlas",
    )
    chunk = (evidence.list_conversation(record.evidence_id)[0],)
    unscoped = _put(
        evidence,
        message_id="u2",
        role="user",
        written_by=WrittenBy.PERSON,
        content="没有工作区的对话。",
        conversation_id="empty-window",
    )

    assert _memory_scopes(chunk, layer=layer) == ("all-apps", "workspace:v1:atlas")
    scopes = _memory_scopes((unscoped,), layer=layer)
    assert scopes[0] == "all-apps"
    assert scopes[1].startswith("workspace:v1:")


def test_unscoped_intention_uses_isolated_conversation_scope(tmp_path) -> None:
    stores = _setup(tmp_path)
    evidence, _profiles, tasks, governance, _secrets, profile = stores
    user = _put(
        evidence,
        message_id="u1",
        role="user",
        written_by=WrittenBy.PERSON,
        content="Deploy the admin backend.",
        conversation_id="empty-window-task",
    )
    task = _task(tasks, profile, user)
    output = HyMemoryOutput(
        memory_id="hy-intention",
        content="Deploy the admin backend.",
        layer="L7_INTENTION",
    )
    bridge = FakeBridge(outputs=(output,))

    asyncio.run(
        _runner(
            tmp_path,
            stores,
            bridge,
        ).run(task.task_id)
    )

    memory = governance.list(user_id="local")[0]
    assert bridge.requests[0].agent_id.startswith(f"{UNIFIED_HY_AGENT_ID}:scope:")
    assert memory.scopes[0] == "all-apps"
    assert memory.scopes[1].startswith("workspace:v1:")
    assert "empty-window-task" not in memory.scopes[1]


def test_hy_empty_result_is_a_successful_no_memory_decision(tmp_path) -> None:
    stores = _setup(tmp_path)
    evidence, _profiles, tasks, governance, _secrets, profile = stores
    user = _put(
        evidence,
        message_id="u1",
        role="user",
        written_by=WrittenBy.PERSON,
        content="把按钮移到左边。",
    )
    task = _task(tasks, profile, user)

    completed = asyncio.run(
        _runner(
            tmp_path,
            stores,
            FakeBridge(outputs=()),
        ).run(task.task_id)
    )

    assert completed.status is MemoryTaskStatus.COMPLETED
    assert governance.list(user_id="local") == []


@pytest.mark.parametrize("author", [WrittenBy.PERSON, WrittenBy.AGENT, WrittenBy.UNKNOWN])
def test_memory_content_without_raw_uses_add_extracted_and_is_usable(tmp_path, author):
    stores = _setup(tmp_path)
    evidence, _profiles, tasks, governance, _secrets, profile = stores
    imported = _put(
        evidence,
        message_id="memory-1",
        role="memory",
        written_by=author,
        content="用户偏好简洁直接的中文表达。",
        content_kind=EvidenceContentKind.MEMORY_CONTENT,
    )
    task = _task(tasks, profile, imported)
    bridge = FakeBridge()

    asyncio.run(_runner(tmp_path, stores, bridge).run(task.task_id))

    assert bridge.extracted_contents == [(imported.verbatim,)]
    memory = governance.list(user_id="local")[0]
    assert memory.status is MemoryStatus.CONFIRMED
    assert memory.content == imported.verbatim


def test_existing_memory_empty_hy_result_never_fabricates_a_governed_output(tmp_path):
    stores = _setup(tmp_path)
    evidence, _profiles, tasks, governance, _secrets, profile = stores
    imported = _put(
        evidence,
        message_id="m1",
        role="memory",
        written_by=WrittenBy.UNKNOWN,
        content="This report has no underlying chat.",
        content_kind=EvidenceContentKind.MEMORY_CONTENT,
    )
    task = _task(tasks, profile, imported)

    class EmptyBridge(FakeBridge):
        async def ingest_extracted(self, request, contents):
            self.extracted_contents.append(tuple(contents))
            return HyMemoryIngestResult(success=True, memories=())

    bridge = EmptyBridge()
    completed = asyncio.run(_runner(tmp_path, stores, bridge).run(task.task_id))
    assert bridge.extracted_contents == [(imported.verbatim,)]
    assert completed.status is MemoryTaskStatus.FAILED
    assert completed.last_error_code == "hy_memory_import_no_readable_output"
    assert governance.list(user_id="local") == []
    assert evidence.get(imported.evidence_id).verbatim == imported.verbatim


@pytest.mark.parametrize("existing_origin", [MemoryOrigin.AGENT, MemoryOrigin.EXTRACTED])
def test_agent_output_keeps_the_origin_of_a_same_content_memory(tmp_path, existing_origin):
    stores = _setup(tmp_path)
    evidence, _profiles, tasks, governance, _secrets, profile = stores
    raw = _put(
        evidence,
        message_id="a1",
        role="assistant",
        written_by=WrittenBy.AGENT,
        content="The assistant reports that the deployment finished.",
    )
    original = as_person(governance).create(
        MemoryDraft(
            user_id="local",
            kind=MemoryKind.CHAT,
            content=raw.verbatim,
            scopes=("all-apps",),
            evidence_ids=(raw.evidence_id,),
            origin=existing_origin,
        ),
        request_id="existing-report",
    )
    task = _task(tasks, profile, raw)
    bridge = FakeBridge(
        outputs=(HyMemoryOutput(memory_id="same-report", content=raw.verbatim, owner="agent"),)
    )
    asyncio.run(_runner(tmp_path, stores, bridge).run(task.task_id))
    current = governance.get(original.memory_id, user_id="local")
    assert current.status is MemoryStatus.CONFIRMED
    assert current.origin == existing_origin
    assert current.revision == original.revision


def test_a_new_hy_memory_is_mirrored_under_its_own_id_even_when_the_text_repeats(tmp_path):
    """HY decides whether two memories are one; the ledger mirrors its ids.

    Folding a new HY id into an older row with the same words left HY holding a
    node the product never showed, and any later HY update or delete of that id
    had nothing to land on.
    """
    stores = _setup(tmp_path)
    evidence, _profiles, tasks, governance, _secrets, profile = stores
    first = _put(
        evidence,
        message_id="u1",
        role="user",
        written_by=WrittenBy.PERSON,
        content="我周末不开评审会。",
        conversation_id="c1",
    )
    earlier = as_person(governance).create(
        MemoryDraft(
            user_id="local",
            kind=MemoryKind.CHAT,
            content="用户周末不安排评审会。",
            scopes=("all-apps",),
            evidence_ids=(first.evidence_id,),
        ),
        request_id="earlier",
        memory_id="hy-earlier",
    )
    second = _put(
        evidence,
        message_id="u2",
        role="user",
        written_by=WrittenBy.PERSON,
        content="再说一次，周末别排评审。",
        conversation_id="c2",
    )
    task = _task(tasks, profile, second)
    bridge = FakeBridge(
        outputs=(HyMemoryOutput(memory_id="hy-later", content="用户周末不安排评审会。"),)
    )

    asyncio.run(_runner(tmp_path, stores, bridge).run(task.task_id))

    later = governance.get("hy-later", user_id="local")
    assert later is not None
    assert later.content == "用户周末不安排评审会。"
    assert later.evidence_ids == (second.evidence_id,)
    assert governance.get(earlier.memory_id, user_id="local").evidence_ids == (first.evidence_id,)


def test_a_memory_file_the_person_imported_is_readable_from_every_app(tmp_path):
    stores = _setup(tmp_path)
    evidence, *_ = stores
    imported = evidence.put(
        EvidenceInput(
            source_id="memory-content-export",
            conversation_id="report-a",
            message_id="1",
            role="memory",
            written_by=WrittenBy.UNKNOWN,
            content_kind=EvidenceContentKind.MEMORY_CONTENT,
            verbatim="An imported profile the person chose to bring in.",
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    ).record
    from one_memory.memory_processing import _memory_scopes

    assert _hy_agent_id((imported,)) != UNIFIED_HY_AGENT_ID
    for layer in ("L0_BASIC_INFO", "L2_FACT", "L6_SCHEMA"):
        assert _memory_scopes((imported,), layer=layer)[0] == "all-apps"


def test_reimport_leaves_an_existing_memory_and_its_scopes_alone(tmp_path):
    stores = _setup(tmp_path)
    evidence, _profiles, tasks, governance, _secrets, profile = stores
    imported = _put(
        evidence,
        message_id="m1",
        role="memory",
        written_by=WrittenBy.UNKNOWN,
        content="An explicitly reviewed report.",
        content_kind=EvidenceContentKind.MEMORY_CONTENT,
    )
    original = as_person(governance).create(
        MemoryDraft(
            user_id="local",
            kind=MemoryKind.CHAT,
            content=imported.verbatim,
            scopes=("workspace:reviewed",),
            evidence_ids=(imported.evidence_id,),
            origin=MemoryOrigin.AGENT,
        ),
        request_id="original-reviewed-report",
    )
    task = _task(tasks, profile, imported)
    asyncio.run(_runner(tmp_path, stores, FakeBridge()).run(task.task_id))
    current = governance.get(original.memory_id, user_id="local")
    assert current.status is MemoryStatus.CONFIRMED
    assert current.scopes == ("workspace:reviewed",)


def test_memory_content_secret_is_redacted_before_hy_and_local_projection(tmp_path) -> None:
    stores = _setup(tmp_path)
    evidence, _profiles, tasks, governance, _secrets, profile = stores
    imported = _put(
        evidence,
        message_id="memory-1",
        role="memory",
        written_by=WrittenBy.AGENT,
        content="Registry password=exposed-token-value",
        content_kind=EvidenceContentKind.MEMORY_CONTENT,
    )
    task = _task(tasks, profile, imported)
    bridge = FakeBridge()

    asyncio.run(_runner(tmp_path, stores, bridge).run(task.task_id))

    assert bridge.extracted_contents == [("Registry password=<redacted-secret>",)]
    assert governance.list(user_id="local")[0].content == ("Registry password=<redacted-secret>")


def test_memory_content_with_raw_never_becomes_an_additional_user_turn(tmp_path) -> None:
    stores = _setup(tmp_path)
    evidence, _profiles, tasks, governance, _secrets, profile = stores
    user = _put(
        evidence,
        message_id="u1",
        role="user",
        written_by=WrittenBy.PERSON,
        content="我喜欢更直接的回答。",
    )
    imported = _put(
        evidence,
        message_id="memory-1",
        role="memory",
        written_by=WrittenBy.AGENT,
        content="用户偏好简洁表达。",
        content_kind=EvidenceContentKind.MEMORY_CONTENT,
    )
    task = _task(tasks, profile, user)
    bridge = FakeBridge()

    asyncio.run(_runner(tmp_path, stores, bridge).run(task.task_id))

    assert bridge.extracted_contents == []
    assert [turn.content for turn in bridge.requests[0].turns] == [user.verbatim]
    assert evidence.get(imported.evidence_id).verbatim == imported.verbatim
    assert bridge.requests[0].source_context is not None
    assert [record.evidence_id for record in bridge.requests[0].source_context.current] == [
        user.evidence_id
    ]
    assert governance.list(user_id="local")[0].content == "用户偏好本地优先的记忆处理。"


def test_conversation_order_is_decided_by_time_not_by_hash(tmp_path) -> None:
    """A turn's order must come from its timestamp, not from an idle tie-break.

    `EvidenceStore.list_conversation` orders by
    `(created_at, source_ordinal, evidence_id)`. Records with no explicit
    `created_at` all tie on the first key, so the order falls through to
    `evidence_id`, which is a UUID5 hash of the message - effectively random.
    Tests that assert a specific order must therefore give their records
    ordered timestamps. This pins that contract so the ordering tests cannot
    silently become flaky again.
    """
    stores = _setup(tmp_path)
    evidence, *_rest = stores
    second = _put(
        evidence,
        message_id="z-later",
        role="assistant",
        written_by=WrittenBy.AGENT,
        content="second",
        created_at=datetime(2026, 1, 1, 0, 0, 5, tzinfo=UTC),
    )
    first = _put(
        evidence,
        message_id="a-earlier",
        role="user",
        written_by=WrittenBy.PERSON,
        content="first",
        created_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
    )
    ordered = [record.evidence_id for record in evidence.list_conversation(first.evidence_id)]
    assert ordered == [first.evidence_id, second.evidence_id]


def test_host_envelopes_and_non_person_user_turns_still_never_reach_hy(tmp_path) -> None:
    """助手轮次放行之后，被挡的那两类必须仍旧被挡。

    这里挡的是「冒充本人」的东西：harness 替人打的字、宿主塞进 user 角色的
    系统提示。助手轮次从不冒充谁，所以它进来不影响这条边界。
    """
    stores = _setup(tmp_path)
    evidence, *_rest = stores
    person = _put(
        evidence,
        message_id="person",
        role="user",
        written_by=WrittenBy.PERSON,
        content="真实问题",
        created_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
    )
    assistant = _put(
        evidence,
        message_id="assistant",
        role="assistant",
        written_by=WrittenBy.AGENT,
        content="Generated response",
        created_at=datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC),
    )
    _put(
        evidence,
        message_id="system-user",
        role="user",
        written_by=WrittenBy.AGENT,
        content="<system_reminder>hidden</system_reminder>",
        created_at=datetime(2026, 1, 1, 0, 0, 2, tzinfo=UTC),
    )
    _put(
        evidence,
        message_id="harness",
        role="user",
        written_by=WrittenBy.PERSON,
        content="<environment_context>cwd=/tmp</environment_context>",
        created_at=datetime(2026, 1, 1, 0, 0, 3, tzinfo=UTC),
    )

    chunks = _conversation_chunks(evidence.list_conversation(person.evidence_id))

    assert len(chunks) == 1
    assert [record.evidence_id for record in chunks[0]] == [
        person.evidence_id,
        assistant.evidence_id,
    ]


def test_unavailable_hy_keeps_task_waiting_for_same_model_snapshot(tmp_path) -> None:
    stores = _setup(tmp_path)
    evidence, _profiles, tasks, governance, _secrets, profile = stores
    user = _put(
        evidence,
        message_id="u1",
        role="user",
        written_by=WrittenBy.PERSON,
        content="记住这段对话。",
    )
    task = _task(tasks, profile, user)

    waiting = asyncio.run(
        _runner(
            tmp_path,
            stores,
            FakeBridge(fail=True),
        ).run(task.task_id)
    )

    assert waiting.status is MemoryTaskStatus.WAITING_FOR_MODEL
    assert waiting.profile_revision == task.profile_revision
    assert governance.list(user_id="local") == []


def test_backfill_creates_native_tasks_for_all_conversations(tmp_path) -> None:
    stores = _setup(tmp_path)
    evidence, profiles, tasks, governance, secrets, _profile = stores
    first = _put(
        evidence,
        message_id="u1",
        role="user",
        written_by=WrittenBy.PERSON,
        content="第一段普通对话。",
        conversation_id="conversation-1",
        content_kind=EvidenceContentKind.MEMORY_CONTENT,
    )
    second = _put(
        evidence,
        message_id="u2",
        role="user",
        written_by=WrittenBy.PERSON,
        content="第二段普通对话。",
        conversation_id="conversation-2",
        content_kind=EvidenceContentKind.MEMORY_CONTENT,
    )

    async def exercise() -> None:
        supervisor = MemoryProcessingSupervisor(
            tasks=tasks,
            profiles=profiles,
            secrets=secrets,
            evidence=evidence,
            governance=governance,
            data_dir=tmp_path,
            runner_factory=lambda: _runner(tmp_path, stores, FakeBridge(outputs=())),
        )
        supervisor.backfill()
        for _ in range(100):
            if len(tasks.list()) == 2 and all(
                task.status is MemoryTaskStatus.COMPLETED for task in tasks.list()
            ):
                break
            await asyncio.sleep(0.01)
        await supervisor.shutdown()

    asyncio.run(exercise())

    assert {task.input_ref for task in tasks.list()} == {
        first.evidence_id,
        second.evidence_id,
    }


def test_write_bridges_are_shared_within_a_source_and_separate_across_sources(tmp_path) -> None:
    stores = _setup(tmp_path)
    _evidence, _profiles, _tasks, _governance, _secrets, profile = stores
    supervisor = MemoryProcessingSupervisor(
        tasks=stores[2],
        profiles=stores[1],
        secrets=stores[4],
        evidence=stores[0],
        governance=stores[3],
        data_dir=tmp_path,
    )

    workbuddy = supervisor._bridge(profile, None, tmp_path, scope_key="workbuddy")
    assert supervisor._bridge(profile, None, tmp_path, scope_key="workbuddy") is workbuddy
    assert supervisor._bridge(profile, None, tmp_path, scope_key="cursor") is not workbuddy


def test_payment_failure_stops_profile_until_successful_probe_resumes_it(tmp_path) -> None:
    stores = _setup(tmp_path)
    evidence, profiles, tasks, governance, secrets, profile = stores
    first = _put(
        evidence,
        message_id="payment-first",
        role="user",
        written_by=WrittenBy.PERSON,
        content="第一段历史。",
        conversation_id="payment-1",
    )
    second = _put(
        evidence,
        message_id="payment-second",
        role="user",
        written_by=WrittenBy.PERSON,
        content="第二段历史。",
        conversation_id="payment-2",
    )
    first_task = _task(tasks, profile, first)
    second_task = _task(tasks, profile, second)

    class PaymentRequiredBridge(FakeBridge):
        async def ingest(self, request) -> HyMemoryIngestResult:
            self.requests.append(request)
            raise HyMemoryBridgeError("hy_memory_model_payment_required")

    bridge = PaymentRequiredBridge()
    failed = asyncio.run(_runner(tmp_path, stores, bridge).run(first_task.task_id))
    assert failed.status is MemoryTaskStatus.WAITING_FOR_MODEL
    assert failed.last_error_code == "hy_memory_model_payment_required"

    supervisor = MemoryProcessingSupervisor(
        tasks=tasks,
        profiles=profiles,
        secrets=secrets,
        evidence=evidence,
        governance=governance,
        data_dir=tmp_path,
    )
    assert tasks.profile_stop_reason(profile.profile_id, profile.revision) == (
        "hy_memory_model_payment_required"
    )
    assert supervisor._classify([second_task]) == []
    assert len(bridge.requests) == 1

    assert tasks.resume_profile_after_probe(profile.profile_id, profile.revision) == 1
    assert tasks.profile_stop_reason(profile.profile_id, profile.revision) is None
    assert [work.task_id for work in supervisor._classify([second_task])] == [second_task.task_id]


def test_backfill_scan_logs_inventory_and_queue_without_content(tmp_path) -> None:
    stores = _setup(tmp_path)
    evidence, profiles, tasks, governance, secrets, _profile = stores
    marker = "private-scheduler-body-5521"
    for index in (1, 2):
        _put(
            evidence,
            message_id=f"u{index}",
            role="user",
            written_by=WrittenBy.PERSON,
            content=f"{marker}-{index}",
            conversation_id=f"conversation-{index}",
            content_kind=EvidenceContentKind.MEMORY_CONTENT,
        )
    root = logging.getLogger()
    previous = root.level
    root.setLevel(logging.DEBUG)
    receipt = configure_diagnostics(tmp_path / "logs")

    async def exercise() -> None:
        supervisor = MemoryProcessingSupervisor(
            tasks=tasks,
            profiles=profiles,
            secrets=secrets,
            evidence=evidence,
            governance=governance,
            data_dir=tmp_path,
            runner_factory=lambda: _runner(tmp_path, stores, FakeBridge(outputs=())),
        )
        await supervisor.backfill_off_thread()
        await supervisor.shutdown()

    try:
        asyncio.run(exercise())
    finally:
        reset_diagnostics()
        root.setLevel(previous)

    written = receipt.path.read_text()
    events = [json.loads(line) for line in written.splitlines()]
    by_name = {event["event"]: event for event in events}
    assert by_name["memory_scheduler.scan_inventory"]["details"]["pending_count"] == 2
    assert by_name["memory_scheduler.tasks_queued"]["details"]["prepared_count"] >= 1
    completed = by_name["memory_scheduler.scan_completed"]
    assert completed["details"]["prepared_count"] == 2
    assert completed["duration_ms"] >= 0
    assert "memory_scheduler.scan_started" in by_name
    assert marker not in written


def test_backfill_preparation_waits_while_realtime_model_work_is_active(tmp_path) -> None:
    stores = _setup(tmp_path)
    evidence, profiles, tasks, governance, secrets, _profile = stores
    scheduler = ServiceLevelScheduler()
    supervisor = MemoryProcessingSupervisor(
        tasks=tasks,
        profiles=profiles,
        secrets=secrets,
        evidence=evidence,
        governance=governance,
        data_dir=tmp_path,
        service_levels=scheduler,
    )
    advanced = threading.Event()

    def batches():
        advanced.set()
        yield []

    async def exercise() -> None:
        stop = threading.Event()
        async with scheduler.admit(
            ServiceResource.MODEL,
            ServiceLevel.REALTIME,
            work_id="live-turn",
        ):
            pending = asyncio.create_task(supervisor._next_backfill_batch(batches(), stop))
            await asyncio.sleep(0.05)
            assert not advanced.is_set()
        assert await asyncio.wait_for(pending, 1) == []
        assert advanced.is_set()

    asyncio.run(exercise())


def test_cold_start_prioritizes_all_apps_before_workspace(tmp_path) -> None:
    """The reason R-02 kept the all-apps preference instead of deleting it.

    Cold start wants profile-shaped evidence landing first, so this is still
    the rule between two pieces of evidence of the same age. What R-02
    changed is where it sits: it used to lead the sort key, which sent every
    conversation written while coding to the back whatever its age. Now it
    only breaks ties, and `test_memory_queue_recency.py` pins that time wins.
    """
    stores = _setup(tmp_path)
    evidence, profiles, tasks, governance, secrets, profile = stores
    personal = _put(
        evidence,
        message_id="personal",
        role="user",
        written_by=WrittenBy.PERSON,
        content="我是一名产品经理，关注长期个人偏好。",
        conversation_id="personal-conversation",
    )
    workspace = _put(
        evidence,
        message_id="workspace",
        role="user",
        written_by=WrittenBy.PERSON,
        content="修 bug。",
        conversation_id="workspace-conversation",
        workspace="workspace:v1:project",
    )
    personal_task = _task(tasks, profile, personal, suffix="personal")
    workspace_task = _task(tasks, profile, workspace, suffix="workspace")
    same_moment = datetime(2026, 9, 8, 9, 0, tzinfo=UTC)

    def order(task, record):
        return classify(
            task.task_id,
            at=same_moment,
            imported_at=record.imported_at,
            workspace=record.workspace,
            length=len(record.verbatim),
            now=same_moment,
        ).order

    assert order(personal_task, personal) < order(workspace_task, workspace)


@pytest.mark.xfail(
    strict=True,
    reason="D-16: superseded evidence does not retract its memory; remediation "
    "policy is an open product decision, so this pins the defect instead of "
    "hiding it. Fixing it must turn this into a plain assertion.",
)
def test_rewritten_turn_does_not_leave_the_old_claim_confirmed(tmp_path) -> None:
    """Editing an already-sent prompt must not leave the old claim standing.

    Cursor lets you rewrite a prompt you already sent, so the host revises the
    record in place. Re-extraction then runs on the new wording, but nothing
    revisits what the previous wording already turned into a confirmed memory,
    and recall serves confirmed memories as fact.
    """
    stores = _setup(tmp_path)
    evidence, _profiles, tasks, governance, _secrets, profile = stores
    original = _put(
        evidence,
        message_id="edited-turn",
        role="user",
        written_by=WrittenBy.PERSON,
        content="我平时用 Windows 开发。",
    )
    asyncio.run(
        _runner(
            tmp_path,
            stores,
            FakeBridge(
                outputs=(
                    HyMemoryOutput(
                        memory_id="hy-windows",
                        content="用户平时用 Windows 开发。",
                        layer="L0_BASIC_INFO",
                    ),
                )
            ),
        ).run(_task(tasks, profile, original).task_id)
    )

    rewritten = _put(
        evidence,
        message_id="edited-turn",
        role="user",
        written_by=WrittenBy.PERSON,
        content="我平时用 macOS 开发。",
    )
    assert rewritten.revision > original.revision

    asyncio.run(
        _runner(
            tmp_path,
            stores,
            FakeBridge(
                outputs=(
                    HyMemoryOutput(
                        memory_id="hy-macos",
                        content="用户平时用 macOS 开发。",
                        layer="L0_BASIC_INFO",
                    ),
                )
            ),
        ).run(_task(tasks, profile, rewritten, suffix="rewritten").task_id)
    )

    confirmed = {
        memory.content for memory in governance.list(user_id="local", status=MemoryStatus.CONFIRMED)
    }
    assert "用户平时用 macOS 开发。" in confirmed
    # The user never said this once they had edited the turn.
    assert "用户平时用 Windows 开发。" not in confirmed


def test_keychain_denial_leaves_extraction_waiting_not_running(tmp_path, monkeypatch) -> None:
    from one_memory.secrets import SecretStoreUnavailableError

    stores = _setup(tmp_path)
    evidence, _profiles, tasks, _governance, secrets, profile = stores
    seed = _put(
        evidence,
        message_id="keychain-denied",
        role="user",
        written_by=WrittenBy.PERSON,
        content="请保留这句话。",
    )
    task = _task(tasks, profile, seed)

    def unavailable(*_args):
        raise SecretStoreUnavailableError("authorization required")

    monkeypatch.setattr(_profiles, "resolve_snapshot_secret", unavailable)
    result = asyncio.run(_runner(tmp_path, stores, FakeBridge(outputs=())).run(task.task_id))
    assert result.status is MemoryTaskStatus.WAITING_FOR_MODEL
    assert result.last_error_code == "secure_storage_unavailable"
    assert evidence.get(seed.evidence_id).verbatim == "请保留这句话。"


def test_cancelled_debounce_does_not_remove_the_replacement_task(tmp_path) -> None:
    evidence, profiles, tasks, governance, secrets, _profile = _setup(tmp_path)
    seed = _put(
        evidence,
        message_id="debounce",
        role="user",
        written_by=WrittenBy.PERSON,
        content="同一会话连续到达的消息。",
    )
    bridge = FakeBridge(outputs=())
    supervisor = MemoryProcessingSupervisor(
        tasks=tasks,
        profiles=profiles,
        secrets=secrets,
        evidence=evidence,
        governance=governance,
        data_dir=tmp_path,
        runner_factory=lambda: MemoryTaskRunner(
            tasks=tasks,
            profiles=profiles,
            secrets=secrets,
            evidence=evidence,
            governance=governance,
            data_dir=tmp_path,
            bridge_factory=lambda _profile, _secret, _data_dir: bridge,
        ),
    )

    async def scenario():
        key = (seed.user_id, seed.source_id, seed.conversation_id, seed.workspace)
        supervisor.enqueue_evidence(seed)
        await asyncio.sleep(0)
        supervisor.enqueue_evidence(seed)
        replacement = supervisor._debounces[key]
        try:
            await asyncio.sleep(0)
            assert supervisor._debounces.get(key) is replacement
        finally:
            replacement.cancel()
            await asyncio.gather(replacement, return_exceptions=True)
            await supervisor.shutdown()

    asyncio.run(scenario())


def test_workspace_attribution_change_creates_a_new_ingest_task(tmp_path) -> None:
    stores = _setup(tmp_path)
    evidence, profiles, tasks, governance, secrets, profile = stores
    original = _put(
        evidence,
        message_id="workspace-later",
        role="user",
        written_by=WrittenBy.PERSON,
        content="项目里的长期决定。",
    )
    supervisor = MemoryProcessingSupervisor(
        tasks=tasks,
        profiles=profiles,
        secrets=secrets,
        evidence=evidence,
        governance=governance,
        data_dir=tmp_path,
    )
    first = supervisor._enqueue_chunk((original,), profile)
    attributed = _put(
        evidence,
        message_id="workspace-later",
        role="user",
        written_by=WrittenBy.PERSON,
        content="项目里的长期决定。",
        workspace="workspace:v1:project",
    )
    second = supervisor._enqueue_chunk((attributed,), profile)

    assert second.task_id != first.task_id
    assert second.request_id != first.request_id


def test_debounce_keeps_two_workspaces_with_one_conversation_id_apart(tmp_path) -> None:
    """The later project's arrival must not cancel the earlier project's timer."""
    stores = _setup(tmp_path)
    evidence, profiles, tasks, governance, secrets, _profile = stores
    first = _put(
        evidence,
        message_id="debounce-a",
        role="user",
        written_by=WrittenBy.PERSON,
        content="项目 A 的消息。",
        conversation_id="same-conversation",
        workspace="workspace:v1:project-a",
    )
    second = _put(
        evidence,
        message_id="debounce-b",
        role="user",
        written_by=WrittenBy.PERSON,
        content="项目 B 的消息。",
        conversation_id="same-conversation",
        workspace="workspace:v1:project-b",
    )
    supervisor = MemoryProcessingSupervisor(
        tasks=tasks,
        profiles=profiles,
        secrets=secrets,
        evidence=evidence,
        governance=governance,
        data_dir=tmp_path,
    )
    first_key = (first.user_id, first.source_id, first.conversation_id, first.workspace)
    second_key = (second.user_id, second.source_id, second.conversation_id, second.workspace)

    async def scenario():
        supervisor.enqueue_evidence(first)
        await asyncio.sleep(0)
        first_pending = supervisor._debounces[first_key]
        supervisor.enqueue_evidence(second)
        await asyncio.sleep(0)
        try:
            assert supervisor._debounces[first_key] is first_pending
            assert supervisor._debounces[second_key] is not first_pending
        finally:
            await supervisor.shutdown()

    asyncio.run(scenario())


def test_memory_task_budget_includes_pipeline_overhead() -> None:
    assert memory_task_timeout_seconds(30) == 180
    assert memory_task_timeout_seconds(240) == 300


def test_process_records_bounds_a_hung_model_call_by_the_profile_timeout(
    tmp_path, monkeypatch
) -> None:
    """The direct `process_records` entry must bound one hung task.

    The durable drain calls `process_records`, not the scheduler. That entry
    used to call `runner.run(task_id)` with no `timeout`, while the scheduler
    path passed the profile's `timeout_seconds`. A hung model call therefore
    held the single harness slot until the process died, and the drain's lease
    renewal kept extending a job that could never finish.

    Here the profile timeout is 1s and the bridge sleeps far longer, so the
    call must come back with `hy_memory_timeout` instead of blocking.
    """
    monkeypatch.setattr("one_memory.memory_processing.MEMORY_TASK_TIMEOUT_MIN_SECONDS", 1.0)
    monkeypatch.setattr("one_memory.memory_processing.MEMORY_TASK_TIMEOUT_MARGIN_SECONDS", 0.0)
    stores = _setup(tmp_path)
    evidence, profiles, tasks, governance, secrets, profile = stores
    record = _put(
        evidence,
        message_id="hang-1",
        role="user",
        written_by=WrittenBy.PERSON,
        content="这个调用会挂住。",
    )
    reply = _put(
        evidence,
        message_id="hang-2",
        role="assistant",
        written_by=WrittenBy.AGENT,
        content="收到。",
    )
    with sqlite3.connect(tmp_path / "one-memory.sqlite3") as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS source_grants (
                user_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                root_path TEXT NOT NULL,
                candidate_kind TEXT NOT NULL,
                granted_at TEXT NOT NULL,
                revoked_at TEXT,
                PRIMARY KEY (user_id, source_id)
            );
            """
        )
        db.execute(
            "INSERT INTO source_grants VALUES ('local','cursor','/authorized','default',?,NULL)",
            (datetime.now(UTC).isoformat(),),
        )

    service_levels = ServiceLevelScheduler()

    class HangingBridge(FakeBridge):
        async def ingest(self, request):
            assert service_levels.should_yield(ServiceResource.IMPORT, ServiceLevel.BACKGROUND)
            await asyncio.sleep(30)
            return await super().ingest(request)

    # A 1s ceiling so the test does not wait on the 240s default.
    updated = profiles.update(
        profile.profile_id,
        ModelProfilePatch(expected_revision=profile.revision, timeout_seconds=1),
        secrets,
    )
    profiles.record_capabilities(
        updated.profile_id,
        expected_revision=updated.revision,
        capabilities=ModelCapabilities(
            endpoint_reachable=True,
            model_available=True,
            structured_output=True,
            embedding_available=True,
        ),
        secrets=secrets,
    )
    profile = profiles.activate(updated.profile_id, secrets)
    supervisor = MemoryProcessingSupervisor(
        tasks=tasks,
        profiles=profiles,
        secrets=secrets,
        evidence=evidence,
        governance=governance,
        data_dir=tmp_path,
        runner_factory=lambda: _runner(tmp_path, stores, HangingBridge()),
        service_levels=service_levels,
    )

    started = time.monotonic()
    with pytest.raises(HyMemoryBridgeError, match="hy_memory_timeout"):
        asyncio.run(supervisor.process_records((record, reply)))
    elapsed = time.monotonic() - started
    assert elapsed < 10, f"hung call was not bounded by the profile timeout ({elapsed:.1f}s)"


def test_authorized_sources_prewarm_write_and_read_bridges_once(tmp_path) -> None:
    stores = _setup(tmp_path)
    evidence, profiles, tasks, governance, secrets, profile = stores
    created = []

    class WarmBridge(FakeBridge):
        def __init__(self):
            super().__init__()
            self.warmed = 0

        async def warm(self) -> None:
            self.warmed += 1

    def factory(*_args):
        bridge = WarmBridge()
        created.append(bridge)
        return bridge

    supervisor = MemoryProcessingSupervisor(
        tasks=tasks,
        profiles=profiles,
        secrets=secrets,
        evidence=evidence,
        governance=governance,
        data_dir=tmp_path,
        bridge_factory=factory,
    )

    asyncio.run(supervisor._prewarm_write_bridges(("cursor", "workbuddy")))
    write_bridge = supervisor._bridge(profile, None, tmp_path)
    read_bridge = supervisor._read_bridge(profile, None, tmp_path)
    assert created == [write_bridge, read_bridge]
    assert [bridge.warmed for bridge in created] == [1, 1]


def test_process_records_does_not_create_a_task_for_a_tool_only_delivery(tmp_path) -> None:
    """A tool event is context for its turn, never a standalone HY turn."""
    stores = _setup(tmp_path)
    evidence, profiles, tasks, governance, secrets, _profile = stores
    tool = _put(
        evidence,
        message_id="tool-only",
        role="tool",
        written_by=WrittenBy.TOOL,
        content="tool output retained as original evidence",
    )
    supervisor = MemoryProcessingSupervisor(
        tasks=tasks,
        profiles=profiles,
        secrets=secrets,
        evidence=evidence,
        governance=governance,
        data_dir=tmp_path,
    )

    assert asyncio.run(supervisor.process_records((tool,))) == ()
    assert tasks.list() == []


def test_oversized_single_turn_has_persistent_semantic_segments_after_restart(tmp_path) -> None:
    stores = _setup(tmp_path)
    evidence, profiles, tasks, governance, secrets, _profile = stores
    paragraphs = [f"第 {index} 段。" + (chr(65 + index) * 4_500) for index in range(3)]
    original = "\n\n".join(paragraphs)
    record = _put(
        evidence,
        message_id="oversized-segmented",
        role="user",
        written_by=WrittenBy.PERSON,
        content=original,
    )
    _put(
        evidence,
        message_id="oversized-segmented-answer",
        role="assistant",
        written_by=WrittenBy.AGENT,
        content="已收到这份长内容。",
    )
    with sqlite3.connect(tasks.database_path) as connection, connection:
        connection.execute(
            """
            CREATE TABLE source_grants (
                user_id TEXT NOT NULL, source_id TEXT NOT NULL,
                root_path TEXT NOT NULL, candidate_kind TEXT NOT NULL,
                granted_at TEXT NOT NULL, revoked_at TEXT,
                PRIMARY KEY (user_id, source_id)
            )
            """
        )
        connection.execute(
            "INSERT INTO source_grants VALUES (?,?,?,?,?,NULL)",
            (
                record.user_id,
                record.source_id,
                "/authorized",
                "default",
                datetime.now(UTC).isoformat(),
            ),
        )
    supervisor = MemoryProcessingSupervisor(
        tasks=tasks,
        profiles=profiles,
        secrets=secrets,
        evidence=evidence,
        governance=governance,
        data_dir=tmp_path,
    )

    prepared = supervisor.prepare_conversation(record)

    assert len(prepared) == 2
    assert all(task.status is MemoryTaskStatus.PENDING for task in prepared)
    refs = [tasks.inputs(task.task_id)[0] for task in prepared]
    assert [ref["segment_index"] for ref in refs] == [0, 1]
    assert all(ref["segment_hash"] for ref in refs)
    restarted = MemoryTaskStore(tasks.database_path)
    runner = MemoryTaskRunner(
        tasks=restarted,
        profiles=profiles,
        secrets=secrets,
        evidence=evidence,
        governance=governance,
        data_dir=tmp_path,
    )
    segments = [runner._frozen_chunk(task)[0].verbatim for task in prepared]
    assert "".join(segments) == original
    assert all(len(segment) <= 12_000 for segment in segments)


def test_oversized_single_turn_segments_are_each_processed(tmp_path) -> None:
    stores = _setup(tmp_path)
    evidence, profiles, tasks, governance, secrets, _profile = stores
    original = ("第一段内容。" + "甲" * 7_000) + "\n\n" + ("第二段内容。" + "乙" * 7_000)
    record = _put(
        evidence,
        message_id="oversized-run",
        role="user",
        written_by=WrittenBy.PERSON,
        content=original,
        content_kind=EvidenceContentKind.MEMORY_CONTENT,
    )
    with sqlite3.connect(tasks.database_path) as connection, connection:
        connection.execute(
            """
            CREATE TABLE source_grants (
                user_id TEXT NOT NULL, source_id TEXT NOT NULL,
                root_path TEXT NOT NULL, candidate_kind TEXT NOT NULL,
                granted_at TEXT NOT NULL, revoked_at TEXT,
                PRIMARY KEY (user_id, source_id)
            )
            """
        )
        connection.execute(
            "INSERT INTO source_grants VALUES (?,?,?,?,?,NULL)",
            (
                record.user_id,
                record.source_id,
                "/authorized",
                "default",
                datetime.now(UTC).isoformat(),
            ),
        )

    class SegmentBridge(FakeBridge):
        async def ingest_extracted(self, request, contents) -> HyMemoryIngestResult:
            self.requests.append(request)
            self.extracted_contents.append(tuple(contents))
            return HyMemoryIngestResult(
                success=True,
                memories=(
                    HyMemoryOutput(
                        memory_id=f"segment-{len(self.requests)}",
                        content=f"第 {len(self.requests)} 段已整理",
                        layer="L2_FACT",
                    ),
                ),
            )

    bridge = SegmentBridge()
    supervisor = MemoryProcessingSupervisor(
        tasks=tasks,
        profiles=profiles,
        secrets=secrets,
        evidence=evidence,
        governance=governance,
        data_dir=tmp_path,
        runner_factory=lambda: _runner(tmp_path, stores, bridge),
    )

    completed = asyncio.run(supervisor.process_records((record,)))

    assert len(completed) == 2
    assert all(task.status is MemoryTaskStatus.COMPLETED for task in completed)
    sent = [request.turns[0].content for request in bridge.requests]
    assert "".join(sent) == original
    assert len(governance.list(user_id="local")) == 2


def test_direct_processing_uses_the_supplied_frozen_revision(tmp_path) -> None:
    stores = _setup(tmp_path)
    evidence, profiles, tasks, governance, secrets, _profile = stores
    old_user = _put(
        evidence,
        message_id="rewound-user",
        role="user",
        written_by=WrittenBy.PERSON,
        content="old branch",
    )
    reply = _put(
        evidence,
        message_id="rewound-reply",
        role="assistant",
        written_by=WrittenBy.AGENT,
        content="old reply",
    )
    revised = _put(
        evidence,
        message_id="rewound-user",
        role="user",
        written_by=WrittenBy.PERSON,
        content="new branch",
    )
    assert revised.evidence_id == old_user.evidence_id
    assert revised.revision == old_user.revision + 1
    with sqlite3.connect(tasks.database_path) as connection, connection:
        connection.execute(
            """
            CREATE TABLE source_grants (
                user_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                root_path TEXT NOT NULL,
                requested_path TEXT NOT NULL,
                root_identity TEXT NOT NULL,
                candidate_kind TEXT NOT NULL,
                granted_at TEXT NOT NULL,
                revoked_at TEXT,
                revision INTEGER NOT NULL,
                PRIMARY KEY (user_id, source_id)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO source_grants (
                user_id,source_id,root_path,requested_path,root_identity,
                candidate_kind,granted_at,revoked_at,revision
            ) VALUES (?,?,?,?,?,?,?,NULL,1)
            """,
            (
                "local",
                "cursor",
                "/authorized",
                "/authorized",
                "test-root",
                "default",
                datetime.now(UTC).isoformat(),
            ),
        )
    bridge = FakeBridge(outputs=())
    supervisor = MemoryProcessingSupervisor(
        tasks=tasks,
        profiles=profiles,
        secrets=secrets,
        evidence=evidence,
        governance=governance,
        data_dir=tmp_path,
        runner_factory=lambda: _runner(tmp_path, stores, bridge),
    )

    completed = asyncio.run(supervisor.process_records((old_user, reply)))

    assert len(completed) == 1
    assert [turn.content for turn in bridge.requests[0].turns] == [
        "old branch",
        "old reply",
    ]


def test_frozen_current_still_revalidates_history_after_the_model_call(tmp_path) -> None:
    from one_memory.hy_source_context import HySourceContext

    stores = _setup(tmp_path)
    evidence, _profiles, tasks, _governance, _secrets, _profile = stores
    current = _put(
        evidence,
        message_id="frozen-current",
        role="user",
        written_by=WrittenBy.PERSON,
        content="old current",
    )
    history = _put(
        evidence,
        message_id="frozen-history",
        role="assistant",
        written_by=WrittenBy.AGENT,
        content="stable history",
    )
    _put(
        evidence,
        message_id="frozen-current",
        role="user",
        written_by=WrittenBy.PERSON,
        content="new current",
    )
    runner = _runner(tmp_path, stores, FakeBridge(outputs=()))
    context = HySourceContext(current=(current,), history=(history,))

    runner._check_source_context(context, skip_current=True)
    with sqlite3.connect(tasks.database_path) as connection, connection:
        connection.execute(
            "DELETE FROM evidence WHERE evidence_id=?",
            (history.evidence_id,),
        )

    with pytest.raises(HyMemoryBridgeError, match="hy_memory_evidence_changed"):
        runner._check_source_context(context, skip_current=True)


def test_oversized_assistant_segments_keep_the_question_as_context(tmp_path) -> None:
    evidence = EvidenceStore(tmp_path / "one-memory.sqlite3")
    question = _put(
        evidence,
        message_id="segment-question",
        role="user",
        written_by=WrittenBy.PERSON,
        content="请整理下面这份很长的方案。",
    )
    answer_text = ("方案第一部分。" + "甲" * 7_000) + "\n\n" + ("方案第二部分。" + "乙" * 7_000)
    answer = _put(
        evidence,
        message_id="segment-answer",
        role="assistant",
        written_by=WrittenBy.AGENT,
        content=answer_text,
    )

    chunks = _conversation_chunks([question, answer])

    assert len(chunks) == 2
    assert all(chunk[0].evidence_id == question.evidence_id for chunk in chunks)
    assert all(sum(len(record.verbatim) for record in chunk) <= 12_000 for chunk in chunks)
    assert "".join(chunk[-1].verbatim for chunk in chunks) == answer_text


def test_semantic_search_uses_a_bridge_independent_from_active_writes(tmp_path) -> None:
    stores = _setup(tmp_path)
    evidence, profiles, tasks, governance, secrets, profile = stores
    made: list[FakeBridge] = []

    def factory(_profile, _secret, _data_dir):
        bridge = FakeBridge()
        bridge.search_result_ids = (f"read-{len(made)}",)
        made.append(bridge)
        return bridge

    supervisor = MemoryProcessingSupervisor(
        tasks=tasks,
        profiles=profiles,
        secrets=secrets,
        evidence=evidence,
        governance=governance,
        data_dir=tmp_path,
        bridge_factory=factory,
    )
    snapshot = profiles.snapshot(profile.profile_id, profile.revision)
    write_bridge = supervisor._bridge(snapshot, None, tmp_path)

    assert asyncio.run(supervisor.semantic_search("独立读取")) == ()
    assert len(made) == 1

    asyncio.run(supervisor._prewarm_write_bridges(("cursor",)))  # noqa: SLF001
    found = asyncio.run(supervisor.semantic_search("独立读取"))

    assert found == (("read-1", 0.9),)
    assert len(made) == 2
    assert supervisor._read_bridges[(profile.profile_id, profile.revision)] is not write_bridge


def test_completed_write_refreshes_the_prewarmed_semantic_read_bridge(tmp_path) -> None:
    stores = _setup(tmp_path)
    evidence, profiles, tasks, governance, secrets, profile = stores
    record = _put(
        evidence,
        message_id="semantic-refresh",
        role="user",
        written_by=WrittenBy.PERSON,
        content="用户偏爱蓝莓。",
    )
    answer = _put(
        evidence,
        message_id="semantic-refresh-answer",
        role="assistant",
        written_by=WrittenBy.AGENT,
        content="我记下了。",
    )
    with sqlite3.connect(tasks.database_path) as connection, connection:
        connection.execute(
            """
            CREATE TABLE source_grants (
                user_id TEXT NOT NULL, source_id TEXT NOT NULL,
                root_path TEXT NOT NULL, candidate_kind TEXT NOT NULL,
                granted_at TEXT NOT NULL, revoked_at TEXT,
                PRIMARY KEY (user_id, source_id)
            )
            """
        )
        connection.execute(
            "INSERT INTO source_grants VALUES (?,?,?,?,?,NULL)",
            (
                record.user_id,
                record.source_id,
                "/authorized",
                "default",
                datetime.now(UTC).isoformat(),
            ),
        )
    generation = 0
    made: list[FakeBridge] = []

    class SnapshotReadBridge(FakeBridge):
        def __init__(self) -> None:
            super().__init__()
            self.visible_generation = -1

        async def warm(self) -> None:
            self.visible_generation = generation

        async def search_scores(self, *_args, **_kwargs) -> tuple[object, ...]:
            return (("hy-memory-1", 0.9),) if self.visible_generation >= 1 else ()

    class WritingBridge(FakeBridge):
        async def ingest(self, request) -> HyMemoryIngestResult:
            nonlocal generation
            generation += 1
            return await super().ingest(request)

    def factory(*_args):
        bridge = SnapshotReadBridge()
        made.append(bridge)
        return bridge

    supervisor = MemoryProcessingSupervisor(
        tasks=tasks,
        profiles=profiles,
        secrets=secrets,
        evidence=evidence,
        governance=governance,
        data_dir=tmp_path,
        bridge_factory=factory,
        runner_factory=lambda: _runner(tmp_path, stores, WritingBridge()),
    )

    async def exercise() -> None:
        await supervisor._prewarm_write_bridges(("workbuddy",))  # noqa: SLF001
        old_read = supervisor._read_bridges[(profile.profile_id, profile.revision)]
        assert await supervisor.semantic_search("偏爱哪种蓝色小浆果") == ()

        completed = await supervisor.process_records((record, answer))

        assert completed[0].status is MemoryTaskStatus.COMPLETED
        assert await supervisor.semantic_search("偏爱哪种蓝色小浆果") == (("hy-memory-1", 0.9),)
        assert supervisor._read_bridges[(profile.profile_id, profile.revision)] is not old_read
        assert old_read.closed is True

    asyncio.run(exercise())
    assert len(made) == 3


def test_failed_semantic_read_refresh_keeps_the_ready_bridge(tmp_path) -> None:
    stores = _setup(tmp_path)
    _evidence, profiles, tasks, governance, secrets, profile = stores
    made: list[FakeBridge] = []

    class RefreshBridge(FakeBridge):
        async def warm(self) -> None:
            if len(made) == 3:
                raise RuntimeError("new view is unavailable")

    def factory(*_args):
        bridge = RefreshBridge()
        bridge.search_result_ids = (f"read-{len(made)}",)
        made.append(bridge)
        return bridge

    supervisor = MemoryProcessingSupervisor(
        tasks=tasks,
        profiles=profiles,
        secrets=secrets,
        evidence=stores[0],
        governance=governance,
        data_dir=tmp_path,
        bridge_factory=factory,
    )

    async def exercise() -> None:
        await supervisor._prewarm_write_bridges(("workbuddy",))  # noqa: SLF001
        old_read = supervisor._read_bridges[(profile.profile_id, profile.revision)]

        await supervisor._refresh_read_bridge(profile)  # noqa: SLF001

        assert supervisor._read_bridges[(profile.profile_id, profile.revision)] is old_read
        assert await supervisor.semantic_search("偏爱哪种蓝色小浆果") == (("read-1", 0.9),)
        assert old_read.closed is False
        assert made[-1].closed is True

    asyncio.run(exercise())


def test_background_read_refresh_coalesces_a_write_burst(tmp_path, monkeypatch) -> None:
    stores = _setup(tmp_path)
    _evidence, profiles, tasks, governance, secrets, profile = stores
    made: list[FakeBridge] = []

    def factory(*_args):
        bridge = FakeBridge()
        made.append(bridge)
        return bridge

    supervisor = MemoryProcessingSupervisor(
        tasks=tasks,
        profiles=profiles,
        secrets=secrets,
        evidence=stores[0],
        governance=governance,
        data_dir=tmp_path,
        bridge_factory=factory,
    )
    monkeypatch.setattr(
        "one_memory.memory_processing._READ_BRIDGE_FIRST_REFRESH_DELAY_SECONDS", 0.01
    )

    async def exercise() -> None:
        await supervisor._prewarm_write_bridges(("workbuddy",))  # noqa: SLF001
        for _ in range(20):
            supervisor._request_read_bridge_refresh(profile)  # noqa: SLF001
        await asyncio.sleep(0.05)

        assert supervisor._read_bridge_generations[(profile.profile_id, profile.revision)] == 1
        assert len(made) == 3
        assert sum(bridge.closed for bridge in made) == 1
        await supervisor.shutdown()

    asyncio.run(exercise())


def test_background_read_refresh_does_not_rebuild_for_later_writes(tmp_path, monkeypatch) -> None:
    stores = _setup(tmp_path)
    _evidence, profiles, tasks, governance, secrets, profile = stores
    collection_generation = 0
    made: list[FakeBridge] = []

    class PersistentCollectionBridge(FakeBridge):
        def __init__(self) -> None:
            super().__init__()
            self.collection_attached = False

        async def warm(self) -> None:
            self.collection_attached = collection_generation > 0

        async def search_scores(self, *_args, **_kwargs) -> tuple[object, ...]:
            if not self.collection_attached:
                return ()
            return ((f"memory-{collection_generation}", 0.9),)

    def factory(*_args):
        bridge = PersistentCollectionBridge()
        made.append(bridge)
        return bridge

    supervisor = MemoryProcessingSupervisor(
        tasks=tasks,
        profiles=profiles,
        secrets=secrets,
        evidence=stores[0],
        governance=governance,
        data_dir=tmp_path,
        bridge_factory=factory,
    )
    monkeypatch.setattr(
        "one_memory.memory_processing._READ_BRIDGE_FIRST_REFRESH_DELAY_SECONDS", 0.01
    )

    async def exercise() -> None:
        nonlocal collection_generation
        await supervisor._prewarm_write_bridges(("workbuddy",))  # noqa: SLF001
        assert await supervisor.semantic_search("首次写入") == ()
        collection_generation = 1
        supervisor._request_read_bridge_refresh(profile)  # noqa: SLF001
        await asyncio.sleep(0.05)
        assert await supervisor.semantic_search("首次写入") == (("memory-1", 0.9),)
        collection_generation = 2
        for _ in range(20):
            supervisor._request_read_bridge_refresh(profile)  # noqa: SLF001
        await asyncio.sleep(0.05)

        assert supervisor._read_bridge_generations[(profile.profile_id, profile.revision)] == 1
        assert len(made) == 3
        assert await supervisor.semantic_search("后续写入") == (("memory-2", 0.9),)
        await supervisor.shutdown()

    asyncio.run(exercise())


def test_unknown_task_is_reconciled_from_exact_hy_output_without_model_retry(tmp_path) -> None:
    stores = _setup(tmp_path)
    evidence, profiles, tasks, governance, secrets, profile = stores
    record = _put(
        evidence,
        message_id="unknown-reconcile",
        role="user",
        written_by=WrittenBy.PERSON,
        content="用户偏好每周日上午攀岩。",
    )
    task = tasks.create(
        request_id="unknown-reconcile-request",
        user_id=record.user_id,
        kind=MemoryTaskKind.EXTRACT,
        input_ref=record.evidence_id,
        profile=profile,
        input_records=(record,),
    )
    with sqlite3.connect(tasks.database_path) as connection, connection:
        connection.execute(
            """
            CREATE TABLE source_grants (
                user_id TEXT NOT NULL, source_id TEXT NOT NULL,
                root_path TEXT NOT NULL, candidate_kind TEXT NOT NULL,
                granted_at TEXT NOT NULL, revoked_at TEXT,
                PRIMARY KEY (user_id, source_id)
            )
            """
        )
        connection.execute(
            "INSERT INTO source_grants VALUES (?,?,?,?,?,NULL)",
            (
                record.user_id,
                record.source_id,
                "/authorized",
                "default",
                datetime.now(UTC).isoformat(),
            ),
        )
        connection.execute(
            "UPDATE memory_tasks SET status='unknown', last_error_code=? WHERE task_id=?",
            ("model_content_outcome_unknown", task.task_id),
        )
    bridge = FakeBridge(
        outputs=(
            HyMemoryOutput(
                memory_id="hy-recovered",
                content="用户偏好每周日上午攀岩。",
                layer="L2_FACT",
                owner="user",
                evidence_ids=(record.evidence_id,),
            ),
        )
    )
    supervisor = MemoryProcessingSupervisor(
        tasks=tasks,
        profiles=profiles,
        secrets=secrets,
        evidence=evidence,
        governance=governance,
        data_dir=tmp_path,
        runner_factory=lambda: _runner(tmp_path, stores, FakeBridge()),
        bridge_factory=lambda _profile, _secret, _data_dir: bridge,
    )

    assert asyncio.run(supervisor.reconcile_unknown_outputs()) == 1
    assert tasks.require(task.task_id).status is MemoryTaskStatus.COMPLETED
    assert [memory.memory_id for memory in governance.list(user_id="local")] == ["hy-recovered"]
    assert bridge.closed is True


def test_an_unknown_task_without_inputs_does_not_end_the_reconcile_pass(tmp_path) -> None:
    """One task with nothing to send must not strand the other unknown tasks.

    An unknown task whose inputs are gone yields an empty chunk, and building a
    request from that reached `chunk[0]` and came out as IndexError. A
    reconcile pass skips a task that raises a domain error, so IndexError did
    not skip anything -- it ended the pass. On this machine 68 of 303 unknown
    tasks had no inputs, and while the retry backed off to 128 and gave up, the
    235 that could have been reconciled stayed unknown with them.
    """
    stores = _setup(tmp_path)
    evidence, profiles, tasks, governance, secrets, profile = stores
    stranded = _put(
        evidence,
        message_id="reconcile-without-inputs",
        role="user",
        written_by=WrittenBy.PERSON,
        content="这条任务的输入已经不在了。",
    )
    record = _put(
        evidence,
        message_id="reconcile-with-inputs",
        role="user",
        written_by=WrittenBy.PERSON,
        content="用户偏好每周日上午攀岩。",
    )
    empty = tasks.create(
        request_id="reconcile-empty-request",
        user_id=stranded.user_id,
        kind=MemoryTaskKind.EXTRACT,
        input_ref=stranded.evidence_id,
        profile=profile,
        input_records=(stranded,),
    )
    task = tasks.create(
        request_id="reconcile-kept-request",
        user_id=record.user_id,
        kind=MemoryTaskKind.EXTRACT,
        input_ref=record.evidence_id,
        profile=profile,
        input_records=(record,),
    )
    with sqlite3.connect(tasks.database_path) as connection, connection:
        connection.execute(
            """
            CREATE TABLE source_grants (
                user_id TEXT NOT NULL, source_id TEXT NOT NULL,
                root_path TEXT NOT NULL, candidate_kind TEXT NOT NULL,
                granted_at TEXT NOT NULL, revoked_at TEXT,
                PRIMARY KEY (user_id, source_id)
            )
            """
        )
        connection.execute(
            "INSERT INTO source_grants VALUES (?,?,?,?,?,NULL)",
            (
                record.user_id,
                record.source_id,
                "/authorized",
                "default",
                datetime.now(UTC).isoformat(),
            ),
        )
        for task_id in (empty.task_id, task.task_id):
            connection.execute(
                "UPDATE memory_tasks SET status='unknown', last_error_code=? WHERE task_id=?",
                ("model_content_outcome_unknown", task_id),
            )
        # What the field data actually looks like: the task is there, its
        # inputs are not.
        connection.execute("DELETE FROM memory_task_inputs WHERE task_id=?", (empty.task_id,))
    bridge = FakeBridge(
        outputs=(
            HyMemoryOutput(
                memory_id="hy-recovered",
                content="用户偏好每周日上午攀岩。",
                layer="L2_FACT",
                owner="user",
                evidence_ids=(record.evidence_id,),
            ),
        )
    )
    supervisor = MemoryProcessingSupervisor(
        tasks=tasks,
        profiles=profiles,
        secrets=secrets,
        evidence=evidence,
        governance=governance,
        data_dir=tmp_path,
        runner_factory=lambda: _runner(tmp_path, stores, FakeBridge()),
        bridge_factory=lambda _profile, _secret, _data_dir: bridge,
    )

    # The task with inputs is reconciled; the one without is passed over.
    assert asyncio.run(supervisor.reconcile_unknown_outputs()) == 1
    assert tasks.require(task.task_id).status is MemoryTaskStatus.COMPLETED
    assert [memory.memory_id for memory in governance.list(user_id="local")] == ["hy-recovered"]


def test_two_workspaces_with_one_conversation_id_prepare_apart(tmp_path) -> None:
    """Cursor can reuse a conversation id across projects.

    `process_records` and the live debounce key must carry workspace with the
    source/conversation identity. Without it, two project copies collapse into
    one model input and the later arrival cancels the earlier project's timer.
    """
    stores = _setup(tmp_path)
    evidence, profiles, tasks, governance, secrets, profile = stores
    first_user = _put(
        evidence,
        message_id="same-user",
        role="user",
        written_by=WrittenBy.PERSON,
        content="项目 A 的决定。",
        conversation_id="same-conversation",
        workspace="workspace:v1:project-a",
    )
    first_reply = _put(
        evidence,
        message_id="same-reply",
        role="assistant",
        written_by=WrittenBy.AGENT,
        content="项目 A 已记录。",
        conversation_id="same-conversation",
        workspace="workspace:v1:project-a",
    )
    second_user = _put(
        evidence,
        message_id="second-user",
        role="user",
        written_by=WrittenBy.PERSON,
        content="项目 B 的决定。",
        conversation_id="same-conversation",
        workspace="workspace:v1:project-b",
    )
    second_reply = _put(
        evidence,
        message_id="second-reply",
        role="assistant",
        written_by=WrittenBy.AGENT,
        content="项目 B 已记录。",
        conversation_id="same-conversation",
        workspace="workspace:v1:project-b",
    )
    with sqlite3.connect(tmp_path / "one-memory.sqlite3") as db:
        db.executescript(
            """
            CREATE TABLE source_grants (
                user_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                root_path TEXT NOT NULL,
                candidate_kind TEXT NOT NULL,
                granted_at TEXT NOT NULL,
                revoked_at TEXT,
                PRIMARY KEY (user_id, source_id)
            );
            """
        )
        db.execute(
            "INSERT INTO source_grants VALUES ('local','cursor','/authorized','default',?,NULL)",
            (datetime.now(UTC).isoformat(),),
        )
    bridge = FakeBridge(outputs=())
    supervisor = MemoryProcessingSupervisor(
        tasks=tasks,
        profiles=profiles,
        secrets=secrets,
        evidence=evidence,
        governance=governance,
        data_dir=tmp_path,
        runner_factory=lambda: _runner(tmp_path, stores, bridge),
    )

    created = asyncio.run(
        supervisor.process_records((first_user, first_reply, second_user, second_reply))
    )
    for task in created:
        assert task.status is MemoryTaskStatus.COMPLETED

    assert len(created) == 2
    assert {
        tuple(record.evidence_id for record in request.source_context.current)
        for request in bridge.requests
        if request.source_context is not None
    } == {
        (first_user.evidence_id, first_reply.evidence_id),
        (second_user.evidence_id, second_reply.evidence_id),
    }


def _growing_conversation(tmp_path):
    stores = _setup(tmp_path)
    evidence, profiles, tasks, governance, secrets, profile = stores
    ask = _put(
        evidence,
        message_id="ask",
        role="user",
        written_by=WrittenBy.PERSON,
        content="记住这个决定。",
    )
    first_reply = _put(
        evidence,
        message_id="reply-1",
        role="assistant",
        written_by=WrittenBy.AGENT,
        content="好的，第一步。",
    )
    second_reply = _put(
        evidence,
        message_id="reply-2",
        role="assistant",
        written_by=WrittenBy.AGENT,
        content="第二步完成。",
    )
    supervisor = MemoryProcessingSupervisor(
        tasks=tasks,
        profiles=profiles,
        secrets=secrets,
        evidence=evidence,
        governance=governance,
        data_dir=tmp_path,
    )
    return supervisor, tasks, profile, ask, first_reply, second_reply


def test_a_grown_chunk_retires_the_unstarted_task_for_the_same_turn(tmp_path) -> None:
    supervisor, tasks, profile, ask, first_reply, second_reply = _growing_conversation(tmp_path)

    shorter = supervisor._enqueue_chunk((ask, first_reply), profile, freeze_inputs=True)
    longer = supervisor._enqueue_chunk(
        (ask, first_reply, second_reply), profile, freeze_inputs=True
    )

    assert longer.task_id != shorter.task_id
    retired = tasks.require(shorter.task_id)
    assert retired.status is MemoryTaskStatus.CANCELLED
    assert retired.last_error_code == "superseded_by_longer_chunk"
    assert tasks.require(longer.task_id).status is MemoryTaskStatus.PENDING


@pytest.mark.parametrize("operation", ["history", "sync"])
def test_import_waits_for_the_file_boundary_before_queuing_a_turn(tmp_path, operation) -> None:
    stores = _setup(tmp_path)
    evidence, profiles, tasks, governance, secrets, _profile = stores
    jobs = ImportJobStore(evidence.database_path)
    supervisor = MemoryProcessingSupervisor(
        tasks=tasks,
        profiles=profiles,
        secrets=secrets,
        evidence=evidence,
        governance=governance,
        data_dir=tmp_path,
    )
    file_key = "f" * 64

    def scan_cursor(checkpoint: str, *, read_to_end: bool = False) -> SourceCursor:
        return SourceCursor(
            file_key=file_key,
            file_identity="fixture:1",
            checkpoint=checkpoint,
            observed_size=200,
            observed_modified_ns=1,
            read_to_end=read_to_end,
        )

    class Adapter:
        descriptor = SourceDescriptor(
            source_id="cursor",
            display_name="Cursor",
            storage_kinds=frozenset({StorageKind.JSONL}),
            capabilities=SourceCapabilities(
                can_append=True,
                can_observe_edits=True,
                can_observe_deletes=False,
                can_reconcile_complete_set=False,
            ),
        )

        def __init__(self, events) -> None:
            self.events = events

        async def scan(self, _options):
            for event in self.events:
                yield event

    user = EvidenceInput(
        source_id="cursor",
        conversation_id="stable-turn",
        message_id="user-1",
        role="user",
        written_by=WrittenBy.PERSON,
        verbatim="把完整一轮对话沉淀成记忆。",
        origin_meta={EvidenceOrigin.FILE: OriginMetadata(raw_meta={"file_key": file_key})},
    )
    assistant = EvidenceInput(
        source_id="cursor",
        conversation_id="stable-turn",
        message_id="assistant-1",
        role="assistant",
        written_by=WrittenBy.AGENT,
        verbatim="会等到回答完整后再开始。",
        origin_meta={EvidenceOrigin.FILE: OriginMetadata(raw_meta={"file_key": file_key})},
    )

    async def import_in_two_slices() -> None:
        job = jobs.create(user_id="local", source_id="cursor", operation=operation)
        first = await ImportRunner(
            evidence_store=evidence,
            job_store=jobs,
            on_stable_evidence=supervisor.enqueue_evidence,
        ).run(
            adapter=Adapter([ScannedEvidence(evidence=user, cursor=scan_cursor("byte:1"))]),
            job_id=job.job_id,
            max_work_events=1,
        )
        assert first.status is ImportStatus.PENDING
        # Wait beyond the live settle budget. A timer alone must not turn an
        # unanswered user message into a complete HY task.
        await asyncio.sleep(1.7)
        assert tasks.list() == []

        completed = await ImportRunner(
            evidence_store=evidence,
            job_store=jobs,
            on_stable_evidence=supervisor.enqueue_evidence,
        ).run(
            adapter=Adapter(
                [
                    ScannedEvidence(evidence=assistant, cursor=scan_cursor("byte:2")),
                    ScanCursorTouch(cursor=scan_cursor("byte:2", read_to_end=True)),
                ]
            ),
            job_id=job.job_id,
        )
        assert completed.status is ImportStatus.COMPLETED
        await asyncio.sleep(0.9)

    asyncio.run(import_in_two_slices())

    queued = tasks.list()
    assert len(queued) == 1
    conversation = evidence.list_conversation(queued[0].input_ref)
    assert [record.verbatim for record in conversation] == [user.verbatim, assistant.verbatim]
    assert all(task.last_error_code != "superseded_by_longer_chunk" for task in queued)


def test_live_import_waits_for_an_open_user_turn_to_receive_its_reply(
    tmp_path, monkeypatch
) -> None:
    import one_memory.memory_processing as processing

    monkeypatch.setattr(processing, "_LIVE_OPEN_TURN_SETTLE_SECONDS", 1.5)
    stores = _setup(tmp_path)
    evidence, profiles, tasks, governance, secrets, _profile = stores
    jobs = ImportJobStore(evidence.database_path)
    supervisor = MemoryProcessingSupervisor(
        tasks=tasks,
        profiles=profiles,
        secrets=secrets,
        evidence=evidence,
        governance=governance,
        data_dir=tmp_path,
    )
    file_key = "l" * 64

    def event(message_id: str, role: str, content: str, checkpoint: str):
        written_by = WrittenBy.PERSON if role == "user" else WrittenBy.AGENT
        cursor = SourceCursor(
            file_key=file_key,
            file_identity="fixture:live",
            checkpoint=checkpoint,
            observed_size=200,
            observed_modified_ns=1,
        )
        return ScannedEvidence(
            evidence=EvidenceInput(
                source_id="cursor",
                conversation_id="live-turn",
                message_id=message_id,
                role=role,
                written_by=written_by,
                created_at=datetime.now(UTC),
                verbatim=content,
                origin_meta={EvidenceOrigin.FILE: OriginMetadata(raw_meta={"file_key": file_key})},
            ),
            cursor=cursor,
        )

    class Adapter:
        descriptor = SourceDescriptor(
            source_id="cursor",
            display_name="Cursor",
            storage_kinds=frozenset({StorageKind.JSONL}),
            capabilities=SourceCapabilities(
                can_append=True,
                can_observe_edits=True,
                can_observe_deletes=False,
                can_reconcile_complete_set=False,
            ),
        )

        def __init__(self, scanned) -> None:
            self.scanned = scanned

        async def scan(self, _options):
            yield self.scanned
            yield ScanCursorTouch(
                cursor=self.scanned.cursor.model_copy(update={"read_to_end": True})
            )

    async def run_live_updates() -> None:
        dispatched = []

        def dispatch(record):
            dispatched.append((record.role, live_sync_arrival.get()))
            return supervisor.enqueue_evidence(record)

        for scanned in (
            event("user", "user", "先等我这轮回答完整。", "byte:1"),
            event("assistant", "assistant", "现在这一轮完整了。", "byte:2"),
        ):
            job = jobs.create(user_id="local", source_id="cursor", operation="sync")
            completed = await ImportRunner(
                evidence_store=evidence,
                job_store=jobs,
                on_stable_evidence=dispatch,
            ).run(adapter=Adapter(scanned), job_id=job.job_id)
            assert completed.status is ImportStatus.COMPLETED
            await asyncio.sleep(0.9)
            if scanned.evidence.role == "user":
                assert tasks.list() == []

        await asyncio.gather(*tuple(supervisor._debounces.values()))
        deadline = asyncio.get_running_loop().time() + 2
        while not tasks.list() and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        assert dispatched == [("user", True), ("assistant", True)]

    asyncio.run(run_live_updates())

    queued = tasks.list()
    assert len(queued) == 1
    assert all(task.last_error_code != "superseded_by_longer_chunk" for task in queued)


def test_preview_empty_is_the_open_turn_contract_not_a_preview_regression(tmp_path) -> None:
    """An open realtime user turn returns no task until the reply closes it.

    This is the C2 side of `[BLOCK C1 -> C2] preview-empty-on-c0`: the import
    fixture stops after the third user record, so a live debounce still holds
    this conversation open. The permission preview sees no task because there
    is no complete turn yet, not because the preview lost Evidence. Clearing
    the debounce after the assistant reply must produce the task.
    """
    stores = _setup(tmp_path)
    evidence, profiles, tasks, governance, secrets, _profile = stores
    user = _put(
        evidence,
        message_id="open-user",
        role="user",
        written_by=WrittenBy.PERSON,
        content="先记下这个计划。",
    )
    supervisor = MemoryProcessingSupervisor(
        tasks=tasks,
        profiles=profiles,
        secrets=secrets,
        evidence=evidence,
        governance=governance,
        data_dir=tmp_path,
    )
    key = (user.user_id, user.source_id, user.conversation_id, user.workspace)

    async def scenario() -> None:
        supervisor._debounces[key] = asyncio.create_task(asyncio.sleep(60))
        try:
            assert supervisor.prepare_conversation(user, live=True) == []
        finally:
            supervisor._debounces.pop(key).cancel()
        assistant = _put(
            evidence,
            message_id="closing-assistant",
            role="assistant",
            written_by=WrittenBy.AGENT,
            content="收到，计划已记下。",
        )
        created = supervisor.prepare_conversation(assistant)
        assert len(created) == 1
        assert created[0].input_ref == user.evidence_id
        await supervisor.shutdown()

    asyncio.run(scenario())


def test_digest_skips_scopes_whose_source_write_permission_is_withdrawn(tmp_path) -> None:
    """A revoked source must not keep feeding System 2.

    Revocation is the user withdrawing the source. New extraction already
    refuses it through `source_writes_permitted`; digest must use the same
    boundary before it lists HY scopes, or it summarises material the user
    withdrew. This pins the C2-owned half of the C3 revoked-digest red state.
    """
    stores = _setup(tmp_path)
    evidence, profiles, tasks, governance, secrets, profile = stores
    record = _put(
        evidence,
        message_id="revoked-scope",
        role="user",
        written_by=WrittenBy.PERSON,
        content="项目范围内的事实。",
        conversation_id="revoked-conversation",
        workspace="workspace:v1:project",
    )
    _confirmed_from(governance, record.evidence_id)
    bridge = FakeBridge(outputs=())
    supervisor = MemoryProcessingSupervisor(
        tasks=tasks,
        profiles=profiles,
        secrets=secrets,
        evidence=evidence,
        governance=governance,
        data_dir=tmp_path,
        source_writes_permitted=lambda _source_id: False,
    )
    supervisor._bridges[(profile.profile_id, profile.revision, None)] = bridge

    result = asyncio.run(supervisor.digest())

    assert result["scope_count"] == 0
    assert result["digests"] == []
    assert bridge.digest_calls == []


def test_digest_rechecks_revoked_source_after_the_supervisor_is_open(tmp_path) -> None:
    """The real grant revocation path must feed the same digest boundary.

    This mirrors C3's executable red state: create the scope, open the
    supervisor while the source is allowed, revoke the grant, then digest.
    Reading the callback at target-selection time (not once in the
    constructor) is what keeps the revoked scope out.
    """
    stores = _setup(tmp_path)
    evidence, profiles, tasks, governance, secrets, profile = stores
    database = tmp_path / "one-memory.sqlite3"
    grants = SourceGrantStore(database, home=tmp_path)
    folder = tmp_path / "cursor-root"
    folder.mkdir()
    grants.grant(
        user_id="local",
        source_id="cursor",
        root=folder,
        candidate_kind=RootCandidateKind.DEFAULT,
    )
    config = ConfigStore(tmp_path)
    config.set_sync("cursor", True)
    record = _put(
        evidence,
        message_id="real-revoked-scope",
        role="user",
        written_by=WrittenBy.PERSON,
        content="撤销之后不应再进入 digest。",
        conversation_id="real-revoked-conversation",
        workspace="workspace:v1:project",
    )
    _confirmed_from(governance, record.evidence_id)
    bridge = FakeBridge(outputs=())
    supervisor = MemoryProcessingSupervisor(
        tasks=tasks,
        profiles=profiles,
        secrets=secrets,
        evidence=evidence,
        governance=governance,
        data_dir=tmp_path,
        source_writes_permitted=lambda source_id: (
            config.sync_enabled(source_id)
            and grants.authorized_root(user_id="local", source_id=source_id) is not None
        ),
    )
    supervisor._bridges[(profile.profile_id, profile.revision, None)] = bridge
    grants.revoke(user_id="local", source_id="cursor")

    result = asyncio.run(supervisor.digest())

    assert result["scope_count"] == 0
    assert bridge.digest_calls == []


def test_frozen_harness_refuses_new_processing_before_a_task_or_model_call(tmp_path) -> None:
    """A frozen Harness must not create new processing work or call HY.

    The backfill guard covered only the history scan. The production durable
    drain and live enqueue call other supervisor entries, so this drives the
    real `SourceGrantStore` policy through all of them: direct processing,
    backfill, live preparation and digest. The only permitted side effect is
    the Evidence already committed by the source import.
    """
    stores = _setup(tmp_path)
    evidence, profiles, tasks, governance, secrets, profile = stores
    database = tmp_path / "one-memory.sqlite3"
    grants = SourceGrantStore(database, home=tmp_path)
    root = tmp_path / "cursor-root"
    root.mkdir()
    granted = grants.grant(
        user_id="local",
        source_id="cursor",
        root=root,
        candidate_kind=RootCandidateKind.DEFAULT,
    )
    user = _put(
        evidence,
        message_id="frozen-user",
        role="user",
        written_by=WrittenBy.PERSON,
        content="Harness Freeze 后不能创建新的记忆任务。",
        conversation_id="frozen-conversation",
        workspace="workspace:v1:project",
    )
    reply = _put(
        evidence,
        message_id="frozen-reply",
        role="assistant",
        written_by=WrittenBy.AGENT,
        content="收到。",
        conversation_id="frozen-conversation",
        workspace="workspace:v1:project",
    )
    _confirmed_from(governance, user.evidence_id)
    evidence_before = evidence.count()
    governed_before = len(governance.list(user_id="local"))

    config = ConfigStore(tmp_path)
    assert not config.sync_enabled("cursor")
    assert granted.revoked_at is None

    def source_may_write(source_id: str) -> bool:
        return config.sync_enabled(source_id) and (
            grants.authorized_root(user_id="local", source_id=source_id) is not None
        )

    bridge = FakeBridge(outputs=())
    supervisor = MemoryProcessingSupervisor(
        tasks=tasks,
        profiles=profiles,
        secrets=secrets,
        evidence=evidence,
        governance=governance,
        data_dir=tmp_path,
        source_writes_permitted=source_may_write,
    )
    supervisor._bridges[(profile.profile_id, profile.revision, None)] = bridge

    assert supervisor._source_may_write([user]) is False
    with pytest.raises(HyMemoryBridgeError, match="hy_memory_source_disabled"):
        asyncio.run(supervisor.process_records((user, reply)))
    assert asyncio.run(supervisor.enqueue_conversation_off_thread(user)) == []
    assert supervisor.prepare_backfill() == []
    assert supervisor.prepare_conversation(user) == []
    digest = asyncio.run(supervisor.digest())

    assert tasks.list() == []
    assert bridge.requests == []
    assert bridge.extracted_contents == []
    assert bridge.digest_calls == []
    assert len(governance.list(user_id="local")) == governed_before
    assert evidence.count() == evidence_before
    assert digest["scope_count"] == 0


def test_revoked_grant_refuses_new_processing(tmp_path) -> None:
    """Revocation is stronger than Freeze and also refuses new work."""
    stores = _setup(tmp_path)
    evidence, profiles, tasks, governance, secrets, profile = stores
    database = tmp_path / "one-memory.sqlite3"
    grants = SourceGrantStore(database, home=tmp_path)
    root = tmp_path / "cursor-root"
    root.mkdir()
    grants.grant(
        user_id="local",
        source_id="cursor",
        root=root,
        candidate_kind=RootCandidateKind.DEFAULT,
    )
    config = ConfigStore(tmp_path)
    config.set_sync("cursor", True)
    user = _put(
        evidence,
        message_id="revoked-user",
        role="user",
        written_by=WrittenBy.PERSON,
        content="撤销后不能进入记忆。",
        conversation_id="revoked-conversation",
        workspace="workspace:v1:project",
    )
    reply = _put(
        evidence,
        message_id="revoked-reply",
        role="assistant",
        written_by=WrittenBy.AGENT,
        content="收到。",
        conversation_id="revoked-conversation",
        workspace="workspace:v1:project",
    )
    _confirmed_from(governance, user.evidence_id)
    evidence_before = evidence.count()
    governed_before = len(governance.list(user_id="local"))

    revoked = grants.revoke(user_id="local", source_id="cursor")
    assert revoked.revoked_at is not None
    assert grants.authorized_root(user_id="local", source_id="cursor") is None

    bridge = FakeBridge(outputs=())
    supervisor = MemoryProcessingSupervisor(
        tasks=tasks,
        profiles=profiles,
        secrets=secrets,
        evidence=evidence,
        governance=governance,
        data_dir=tmp_path,
        source_writes_permitted=lambda source_id: (
            config.sync_enabled(source_id)
            and grants.authorized_root(user_id="local", source_id=source_id) is not None
        ),
    )
    supervisor._bridges[(profile.profile_id, profile.revision, None)] = bridge

    assert supervisor._source_may_write([user]) is False
    with pytest.raises(HyMemoryBridgeError, match="hy_memory_source_disabled"):
        asyncio.run(supervisor.process_records((user, reply)))
    assert asyncio.run(supervisor.enqueue_conversation_off_thread(user)) == []
    assert supervisor.prepare_backfill() == []
    assert supervisor.prepare_conversation(user) == []
    digest = asyncio.run(supervisor.digest())

    assert tasks.list() == []
    assert bridge.requests == []
    assert bridge.extracted_contents == []
    assert bridge.digest_calls == []
    assert len(governance.list(user_id="local")) == governed_before
    assert evidence.count() == evidence_before
    assert digest["scope_count"] == 0


def test_thawed_harness_processes_a_complete_turn(tmp_path) -> None:
    """Positive control: the same complete turn runs after Thaw."""
    stores = _setup(tmp_path)
    evidence, profiles, tasks, governance, secrets, profile = stores
    database = tmp_path / "one-memory.sqlite3"
    grants = SourceGrantStore(database, home=tmp_path)
    root = tmp_path / "cursor-root"
    root.mkdir()
    grants.grant(
        user_id="local",
        source_id="cursor",
        root=root,
        candidate_kind=RootCandidateKind.DEFAULT,
    )
    config = ConfigStore(tmp_path)
    config.set_sync("cursor", True)
    user = _put(
        evidence,
        message_id="write-on-user",
        role="user",
        written_by=WrittenBy.PERSON,
        content="Harness Thaw 后应进入记忆流程。",
        conversation_id="write-on-conversation",
    )
    reply = _put(
        evidence,
        message_id="write-on-reply",
        role="assistant",
        written_by=WrittenBy.AGENT,
        content="收到。",
        conversation_id="write-on-conversation",
    )
    callback_threads: list[int] = []

    def source_may_write(source_id: str) -> bool:
        callback_threads.append(threading.get_ident())
        return config.sync_enabled(source_id)

    bridge = FakeBridge(
        outputs=(
            HyMemoryOutput(
                memory_id="hy-write-on",
                content="Harness 已 Thaw。",
                layer="L2_FACT",
            ),
        )
    )
    supervisor = MemoryProcessingSupervisor(
        tasks=tasks,
        profiles=profiles,
        secrets=secrets,
        evidence=evidence,
        governance=governance,
        data_dir=tmp_path,
        source_writes_permitted=source_may_write,
    )
    supervisor._bridges[(profile.profile_id, profile.revision, None)] = bridge

    completed = asyncio.run(supervisor.process_records((user, reply)))

    assert len(completed) == 1
    assert completed[0].status is MemoryTaskStatus.COMPLETED
    assert len(bridge.requests) == 1
    assert len(governance.list(user_id="local")) == 1
    assert callback_threads
    assert threading.get_ident() not in callback_threads


def test_a_grown_chunk_leaves_started_work_alone(tmp_path) -> None:
    supervisor, tasks, profile, ask, first_reply, second_reply = _growing_conversation(tmp_path)

    shorter = supervisor._enqueue_chunk((ask, first_reply), profile, freeze_inputs=True)
    tasks.claim(shorter.task_id)
    supervisor._enqueue_chunk((ask, first_reply, second_reply), profile, freeze_inputs=True)

    assert tasks.require(shorter.task_id).status is MemoryTaskStatus.RUNNING


def test_a_chunk_that_drops_earlier_input_does_not_retire_it(tmp_path) -> None:
    supervisor, tasks, profile, ask, first_reply, second_reply = _growing_conversation(tmp_path)

    earlier = supervisor._enqueue_chunk((ask, first_reply), profile, freeze_inputs=True)
    supervisor._enqueue_chunk((ask, second_reply), profile, freeze_inputs=True)

    assert tasks.require(earlier.task_id).status is MemoryTaskStatus.PENDING


def test_digest_targets_stable_scopes_and_skips_quarantined_conversations(
    tmp_path,
) -> None:
    stores = _setup(tmp_path)
    evidence, profiles, tasks, governance, secrets, profile = stores
    workspace = _put(
        evidence,
        message_id="workspace",
        role="user",
        written_by=WrittenBy.PERSON,
        content="项目范围内的事实。",
        conversation_id="workspace-conversation",
        workspace="workspace:v1:project",
    )
    quarantined = _put(
        evidence,
        message_id="quarantined",
        role="user",
        written_by=WrittenBy.PERSON,
        content="尚未确定归属的对话。",
        conversation_id="unknown-conversation",
    )
    imported_memory = _put(
        evidence,
        message_id="imported-memory",
        role="user",
        written_by=WrittenBy.PERSON,
        content="用户偏好简洁的回答。",
        conversation_id="memory-conversation",
        content_kind=EvidenceContentKind.MEMORY_CONTENT,
    )
    for index, record in enumerate((workspace, quarantined, imported_memory)):
        as_person(governance).create(
            MemoryDraft(
                user_id="local",
                kind=MemoryKind.CHAT,
                content=f"记忆 {index}",
                scopes=("all-apps",),
                evidence_ids=(record.evidence_id,),
            ),
            request_id=f"candidate-{index}",
        )
    bridge = FakeBridge(outputs=())
    supervisor = MemoryProcessingSupervisor(
        tasks=tasks,
        profiles=profiles,
        secrets=secrets,
        evidence=evidence,
        governance=governance,
        data_dir=tmp_path,
    )
    supervisor._bridges[(profile.profile_id, profile.revision, None)] = bridge

    result = asyncio.run(supervisor.digest())

    assert bridge.digest_calls == [
        ("local", UNIFIED_HY_AGENT_ID),
        ("local", _hy_agent_id((workspace,))),
    ]
    assert result["success"] is True
    assert result["scope_count"] == 2
    assert result["skipped_conversation_scopes"] == 1


def test_digest_scopes_separate_applications_and_dates(tmp_path) -> None:
    evidence = EvidenceStore(tmp_path / "one-memory.sqlite3")
    records = (
        _put(
            evidence,
            message_id="a",
            role="user",
            written_by=WrittenBy.PERSON,
            content="a",
            source_id="cursor",
            created_at=datetime(2026, 10, 2, tzinfo=UTC),
        ),
        _put(
            evidence,
            message_id="b",
            role="user",
            written_by=WrittenBy.PERSON,
            content="b",
            source_id="cursor",
            created_at=datetime(2026, 10, 3, tzinfo=UTC),
        ),
        _put(
            evidence,
            message_id="c",
            role="user",
            written_by=WrittenBy.PERSON,
            content="c",
            source_id="codex",
            created_at=datetime(2026, 10, 2, tzinfo=UTC),
        ),
    )
    assert len({_hy_agent_id((record,)) for record in records}) == 3


def test_cancelled_multi_source_lock_acquisition_releases_earlier_source(tmp_path) -> None:
    evidence, profiles, tasks, governance, secrets, _profile = _setup(tmp_path)
    first = _put(
        evidence,
        message_id="first",
        role="user",
        written_by=WrittenBy.PERSON,
        content="first",
        source_id="a-source",
    )
    second = _put(
        evidence,
        message_id="second",
        role="user",
        written_by=WrittenBy.PERSON,
        content="second",
        source_id="b-source",
    )
    supervisor = MemoryProcessingSupervisor(
        tasks=tasks,
        profiles=profiles,
        secrets=secrets,
        evidence=evidence,
        governance=governance,
        data_dir=tmp_path,
    )

    async def scenario() -> None:
        blocked = _shared_source_lock("b-source")
        await blocked.acquire(ServiceLevel.BACKGROUND)

        async def acquire_both() -> None:
            async with supervisor._explicit_processing_lock((first, second)):
                pass

        task = asyncio.create_task(acquire_both())
        await asyncio.sleep(0)
        first_lock = _shared_source_lock("a-source")
        assert first_lock.locked()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not first_lock.locked()
        await blocked.release()

    asyncio.run(scenario())


def test_realtime_source_waiter_overtakes_background_before_hy(tmp_path) -> None:
    evidence, profiles, tasks, governance, secrets, _profile = _setup(tmp_path)
    record = _put(
        evidence,
        message_id="priority-source",
        role="user",
        written_by=WrittenBy.PERSON,
        content="priority source",
        source_id="workbuddy",
    )
    supervisor = MemoryProcessingSupervisor(
        tasks=tasks,
        profiles=profiles,
        secrets=secrets,
        evidence=evidence,
        governance=governance,
        data_dir=tmp_path,
    )

    async def scenario() -> None:
        order: list[str] = []
        holder_ready = asyncio.Event()
        release = asyncio.Event()

        async def hold() -> None:
            with service_level_context(ServiceLevel.BACKGROUND):
                async with supervisor._source_processing_lock((record.source_id,)):
                    holder_ready.set()
                    await release.wait()

        async def wait(name: str, level: ServiceLevel) -> None:
            with service_level_context(level):
                async with supervisor._source_processing_lock((record.source_id,)):
                    order.append(name)

        holder = asyncio.create_task(hold())
        await holder_ready.wait()
        background = asyncio.create_task(wait("background", ServiceLevel.BACKGROUND))
        await asyncio.sleep(0)
        realtime = asyncio.create_task(wait("realtime", ServiceLevel.REALTIME))
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(holder, background, realtime)

        assert order == ["realtime", "background"]

    asyncio.run(scenario())


def _confirmed_from(governance, evidence_id: str, *, index: int = 0) -> None:
    as_person(governance).create(
        MemoryDraft(
            user_id="local",
            kind=MemoryKind.CHAT,
            content=f"记忆 {index}",
            scopes=("workspace:v1:project",),
            evidence_ids=(evidence_id,),
        ),
        request_id=f"candidate-{index}",
    )


def test_what_system_2_induces_is_written_down_rather_than_returned(tmp_path) -> None:
    """`digest()` used to compute mental models and keep none of them.

    L6_SCHEMA lives in the graph, not in what a write returns, and digest
    handed its result to whoever called the endpoint. Five of them sat in Kuzu
    on this machine matching nothing in governance — the other half of the
    profile layer, produced and discarded.
    """
    stores = _setup(tmp_path)
    evidence, profiles, tasks, governance, secrets, profile = stores
    record = _put(
        evidence,
        message_id="m1",
        role="user",
        written_by=WrittenBy.PERSON,
        content="项目范围内的事实。",
        workspace="workspace:v1:project",
    )
    _confirmed_from(governance, record.evidence_id)
    bridge = FakeBridge(
        outputs=(),
        graph_nodes=(
            HyGraphNode(
                node_id="schema-1",
                content=(
                    "When ensuring data consistency, the user prioritizes cross-environment checks."
                ),
                layer="l6_schema",
            ),
            # Relations and facts share the graph; only schemas are profile.
            HyGraphNode(node_id="fact-1", content="一条普通事实。", layer="l2_fact"),
        ),
    )
    supervisor = MemoryProcessingSupervisor(
        tasks=tasks,
        profiles=profiles,
        secrets=secrets,
        evidence=evidence,
        governance=governance,
        data_dir=tmp_path,
    )
    supervisor._bridges[(profile.profile_id, profile.revision, None)] = bridge

    result = asyncio.run(supervisor.digest())

    assert result["schemas_recorded"] == 1
    schemas = [
        memory for memory in governance.list(user_id="local") if memory.kind is MemoryKind.SCHEMA
    ]
    assert len(schemas) == 1
    # Readable from every app: a schema says how the person works, not what
    # one codebase contains.
    assert schemas[0].scopes == ("all-apps",)
    assert schemas[0].status is MemoryStatus.CONFIRMED
    # And it says what it was induced from.
    assert schemas[0].evidence_ids == (record.evidence_id,)


def test_the_same_schema_is_not_written_twice_by_the_next_digest(tmp_path) -> None:
    """Digest runs repeatedly and System 2 keeps its nodes between runs."""
    stores = _setup(tmp_path)
    evidence, profiles, tasks, governance, secrets, profile = stores
    record = _put(
        evidence,
        message_id="m1",
        role="user",
        written_by=WrittenBy.PERSON,
        content="项目范围内的事实。",
        workspace="workspace:v1:project",
    )
    _confirmed_from(governance, record.evidence_id)
    nodes = (HyGraphNode(node_id="schema-1", content="用户偏好可验证的结论。", layer="l6_schema"),)
    supervisor = MemoryProcessingSupervisor(
        tasks=tasks,
        profiles=profiles,
        secrets=secrets,
        evidence=evidence,
        governance=governance,
        data_dir=tmp_path,
    )
    supervisor._bridges[(profile.profile_id, profile.revision, None)] = FakeBridge(
        outputs=(), graph_nodes=nodes
    )

    first = asyncio.run(supervisor.digest())
    second = asyncio.run(supervisor.digest())

    assert first["schemas_recorded"] == 1
    assert second["schemas_recorded"] == 0
    assert len([m for m in governance.list(user_id="local") if m.kind is MemoryKind.SCHEMA]) == 1


def test_a_schema_is_mirrored_under_its_node_id_even_when_another_memory_has_its_text(
    tmp_path,
) -> None:
    stores = _setup(tmp_path)
    evidence, profiles, tasks, governance, secrets, profile = stores
    record = _put(
        evidence,
        message_id="m1",
        role="user",
        written_by=WrittenBy.PERSON,
        content="项目范围内的事实。",
        workspace="workspace:v1:project",
    )
    _confirmed_from(governance, record.evidence_id)
    text = "用户偏好可验证的结论。"
    as_person(governance).create(
        MemoryDraft(
            user_id="local",
            kind=MemoryKind.CHAT,
            content=text,
            scopes=("all-apps",),
            evidence_ids=(record.evidence_id,),
        ),
        request_id="same-words",
        memory_id="hy-chat-node",
    )
    supervisor = MemoryProcessingSupervisor(
        tasks=tasks,
        profiles=profiles,
        secrets=secrets,
        evidence=evidence,
        governance=governance,
        data_dir=tmp_path,
    )
    supervisor._bridges[(profile.profile_id, profile.revision, None)] = FakeBridge(
        outputs=(),
        graph_nodes=(HyGraphNode(node_id="schema-1", content=text, layer="l6_schema"),),
    )

    assert asyncio.run(supervisor.digest())["schemas_recorded"] == 1
    assert governance.get("schema-1", user_id="local").kind is MemoryKind.SCHEMA


def test_a_scope_with_no_local_evidence_left_records_no_schema(tmp_path) -> None:
    """We do not keep a memory we cannot say why we believe.

    HY is the source of truth for which scopes exist, so digest also visits
    scopes whose local memories are gone. A schema from one of those has
    nothing to cite, and governance requires a citation.
    """
    stores = _setup(tmp_path)
    evidence, profiles, tasks, governance, secrets, profile = stores
    bridge = FakeBridge(
        outputs=(),
        graph_nodes=(
            HyGraphNode(node_id="schema-1", content="无从追溯的归纳。", layer="l6_schema"),
        ),
    )
    bridge.stored_agent_ids = ("one-memory:scope:vanished",)
    supervisor = MemoryProcessingSupervisor(
        tasks=tasks,
        profiles=profiles,
        secrets=secrets,
        evidence=evidence,
        governance=governance,
        data_dir=tmp_path,
    )
    supervisor._bridges[(profile.profile_id, profile.revision, None)] = bridge

    result = asyncio.run(supervisor.digest())

    assert result["schemas_recorded"] == 0
    assert [m for m in governance.list(user_id="local") if m.kind is MemoryKind.SCHEMA] == []


def test_digest_also_covers_scopes_only_hy_still_knows_about(tmp_path) -> None:
    stores = _setup(tmp_path)
    evidence, profiles, tasks, governance, secrets, profile = stores
    workspace = _put(
        evidence,
        message_id="workspace",
        role="user",
        written_by=WrittenBy.PERSON,
        content="项目范围内的事实。",
        conversation_id="workspace-conversation",
        workspace="workspace:v1:project",
    )
    as_person(governance).create(
        MemoryDraft(
            user_id="local",
            kind=MemoryKind.CHAT,
            content="记忆",
            scopes=("all-apps",),
            evidence_ids=(workspace.evidence_id,),
        ),
        request_id="candidate",
    )
    bridge = FakeBridge(outputs=())
    # Memories written under an earlier routing policy still live here.
    bridge.stored_agent_ids = (UNIFIED_HY_AGENT_ID, "one-memory:scope:legacy")
    supervisor = MemoryProcessingSupervisor(
        tasks=tasks,
        profiles=profiles,
        secrets=secrets,
        evidence=evidence,
        governance=governance,
        data_dir=tmp_path,
    )
    supervisor._bridges[(profile.profile_id, profile.revision, None)] = bridge

    result = asyncio.run(supervisor.digest())

    assert {agent_id for _user, agent_id in bridge.digest_calls} == {
        UNIFIED_HY_AGENT_ID,
        "one-memory:scope:legacy",
        _hy_agent_id((workspace,)),
    }
    assert result["scope_count"] == 3


def test_host_injected_text_is_not_treated_as_the_person_speaking(tmp_path) -> None:
    """Wrapper text a host injects is not testimony, whoever it is attributed to.

    Hosts prepend their own envelopes to a turn, and the record still arrives
    tagged as person-authored. Reading those as things the user said would let
    a host's own boilerplate become a confirmed fact about the user.
    """
    evidence = _setup(tmp_path)[0]
    prefixed = _put(
        evidence,
        message_id="prefixed",
        role="user",
        written_by=WrittenBy.PERSON,
        content="<system_notification>我喜欢深色主题。</system_notification>",
    )
    # The envelope does not have to start the turn to be an envelope.
    wrapped = _put(
        evidence,
        message_id="wrapped",
        role="user",
        written_by=WrittenBy.PERSON,
        content=(
            "<timestamp>2026-09-04</timestamp>\n"
            "<system_notification>我喜欢深色主题。</system_notification>"
        ),
    )
    # A harness may also hand us its raw request payload.
    payload = _put(
        evidence,
        message_id="payload",
        role="user",
        written_by=WrittenBy.PERSON,
        content='{"request_id": "r-1", "steps": [{"text": "我喜欢深色主题。"}]}',
    )
    spoken = _put(
        evidence,
        message_id="spoken",
        role="user",
        written_by=WrittenBy.PERSON,
        content="我喜欢深色主题。",
    )
    assert _is_hy_input_turn(spoken)
    assert not any(_is_hy_input_turn(record) for record in (prefixed, wrapped, payload))


def test_harness_instruction_blocks_never_reach_extraction(tmp_path) -> None:
    """Instructions the harness prepends arrive wearing the user's role.

    A rules file or environment preamble is a fact about the project, not a
    preference of the person. Letting one through would turn "this repo uses
    axios" into "the user prefers axios" and then recall it everywhere.
    """
    evidence = _setup(tmp_path)[0]
    injected = _put(
        evidence,
        message_id="agents-md",
        role="user",
        written_by=WrittenBy.PERSON,
        content="# AGENTS.md instructions\n\n统一用 axios 封装，不用 fetch 裸调。",
    )
    spoken = _put(
        evidence,
        message_id="spoken",
        role="user",
        written_by=WrittenBy.PERSON,
        content="我喜欢深色主题。",
    )

    assert _conversation_chunks([injected]) == []
    # Positive control: a real turn still gets through.
    assert _conversation_chunks([spoken]) != []


class _Sluggish:
    """A store whose every call takes a moment, the way a contended write
    lock does. Wrapping the real one keeps the behaviour under test intact."""

    def __init__(self, inner: object, delay: float = 0.05) -> None:
        self._inner = inner
        self._delay = delay

    def __getattr__(self, name: str):
        attribute = getattr(self._inner, name)
        if not callable(attribute):
            return attribute

        def slowly(*args, **kwargs):
            time.sleep(self._delay)
            return attribute(*args, **kwargs)

        return slowly


def test_the_pipeline_leaves_the_loop_free_to_answer(tmp_path) -> None:
    """A task must not stop the app answering while it runs.

    Every store call in a task is synchronous, and the writes wait on the
    write lock — up to five seconds each. Made from the loop, and made a dozen
    times per task with hundreds of tasks queued, they stalled everything
    served from the same process: /health, which does no work, was measured at
    3.5 seconds on this machine and a library read at 20.7 seconds, past the
    renderer's timeout, so a library of 483 memories displayed as empty.
    """
    stores = _setup(tmp_path)
    evidence, profiles, tasks, governance, secrets, profile = stores
    record = _put(
        evidence,
        message_id="u1",
        role="user",
        written_by=WrittenBy.PERSON,
        content="我更希望记忆处理都在本地完成。",
    )
    task = _task(tasks, profile, record)
    # A fifth of a second per call, so a dozen of them on the loop is seconds
    # and no plausible amount of machine noise can be mistaken for it.
    delay = 0.2
    runner = MemoryTaskRunner(
        tasks=_Sluggish(tasks, delay),
        profiles=_Sluggish(profiles, delay),
        secrets=secrets,
        evidence=_Sluggish(evidence, delay),
        governance=_Sluggish(governance, delay),
        data_dir=tmp_path,
        bridge_factory=lambda _profile, _secret, _data_dir: FakeBridge(
            outputs=(
                HyMemoryOutput(memory_id="m1", content="偏好本地处理。", layer="L2_FACT"),
                HyMemoryOutput(memory_id="m2", content="偏好可追溯。", layer="L2_FACT"),
                HyMemoryOutput(memory_id="m3", content="偏好一步完成。", layer="L2_FACT"),
            )
        ),
    )

    async def run_while_watching_the_loop() -> tuple[float, object]:
        longest = 0.0

        async def heartbeat() -> None:
            nonlocal longest
            last = time.perf_counter()
            while True:
                await asyncio.sleep(0.005)
                now = time.perf_counter()
                longest = max(longest, now - last)
                last = now

        beat = asyncio.create_task(heartbeat())
        try:
            finished = await runner.run(task.task_id)
        finally:
            beat.cancel()
        return longest, finished

    longest, finished = asyncio.run(run_while_watching_the_loop())

    # The work really happened, so this cannot pass by doing nothing.
    assert finished.status is MemoryTaskStatus.COMPLETED
    assert len(governance.list(user_id=record.user_id)) == 3
    # Over a dozen calls at a fifth of a second each: on the loop that is
    # seconds, so the gap between pass and fail is an order of magnitude and
    # this does not become a test of how busy the machine is.
    assert longest < delay * 3, f"事件循环被堵住 {longest:.3f}s"


def test_live_sync_enqueueing_leaves_the_loop_free_to_answer(tmp_path) -> None:
    """Every conversation that changes arrives here while the app is in use.

    Rebuilding one is seconds for a long conversation, and it was read on the
    loop: the watchdog caught this path holding the loop 2.1s, and a held loop
    is a host waiting for a recall that cannot be served.
    """
    stores = _setup(tmp_path)
    evidence, profiles, tasks, governance, secrets, _profile = stores
    seed = _put(
        evidence,
        message_id="u1",
        role="user",
        written_by=WrittenBy.PERSON,
        content="我希望同步是实时的。",
        conversation_id="conversation-1",
    )
    _put(
        evidence,
        message_id="a1",
        role="assistant",
        written_by=WrittenBy.AGENT,
        content="同步会保持实时。",
        conversation_id="conversation-1",
    )
    delay = 0.2

    async def enqueue_while_watching_the_loop() -> tuple[float, int]:
        supervisor = MemoryProcessingSupervisor(
            tasks=_Sluggish(tasks, delay),
            profiles=_Sluggish(profiles, delay),
            secrets=secrets,
            evidence=_Sluggish(evidence, delay),
            governance=governance,
            data_dir=tmp_path,
            runner_factory=lambda: _runner(tmp_path, stores, FakeBridge(outputs=())),
        )
        # Paid up front: the first hand-off to a thread in a process imports
        # the pool and starts it, which is a fifth of a second on the loop
        # once and has nothing to do with what is measured here.
        await asyncio.to_thread(lambda: None)
        longest = 0.0
        ticks = 0

        async def heartbeat() -> None:
            nonlocal longest, ticks
            last = time.perf_counter()
            while True:
                await asyncio.sleep(0.005)
                now = time.perf_counter()
                longest = max(longest, now - last)
                last = now
                ticks += 1

        beat = asyncio.create_task(heartbeat())
        # Let it beat before the work starts. Without this its first
        # measurement is taken after the stall, so it cannot see the very gap
        # it is here to catch — which is how the first version of this test
        # passed against the defect it was written for.
        await asyncio.sleep(0.05)
        try:
            created = await supervisor.enqueue_conversation_off_thread(seed)
            # A stall is only measured by the beat that follows it, so let one
            # follow. Cancelling straight away hid the gap entirely — the
            # second way this test managed to pass against its own defect.
            await asyncio.sleep(0.02)
        finally:
            beat.cancel()
            await supervisor.shutdown()
        return longest, len(created), ticks

    longest, created, ticks = asyncio.run(enqueue_while_watching_the_loop())

    # It really enqueued, so this cannot pass by doing nothing.
    assert created == 1
    assert ticks > 1, "心跳没有真的在跑"
    assert longest < delay * 2, f"实时入队期间循环被堵住 {longest:.3f}s"


def test_work_left_unfinished_by_the_last_run_is_taken_up_again(tmp_path) -> None:
    """Nothing else carries an interrupted memory across a restart.

    A task recorded but not finished — the app quit, the machine slept, the
    model was unreachable — is only ever picked up by resuming. Until this
    test there was nothing to notice if it stopped happening, and the symptom
    would be memories that never appear, from conversations already imported.
    """
    stores = _setup(tmp_path)
    evidence, profiles, tasks, governance, secrets, _profile = stores
    interrupted = _put(
        evidence,
        message_id="u1",
        role="user",
        written_by=WrittenBy.PERSON,
        content="上一轮没跑完的对话。",
        conversation_id="conversation-1",
        content_kind=EvidenceContentKind.MEMORY_CONTENT,
    )

    async def record_then_walk_away() -> None:
        supervisor = MemoryProcessingSupervisor(
            tasks=tasks,
            profiles=profiles,
            secrets=secrets,
            evidence=evidence,
            governance=governance,
            data_dir=tmp_path,
            runner_factory=lambda: _runner(tmp_path, stores, FakeBridge(outputs=())),
        )
        # Recorded without being scheduled: the state a quit mid-catch-up
        # leaves behind.
        supervisor.prepare_backfill()
        await supervisor.shutdown()

    asyncio.run(record_then_walk_away())

    pending = tasks.list()
    assert [task.status for task in pending] == [MemoryTaskStatus.PENDING]

    async def start_again() -> None:
        supervisor = MemoryProcessingSupervisor(
            tasks=tasks,
            profiles=profiles,
            secrets=secrets,
            evidence=evidence,
            governance=governance,
            data_dir=tmp_path,
            runner_factory=lambda: _runner(tmp_path, stores, FakeBridge(outputs=())),
        )
        await supervisor.resume_off_thread()
        for _ in range(200):
            if all(task.status is MemoryTaskStatus.COMPLETED for task in tasks.list()):
                break
            await asyncio.sleep(0.01)
        await supervisor.shutdown()

    asyncio.run(start_again())

    finished = tasks.list()
    assert [task.input_ref for task in finished] == [interrupted.evidence_id]
    assert [task.status for task in finished] == [MemoryTaskStatus.COMPLETED]


def test_hy_topic_tags_reach_the_library(tmp_path) -> None:
    """HY 打的主题标签要一路走到库里，而不是在边界上被丢掉。"""
    stores = _setup(tmp_path)
    evidence, _profiles, tasks, governance, _secrets, profile = stores
    user = _put(
        evidence,
        message_id="u1",
        role="user",
        written_by=WrittenBy.PERSON,
        content="周末不排评审会。",
    )
    task = _task(tasks, profile, user)
    bridge = FakeBridge(
        outputs=(
            HyMemoryOutput(
                memory_id="hy-1",
                content="用户周末不安排评审会。",
                tags=("排期", "工作习惯"),
            ),
        )
    )

    asyncio.run(_runner(tmp_path, stores, bridge).run(task.task_id))

    assert governance.list(user_id="local")[0].tags == ("排期", "工作习惯")


class _GatedDigestBridge(FakeBridge):
    """Holds `digest` open until released, and counts how many are inside it.

    The state is shared between bridges, so two supervisors each with their
    own bridge still report into one place.
    """

    def __init__(self, state: dict[str, object]) -> None:
        super().__init__(outputs=())
        self.state = state
        self.stored_agent_ids = ("agent-shared",)

    async def digest(self, *, user_id: str, agent_id: str) -> dict[str, object]:
        state = self.state
        state["entered"] += 1
        state["inside"] += 1
        state["peak"] = max(state["peak"], state["inside"])
        try:
            await state["release"].wait()
        finally:
            state["inside"] -= 1
        return await super().digest(user_id=user_id, agent_id=agent_id)


def test_digests_are_serialised_across_supervisors(tmp_path, monkeypatch) -> None:
    """Two digests must not overlap, even from different supervisors.

    A digest reads the memory it is about to summarise and then writes the
    summary. Two running at once — yesterday's harness and today's, say — each
    see a state the other is part-way through changing, and each concludes from
    it. The result is two summaries of two different half-updated memories.

    This used to acquire `_digest_mutex()` itself and gather on it, which only
    proved that `asyncio.Lock` works: deleting the acquire/release in
    `digest()` left it green. Here the real `digest()` of two supervisors runs,
    and the bridge each one calls is what observes the overlap.
    """
    from one_memory import memory_processing

    # The guard is process-wide and binds to the loop that first contends it;
    # start from a fresh one so an earlier test's loop cannot leak in.
    monkeypatch.setattr(memory_processing, "_digest_guard", None)
    state: dict[str, object] = {"entered": 0, "inside": 0, "peak": 0}
    supervisors = []
    for name in ("yesterday", "today"):
        root = tmp_path / name
        root.mkdir()
        evidence, profiles, tasks, governance, secrets, profile = _setup(root)
        supervisor = MemoryProcessingSupervisor(
            tasks=tasks,
            profiles=profiles,
            secrets=secrets,
            evidence=evidence,
            governance=governance,
            data_dir=root,
        )
        supervisor._bridges[(profile.profile_id, profile.revision, None)] = _GatedDigestBridge(
            state
        )
        supervisors.append(supervisor)

    async def scenario() -> list[dict[str, object]]:
        state["release"] = asyncio.Event()
        running = [asyncio.create_task(supervisor.digest()) for supervisor in supervisors]
        try:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + 3
            while state["entered"] < 1 and loop.time() < deadline:
                await asyncio.sleep(0.01)
            assert state["entered"] >= 1, "neither digest reached the bridge"
            # Give the second digest every chance to get in while the first
            # is still inside. Without the lock it does, well within this.
            deadline = loop.time() + 0.5
            while state["entered"] < 2 and loop.time() < deadline:
                await asyncio.sleep(0.01)
        finally:
            state["release"].set()
        return await asyncio.wait_for(asyncio.gather(*running), 5)

    results = asyncio.run(scenario())

    # Non-vacuous: both digests really ran their scope through the bridge.
    assert state["entered"] == 2
    assert [result["scope_count"] for result in results] == [1, 1]
    assert state["peak"] == 1, "two digests were inside the bridge at once"


def test_the_digest_lock_is_one_lock_for_the_process() -> None:
    """A per-supervisor lock could not order digests from different callers."""
    from one_memory.memory_processing import _digest_mutex

    assert _digest_mutex() is _digest_mutex()


def test_digest_lock_precedes_scope_and_evidence_snapshot(tmp_path, monkeypatch) -> None:
    from one_memory import memory_processing

    monkeypatch.setattr(memory_processing, "_digest_guard", None)
    entered = 0
    first_entered = asyncio.Event()
    release = asyncio.Event()
    original = MemoryProcessingSupervisor._digest_scopes

    async def gated(supervisor, bridge):
        nonlocal entered
        entered += 1
        if entered == 1:
            first_entered.set()
            await release.wait()
        return await original(supervisor, bridge)

    monkeypatch.setattr(MemoryProcessingSupervisor, "_digest_scopes", gated)
    supervisors = []
    for name in ("first", "second"):
        root = tmp_path / name
        root.mkdir()
        evidence, profiles, tasks, governance, secrets, profile = _setup(root)
        supervisor = MemoryProcessingSupervisor(
            tasks=tasks,
            profiles=profiles,
            secrets=secrets,
            evidence=evidence,
            governance=governance,
            data_dir=root,
        )
        supervisor._bridges[(profile.profile_id, profile.revision, None)] = FakeBridge(outputs=())
        supervisors.append(supervisor)

    async def scenario() -> None:
        tasks = [asyncio.create_task(supervisor.digest()) for supervisor in supervisors]
        await asyncio.wait_for(first_entered.wait(), 1)
        await asyncio.sleep(0.1)
        assert entered == 1
        release.set()
        await asyncio.gather(*tasks)

    asyncio.run(scenario())
    assert entered == 2


class _HangingBridge:
    """A bridge whose write never returns, to exercise the budget."""

    def __init__(self) -> None:
        self.calls = 0

    async def add_memory(self, *_args: object, **_kwargs: object) -> object:
        self.calls += 1
        await asyncio.sleep(30)
        return None


def test_a_task_past_its_budget_surfaces_as_hy_memory_timeout(tmp_path) -> None:
    """The wall-clock budget turns a hang into a classified timeout.

    A harness is serial, so a hung task holds the only slot that harness has
    and every later turn waits behind it. The budget must therefore surface as
    `hy_memory_timeout` — which the runner's own classification turns into a
    retry rather than a terminal failure or a silent hang.
    """
    stores = _setup(tmp_path)
    evidence, _profiles, tasks, _governance, _secrets, profile = stores
    record = _put(
        evidence,
        message_id="u-budget",
        role="user",
        written_by=WrittenBy.PERSON,
        content="预算测试的一句话。",
        conversation_id="conversation-budget",
    )
    task = _task(tasks, profile, record)
    runner = _runner(tmp_path, stores, _HangingBridge())

    async def scenario() -> None:
        async def hang(_task_id: str) -> object:
            await asyncio.sleep(30)
            return None

        # The runner's own body is what the budget wraps; patching it keeps the
        # test on the budget itself rather than on a bridge's behaviour.
        runner._run_without_content_permission = hang  # type: ignore[method-assign]
        with pytest.raises(HyMemoryBridgeError) as caught:
            await runner.run(task.task_id, timeout=0.05)
        assert str(caught.value) == "hy_memory_timeout"

    asyncio.run(scenario())
    recovered = tasks.require(task.task_id)
    assert recovered.status is MemoryTaskStatus.PENDING


def test_without_a_budget_a_slow_task_is_not_cut_off(tmp_path) -> None:
    """Absent profile timeout means unbounded, not an invented default.

    Deciding a number here would silently override whatever the operator
    configured, which is worse than not having a budget.
    """
    stores = _setup(tmp_path)
    evidence, _profiles, tasks, _governance, _secrets, profile = stores
    record = _put(
        evidence,
        message_id="u-budget",
        role="user",
        written_by=WrittenBy.PERSON,
        content="预算测试的一句话。",
        conversation_id="conversation-budget",
    )
    task = _task(tasks, profile, record)
    runner = _runner(tmp_path, stores, _HangingBridge())

    async def scenario() -> None:
        async def slow(_task_id: str) -> object:
            await asyncio.sleep(0.02)
            return tasks.require(task.task_id)

        runner._run_without_content_permission = slow  # type: ignore[method-assign]
        finished = await runner.run(task.task_id)
        assert finished.task_id == task.task_id

    asyncio.run(scenario())


def test_a_timed_out_task_becomes_unknown_and_is_not_left_running(tmp_path) -> None:
    """The budget must both release the slot and not invite a re-send.

    A task is claimed as `running` before anything slow happens, so a timeout
    must release that slot. But the timeout can fire after the HY call was
    already in flight, and HY has no idempotency key, so the task must settle
    `unknown` rather than a retryable state - otherwise the scheduler re-selects
    it and sends a second copy.
    """
    stores = _setup(tmp_path)
    evidence, _profiles, tasks, _governance, _secrets, profile = stores
    record = _put(
        evidence,
        message_id="u-timeout",
        role="user",
        written_by=WrittenBy.PERSON,
        content="超时测试的一句话。",
        conversation_id="conversation-timeout",
    )
    task = _task(tasks, profile, record)
    runner = _runner(tmp_path, stores, _HangingBridge())

    async def scenario() -> None:
        # Claim it, the way a real run does before the slow part.
        claimed, token = tasks.claim(task.task_id)
        assert claimed.status is MemoryTaskStatus.RUNNING

        async def hang(_task_id: str) -> object:
            tasks.record_hy_invocation(task.task_id, runner_token=token)
            await asyncio.sleep(30)
            return None

        runner._run_without_content_permission = hang  # type: ignore[method-assign]
        with pytest.raises(HyMemoryBridgeError):
            await runner.run(task.task_id, timeout=0.05)

        # Not left running: the turns behind it have to be able to run.
        settled = tasks.require(task.task_id)
        assert settled.status is MemoryTaskStatus.UNKNOWN
        assert settled.last_error_code == "model_content_outcome_unknown"

    asyncio.run(scenario())


def test_client_initialization_failure_retries_without_marking_hy_invoked(tmp_path) -> None:
    stores = _setup(tmp_path)
    evidence, _profiles, tasks, _governance, _secrets, profile = stores
    record = _put(
        evidence,
        message_id="init-retry",
        role="user",
        written_by=WrittenBy.PERSON,
        content="初始化失败不应变成结果未知。",
        conversation_id="conversation-init-retry",
    )
    task = _task(tasks, profile, record)

    class InitiallyUnavailableBridge(FakeBridge):
        prepare_calls = 0

        async def prepare(self) -> None:
            self.prepare_calls += 1
            if self.prepare_calls == 1:
                raise HyMemoryBridgeError("hy_memory_init_failed")

    bridge = InitiallyUnavailableBridge()
    runner = _runner(tmp_path, stores, bridge)

    first = asyncio.run(runner.run(task.task_id))
    assert first.status is MemoryTaskStatus.FAILED
    assert first.last_error_code == "hy_memory_init_failed"
    with sqlite3.connect(tasks.database_path) as connection:
        assert (
            connection.execute(
                "SELECT hy_invocation_count FROM memory_tasks WHERE task_id=?",
                (task.task_id,),
            ).fetchone()[0]
            == 0
        )

    second = asyncio.run(runner.run(task.task_id))
    assert second.status is MemoryTaskStatus.COMPLETED
    with sqlite3.connect(tasks.database_path) as connection:
        assert (
            connection.execute(
                "SELECT hy_invocation_count FROM memory_tasks WHERE task_id=?",
                (task.task_id,),
            ).fetchone()[0]
            == 1
        )


def test_binding_change_before_hy_is_cancelled_for_immediate_replacement(tmp_path) -> None:
    stores = _setup(tmp_path)
    evidence, profiles, tasks, governance, secrets, profile = stores
    record = _put(
        evidence,
        message_id="binding-replacement",
        role="user",
        written_by=WrittenBy.PERSON,
        content="开关切换后应使用最新授权重新整理。",
        conversation_id="binding-replacement",
    )
    task = _task(tasks, profile, record)
    claimed, token = tasks.claim(task.task_id)
    assert claimed.status is MemoryTaskStatus.RUNNING

    cancelled = tasks.stop(
        task.task_id,
        runner_token=token,
        error_code="model_content_continuous_binding_invalid",
    )

    assert cancelled.status is MemoryTaskStatus.CANCELLED
    assert cancelled.last_error_code == "model_content_continuous_binding_invalid"
    with sqlite3.connect(tasks.database_path) as connection:
        assert (
            connection.execute(
                "SELECT hy_invocation_count FROM memory_tasks WHERE task_id=?",
                (task.task_id,),
            ).fetchone()[0]
            == 0
        )

    supervisor = MemoryProcessingSupervisor(
        tasks=tasks,
        profiles=stores[1],
        secrets=stores[4],
        evidence=evidence,
        governance=stores[3],
        data_dir=tmp_path,
    )
    replacements = []

    async def replace(latest):
        replacements.append(latest)
        return []

    supervisor.enqueue_conversation_off_thread = replace  # type: ignore[method-assign]
    asyncio.run(supervisor._replace_stale_binding(cancelled))  # noqa: SLF001
    assert replacements == [record]


def test_runtime_maintenance_recovers_an_orphan_while_the_queue_is_idle(
    tmp_path, monkeypatch
) -> None:
    stores = _setup(tmp_path)
    evidence, profiles, tasks, governance, secrets, profile = stores
    record = _put(
        evidence,
        message_id="orphan-running",
        role="user",
        written_by=WrittenBy.PERSON,
        content="这条任务的 worker 已经消失。",
        conversation_id="orphan-running",
    )
    task = _task(tasks, profile, record)
    tasks.claim(task.task_id)
    monkeypatch.setattr("one_memory.memory_processing.ORPHAN_RECOVERY_INTERVAL_SECONDS", 0.01)
    supervisor = MemoryProcessingSupervisor(
        tasks=tasks,
        profiles=profiles,
        secrets=secrets,
        evidence=evidence,
        governance=governance,
        data_dir=tmp_path,
        runner_factory=lambda: _runner(tmp_path, stores, FakeBridge()),
    )

    async def scenario() -> None:
        supervisor.start_maintenance()
        try:
            for _ in range(50):
                if tasks.require(task.task_id).status is MemoryTaskStatus.PENDING:
                    break
                await asyncio.sleep(0.01)
            assert tasks.require(task.task_id).status is MemoryTaskStatus.PENDING
        finally:
            await supervisor.shutdown()

    asyncio.run(scenario())


def test_runtime_maintenance_does_not_reclaim_a_direct_durable_task(tmp_path, monkeypatch) -> None:
    stores = _setup(tmp_path)
    evidence, profiles, tasks, governance, secrets, profile = stores
    record = _put(
        evidence,
        message_id="durable-running",
        role="user",
        written_by=WrittenBy.PERSON,
        content="这条直接队列任务仍由当前进程执行。",
        conversation_id="durable-running",
    )
    answer = _put(
        evidence,
        message_id="durable-running-answer",
        role="assistant",
        written_by=WrittenBy.AGENT,
        content="开始执行。",
        conversation_id="durable-running",
    )
    entered = asyncio.Event()
    release = asyncio.Event()

    class SlowDirectRunner:
        async def run(self, task_id, *, timeout=None):
            del timeout
            _running, token = tasks.claim(task_id)
            entered.set()
            await release.wait()
            return tasks.complete(task_id, runner_token=token)

        def _frozen_chunk(self, _task):
            return (record, answer)

    monkeypatch.setattr("one_memory.memory_processing.ORPHAN_RECOVERY_INTERVAL_SECONDS", 0.01)
    supervisor = MemoryProcessingSupervisor(
        tasks=tasks,
        profiles=profiles,
        secrets=secrets,
        evidence=evidence,
        governance=governance,
        data_dir=tmp_path,
        bridge_factory=lambda *_args: FakeBridge(),
        runner_factory=SlowDirectRunner,
    )

    async def scenario() -> None:
        supervisor.start_maintenance()
        running = asyncio.create_task(supervisor.process_records((record, answer)))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            await asyncio.sleep(0.05)
            current = tasks.task_for_evidence(record.evidence_id)
            assert current is not None
            assert current.status is MemoryTaskStatus.RUNNING
            release.set()
            await asyncio.wait_for(running, 2)
            assert tasks.task_for_evidence(record.evidence_id).status is MemoryTaskStatus.COMPLETED
        finally:
            release.set()
            await supervisor.shutdown()

    asyncio.run(scenario())


def test_second_supervisor_does_not_reclaim_a_direct_task_owned_by_the_first(
    tmp_path, monkeypatch
) -> None:
    stores = _setup(tmp_path)
    evidence, profiles, tasks, governance, secrets, profile = stores
    record = _put(
        evidence,
        message_id="durable-running-cross-supervisor",
        role="user",
        written_by=WrittenBy.PERSON,
        content="这条直接队列任务由另一个 supervisor 执行。",
        conversation_id="durable-running-cross-supervisor",
    )
    answer = _put(
        evidence,
        message_id="durable-running-cross-supervisor-answer",
        role="assistant",
        written_by=WrittenBy.AGENT,
        content="开始执行。",
        conversation_id="durable-running-cross-supervisor",
    )
    entered = asyncio.Event()
    release = asyncio.Event()

    class SlowDirectRunner:
        async def run(self, task_id, *, timeout=None):
            del timeout
            _running, token = tasks.claim(task_id)
            entered.set()
            await release.wait()
            return tasks.complete(task_id, runner_token=token)

        def _frozen_chunk(self, _task):
            return (record, answer)

    monkeypatch.setattr("one_memory.memory_processing.ORPHAN_RECOVERY_INTERVAL_SECONDS", 0.01)
    owner = MemoryProcessingSupervisor(
        tasks=tasks,
        profiles=profiles,
        secrets=secrets,
        evidence=evidence,
        governance=governance,
        data_dir=tmp_path,
        bridge_factory=lambda *_args: FakeBridge(),
        runner_factory=SlowDirectRunner,
    )
    reclaimer = MemoryProcessingSupervisor(
        tasks=MemoryTaskStore(tasks.database_path),
        profiles=profiles,
        secrets=secrets,
        evidence=evidence,
        governance=governance,
        data_dir=tmp_path,
        bridge_factory=lambda *_args: FakeBridge(),
    )

    async def scenario() -> None:
        reclaimer.start_maintenance()
        running = asyncio.create_task(owner.process_records((record, answer)))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            await asyncio.sleep(0.05)
            current = tasks.task_for_evidence(record.evidence_id)
            assert current is not None
            assert current.status is MemoryTaskStatus.RUNNING
            release.set()
            await asyncio.wait_for(running, 2)
            assert tasks.task_for_evidence(record.evidence_id).status is MemoryTaskStatus.COMPLETED
        finally:
            release.set()
            await owner.shutdown()
            await reclaimer.shutdown()

    asyncio.run(scenario())


def test_policy_refusals_are_counted_apart_from_real_failures(tmp_path) -> None:
    """A revoked policy refuses tasks at creation; that is not a failed extract.

    Those tasks never ran (`attempts = 0`), so counting them as ordinary
    failures makes a deliberately turned-off feature look like a broken
    pipeline. They are reported as their own number instead.
    """
    stores = _setup(tmp_path)
    evidence, _profiles, tasks, _governance, _secrets, profile = stores
    record = _put(
        evidence,
        message_id="u-policy",
        role="user",
        written_by=WrittenBy.PERSON,
        content="策略失效测试的一句话。",
        conversation_id="conversation-policy",
    )
    task = _task(tasks, profile, record)

    _claimed, token = tasks.claim(task.task_id)
    tasks.fail(
        task.task_id,
        runner_token=token,
        error_code="model_content_policy_not_current_or_enabled",
    )
    assert tasks.require(task.task_id).status is MemoryTaskStatus.FAILED

    # Never attempted, so it is a policy refusal rather than a failure to extract.
    assert tasks.unexecuted_policy_failures() == 1


def test_a_real_failure_is_not_counted_as_a_policy_refusal(tmp_path) -> None:
    """Otherwise the number would under-report genuine breakage."""
    stores = _setup(tmp_path)
    evidence, _profiles, tasks, _governance, _secrets, profile = stores
    record = _put(
        evidence,
        message_id="u-real",
        role="user",
        written_by=WrittenBy.PERSON,
        content="真实失败测试的一句话。",
        conversation_id="conversation-real",
    )
    task = _task(tasks, profile, record)

    _claimed, token = tasks.claim(task.task_id)
    tasks.fail(task.task_id, runner_token=token, error_code="hy_memory_runtime_failed")

    assert tasks.require(task.task_id).status is MemoryTaskStatus.FAILED
    assert tasks.unexecuted_policy_failures() == 0


def test_scheduler_skips_failed_tasks_at_the_fixed_retry_ceiling(tmp_path) -> None:
    """The scheduler's retry ceiling must be a fixed bound, not the constant.

    `MemoryTaskStore.claim()` already refuses a `FAILED` row at the ceiling.
    The dispatch path has a second guard so such a row is never handed to a
    runner at all. Build the rows with the concrete boundary (3) rather than
    importing the implementation constant: mutating that constant to a huge
    value must make this test fail instead of silently widening the budget.
    """
    stores = _setup(tmp_path)
    evidence, profiles, tasks, governance, secrets, profile = stores
    exhausted = _put(
        evidence,
        message_id="retry-ceiling-exhausted",
        role="user",
        written_by=WrittenBy.PERSON,
        conversation_id="retry-ceiling-exhausted",
        content="This conversation has already failed three times.",
    )
    retryable = _put(
        evidence,
        message_id="retry-ceiling-retryable",
        role="user",
        written_by=WrittenBy.PERSON,
        conversation_id="retry-ceiling-retryable",
        content="This conversation still has one attempt left.",
    )
    exhausted_task = _task(tasks, profile, exhausted, suffix="exhausted")
    retryable_task = _task(tasks, profile, retryable, suffix="retryable")
    with sqlite3.connect(tasks.database_path) as connection, connection:
        connection.execute(
            "UPDATE memory_tasks SET status=?, attempts=3, "
            "last_error_code='model_content_execution_failed' WHERE task_id=?",
            (MemoryTaskStatus.FAILED, exhausted_task.task_id),
        )
        connection.execute(
            "UPDATE memory_tasks SET status=?, attempts=2, "
            "last_error_code='model_content_execution_failed' WHERE task_id=?",
            (MemoryTaskStatus.FAILED, retryable_task.task_id),
        )

    supervisor = MemoryProcessingSupervisor(
        tasks=tasks,
        profiles=profiles,
        secrets=secrets,
        evidence=evidence,
        governance=governance,
        data_dir=tmp_path,
        runner_factory=lambda: _runner(tmp_path, stores, FakeBridge()),
    )
    work = supervisor._classify(tasks.list(statuses=(MemoryTaskStatus.FAILED,)))

    assert [item.task_id for item in work] == [retryable_task.task_id]
