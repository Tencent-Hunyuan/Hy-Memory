"""R-09：首次安装引导把画像录下来，不问用户一句话。

引导导完来源就结束了，从不归纳一次画像。`digest()` 全仓唯一的调用方是
记忆空间里那个按钮（`MemorySpace.tsx:265`），也就是说首次安装走完，
System 2 归纳出的画像一条都不在治理层里,新装的库对「我是谁」一无所知，
而证据其实已经导进来了。

归纳本身还漏了一半：`_record_schemas` 只挑 `L6_SCHEMA` 节点，
`L0_BASIC_INFO` 直接跳过。`_read_graph_snapshot` 的查询不带 layer 过滤，
L0 节点本来就在快照里,取回来了又丢掉。HY 自己把这两层并称画像
（`_PROFILE_LAYERS = {"l0_basic_info", "l6_schema"}`），
basic info 那半边留在 Kuzu 里，本地看不见。
"""

import asyncio
import sqlite3

from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from one_memory.api import create_app
from one_memory.config import Settings
from one_memory.hy_memory_bridge import HyGraphNode, HyGraphSnapshot, HyMemoryBridgeError
from one_memory.imports.models import (
    EvidenceContentKind,
    EvidenceInput,
    EvidenceOrigin,
    OriginMetadata,
    WrittenBy,
)
from one_memory.imports.storage import EvidenceStore
from one_memory.memory_governance import (
    MemoryDraft,
    MemoryGovernanceStore,
    MemoryKind,
    MemoryStatus,
)
from one_memory.memory_processing import MemoryProcessingSupervisor
from one_memory.memory_tasks import MemoryTaskStore
from one_memory.model_profiles import (
    ModelCapabilities,
    ModelProfileInput,
    ModelProfileStore,
    ProviderKind,
)
from one_memory.secrets import InMemorySecretStore
from tests.person_door import as_person


class FakeGraph:
    """归纳只用得上四个方法，其余不实现,少一个假实现少一处失真。"""

    def __init__(self, graph_nodes: tuple[HyGraphNode, ...]) -> None:
        self.graph_nodes = graph_nodes
        self.graph_reads: list[tuple[str, str]] = []
        self.digest_calls: list[tuple[str, str]] = []

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
        return ()

    async def close(self) -> None:
        return None


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


def _imported(evidence: EvidenceStore, *, workspace: str | None = "workspace:v1:project"):
    return evidence.put(
        EvidenceInput(
            source_id="cursor",
            workspace=workspace,
            conversation_id="conversation",
            message_id="m1",
            correlation_id="m1",
            content_kind=EvidenceContentKind.RAW_CONVERSATION,
            role="user",
            written_by=WrittenBy.PERSON,
            verbatim="导入进来的一段对话。",
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    ).record


def _confirmed_from(governance: MemoryGovernanceStore, evidence_id: str) -> None:
    """归纳只对「还有本地证据的 scope」动手，所以得先有一条确认记忆。"""
    as_person(governance).create(
        MemoryDraft(
            user_id="local",
            kind=MemoryKind.CHAT,
            content="项目里的一条既有记忆。",
            scopes=("workspace:v1:project",),
            evidence_ids=(evidence_id,),
        ),
        request_id="candidate-1",
    )


def _supervisor(tmp_path, stores, bridge) -> MemoryProcessingSupervisor:
    evidence, profiles, tasks, governance, secrets, profile = stores
    supervisor = MemoryProcessingSupervisor(
        tasks=tasks,
        profiles=profiles,
        secrets=secrets,
        evidence=evidence,
        governance=governance,
        data_dir=tmp_path,
    )
    supervisor._bridges[(profile.profile_id, profile.revision, None)] = bridge
    return supervisor


def test_the_basic_info_half_of_the_profile_is_written_down_too(tmp_path) -> None:
    """L0_BASIC_INFO 取回来了又被丢掉,画像只进了一半。

    HY 自己把 L0_basic_info 与 L6_schema 并称画像，快照里两层都在，
    而 `_record_schemas` 只留 L6。零提问录画像要的正是 L0 这半边:
    「用户是一名产品经理」这种事实，只会出现在 basic info 层。
    """
    stores = _setup(tmp_path)
    evidence, _profiles, _tasks, governance, _secrets, _profile = stores
    record = _imported(evidence)
    _confirmed_from(governance, record.evidence_id)
    bridge = FakeGraph(
        graph_nodes=(
            HyGraphNode(
                node_id="basic-1",
                content="用户是一名非技术背景的产品经理。",
                layer="l0_basic_info",
            ),
            HyGraphNode(
                node_id="schema-1",
                content="用户偏好可验证的结论。",
                layer="l6_schema",
            ),
        ),
    )
    supervisor = _supervisor(tmp_path, stores, bridge)

    result = asyncio.run(supervisor.digest())

    profile_contents = {
        memory.content: memory
        for memory in governance.list(user_id="local")
        if memory.scopes == ("all-apps",)
    }
    assert "用户是一名非技术背景的产品经理。" in profile_contents
    assert "用户偏好可验证的结论。" in profile_contents
    assert result["schemas_recorded"] == 2
    basic = profile_contents["用户是一名非技术背景的产品经理。"]
    assert basic.status is MemoryStatus.CONFIRMED
    # 说得出为什么相信它,和 L6 那半边同一个标准。
    assert basic.evidence_ids == (record.evidence_id,)
    # kind 用仓库既有的 layer→kind 映射，不为画像新造一个种类
    # （种类是 API 契约的一部分，新增要人放行）。
    assert basic.kind is MemoryKind.CHAT


def test_a_layer_hy_does_not_call_profile_still_never_reaches_governance(tmp_path) -> None:
    """绿对照，也是这一项的防假修复守卫。

    把「只挑 L6」改成「快照里的都带回来」能让上面那条变绿，代价是
    整段原始对话（L1_RAW）被当成画像广播给每个 app。这条盯着它。
    """
    stores = _setup(tmp_path)
    evidence, _profiles, _tasks, governance, _secrets, _profile = stores
    record = _imported(evidence)
    _confirmed_from(governance, record.evidence_id)
    bridge = FakeGraph(
        graph_nodes=tuple(
            HyGraphNode(node_id=f"node-{layer}", content=f"{layer} 的内容。", layer=layer)
            for layer in ("l1_raw", "l2_fact", "l3_summary", "l4_identity", "l7_intention")
        ),
    )
    supervisor = _supervisor(tmp_path, stores, bridge)

    result = asyncio.run(supervisor.digest())

    assert result["schemas_recorded"] == 0
    assert [memory.content for memory in governance.list(user_id="local")] == [
        "项目里的一条既有记忆。"
    ]


def test_the_same_basic_info_is_not_written_twice(tmp_path) -> None:
    """绿对照：归纳会被反复触发（引导一次、按钮随时），不能越攒越多。"""
    stores = _setup(tmp_path)
    evidence, _profiles, _tasks, governance, _secrets, _profile = stores
    record = _imported(evidence)
    _confirmed_from(governance, record.evidence_id)
    nodes = (
        HyGraphNode(
            node_id="basic-1",
            content="用户是一名非技术背景的产品经理。",
            layer="l0_basic_info",
        ),
    )
    supervisor = _supervisor(tmp_path, stores, FakeGraph(graph_nodes=nodes))

    first = asyncio.run(supervisor.digest())
    second = asyncio.run(supervisor.digest())

    assert first["schemas_recorded"] == 1
    assert second["schemas_recorded"] == 0
    assert [
        memory.content
        for memory in governance.list(user_id="local")
        if memory.scopes == ("all-apps",)
    ] == ["用户是一名非技术背景的产品经理。"]


def _app(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data",
        source_home=tmp_path / "home",
        api_token=SecretStr("test-token"),
    )
    return create_app(settings, source_catalog={})


def _client(app) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={
            "Authorization": "Bearer test-token",
            "Origin": "http://127.0.0.1:5173",
        },
    )


def test_finishing_onboarding_is_not_model_content_consent(tmp_path) -> None:
    """S1.3: completion and skip cannot authorize profile induction."""

    async def scenario() -> None:
        app = _app(tmp_path)
        async with app.router.lifespan_context(app), _client(app) as client:
            calls: list[str] = []

            async def spy() -> dict[str, object]:
                calls.append("digest")
                return {"success": True, "schemas_recorded": 0}

            app.state.memory_processing_supervisor.digest = spy  # type: ignore[method-assign]

            # 只是看一眼引导状态，不该触发归纳。
            assert (await client.get("/api/v1/onboarding")).status_code == 200
            assert calls == []

            finished = await client.post("/api/v1/onboarding/complete")

            assert finished.json()["status"] == "completed"
            assert calls == []

    asyncio.run(scenario())


def test_the_way_back_to_the_first_run_deletes_nothing(tmp_path) -> None:
    """重置只动完成标记，记忆一条不少。

    完成态和记忆放在同一个库里是有意的:重装一遍 App 不该把一个已经有记忆的人
    当成新用户。代价是想再看一次冷启动，重装不管用。这条盯着那个出口:
    状态回到首次运行，别的都不动。
    """

    async def scenario() -> None:
        app = _app(tmp_path)
        async with app.router.lifespan_context(app), _client(app) as client:
            database = tmp_path / "data" / "one-memory.sqlite3"
            with sqlite3.connect(database) as connection:
                connection.execute(
                    "INSERT INTO governed_memories "
                    "(memory_id,user_id,kind,status,content,scopes_json,"
                    "revision,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        "keep-me",
                        "local",
                        "chat",
                        "confirmed",
                        "记忆必须保留",
                        '["all-apps"]',
                        1,
                        "2026-10-17T00:00:00+00:00",
                        "2026-10-17T00:00:00+00:00",
                    ),
                )
            finished = await client.post("/api/v1/onboarding/complete")

            assert finished.json()["status"] == "completed"

            reset = await client.post("/api/v1/onboarding/reset")

            assert reset.status_code == 200
            assert reset.json()["status"] == "not_started"
            assert reset.json()["completed_at"] is None
            # 落盘了，不是只在响应里改一下：下一次启动读到的也是首次运行。
            assert (await client.get("/api/v1/onboarding")).json()["status"] == "not_started"
            with sqlite3.connect(database) as connection:
                assert connection.execute(
                    "SELECT content,revision FROM governed_memories "
                    "WHERE memory_id='keep-me'"
                ).fetchone() == ("记忆必须保留", 1)

    asyncio.run(scenario())


def test_resetting_before_anything_was_completed_is_not_an_error(tmp_path) -> None:
    """绿对照:新库里还没有那一行，重置不能炸。"""

    async def scenario() -> None:
        app = _app(tmp_path)
        async with app.router.lifespan_context(app), _client(app) as client:
            reset = await client.post("/api/v1/onboarding/reset")

            assert reset.status_code == 200
            assert reset.json()["status"] == "not_started"

    asyncio.run(scenario())


def test_hy_being_down_does_not_leave_the_first_install_unfinished(tmp_path) -> None:
    """守卫：这一步可跳过，归纳失败不能把引导卡住。

    首次安装时模型档案可能还没配好，通用出站网络也可能不可用。
    归纳失败只该少一条画像，不该让人装不完。
    """

    async def scenario() -> None:
        app = _app(tmp_path)
        async with app.router.lifespan_context(app), _client(app) as client:

            async def unavailable() -> dict[str, object]:
                raise HyMemoryBridgeError("hy_memory_unavailable")

            app.state.memory_processing_supervisor.digest = unavailable  # type: ignore[method-assign]

            finished = await client.post("/api/v1/onboarding/complete")

            assert finished.status_code == 200
            assert finished.json()["status"] == "completed"
            assert finished.json()["completed_at"] is not None
            # 状态是真的落了盘，不是只在这次响应里。
            assert (await client.get("/api/v1/onboarding")).json()["status"] == "completed"

    asyncio.run(scenario())
