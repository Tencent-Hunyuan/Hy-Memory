import asyncio
import os
from types import SimpleNamespace

import pytest

from hy_memory.agent.reconciler import MemoryReconciler
from hy_memory.config import MemoryConfig
from hy_memory.data.qdrant_native import check_version
from hy_memory.retrieval import NATIVE_CONFIG_ID as CONFIG_ID
from hy_memory.retrieval import validate_native_threshold as validate_threshold
from hy_memory.data.vector_store_base import validate_native_query as validate_query
from hy_memory.data.vector_store_base import NativeQuery, VectorStoreBase
from hy_memory.data.vector_store_qdrant import QdrantVectorStore
from hy_memory.models.memory import MemoryLayer, MemoryStatus
from hy_memory.pipelines._retrieval.evolution import resolve_native_hits
from hy_memory.pipelines.base import ReadResponse
from hy_memory.pipelines.registry import ComponentFactory
from hy_memory.retrieval import NativeRetrievalError, NativeScoreThreshold
from conftest import CaptureLLM, FixedEmbed, node, public_client


def plan(**kwargs):
    return NativeQuery(
        texts=["query"],
        embeddings=[[1.0, 0.0]],
        user_ids=["alice"],
        layers=[MemoryLayer.L2_FACT],
        **kwargs,
    )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, "0.5"])
def test_T08_nonfinite_and_untyped_threshold(value):
    with pytest.raises(NativeRetrievalError):
        NativeScoreThreshold(value, "hybrid", "rrf", CONFIG_ID)


@pytest.mark.parametrize(
    "threshold",
    [
        0.5,
        NativeScoreThreshold(0, "dense", "cosine", CONFIG_ID),
        NativeScoreThreshold(0, "hybrid", "rrf", "wrong"),
    ],
)
def test_T08_mismatched_threshold(threshold):
    with pytest.raises(NativeRetrievalError):
        validate_threshold(threshold, "hybrid")


@pytest.mark.parametrize(
    "field,value",
    [
        ("user_ids", []),
        ("mode", "invalid"),
        ("embeddings", []),
        ("embeddings", [[float("nan")]]),
        ("limit", 301),
    ],
)
def test_invalid_plan(field, value):
    query = plan()
    setattr(query, field, value)
    with pytest.raises(NativeRetrievalError):
        validate_query(query)


async def test_unsupported_base_and_local():
    with pytest.raises(NativeRetrievalError, match="Qdrant Server"):
        await VectorStoreBase.native_query(object(), plan())
    with pytest.raises(NativeRetrievalError) as exc:
        check_version(None, True)
    assert exc.value.reason == "unsupported"


def test_T18_python39_and_version_rejection(monkeypatch):
    monkeypatch.setattr("hy_memory.data.vector_store_base.sys.version_info", (3, 9))
    with pytest.raises(NativeRetrievalError, match="Python"):
        validate_query(plan())
    monkeypatch.setattr("hy_memory.data.vector_store_base.sys.version_info", (3, 12))
    monkeypatch.setattr("hy_memory.data.qdrant_native.version", lambda _: "1.19.1")
    with pytest.raises(NativeRetrievalError):
        check_version(None, False)
    monkeypatch.setattr("hy_memory.data.qdrant_native.version", lambda _: "1.20.0")
    with pytest.raises(NativeRetrievalError):
        check_version(
            SimpleNamespace(info=lambda: SimpleNamespace(version="1.19.1")), False
        )


@pytest.mark.parametrize(
    "option,value",
    [
        ("min_score", 0),
        ("schema_limit", 1),
        ("intention_limit", 1),
        ("rerank", True),
        ("native_mode", "bad"),
        ("limit", -1),
        ("limit", 101),
        ("scene", "all"),
        ("scene", "working"),
        ("layers", ["l1_raw"]),
    ],
)
async def test_public_rejects_before_backend(option, value):
    store = SimpleNamespace(config=MemoryConfig())
    client = public_client(store)
    kwargs = dict(reader="native", user_ids=["alice"])
    kwargs[option] = value
    with pytest.raises((NativeRetrievalError, ValueError)):
        await client.async_search("query", **kwargs)


async def test_T18_legacy_omitted_and_explicit_zero(monkeypatch):
    store = SimpleNamespace(config=MemoryConfig())
    client = public_client(store)
    requests = []

    class Reader:
        VERSION = "legacy"

        async def read(self, request):
            requests.append(request)
            return ReadResponse(success=True)

    async def reader(**kwargs):
        return Reader()

    monkeypatch.setattr(client._registry, "get_reader", reader)
    monkeypatch.setattr(client, "_spawn_search_dedup", lambda *args, **kwargs: None)
    await client.async_search("query", user_ids=["alice"])
    assert (requests[-1].min_score, requests[-1].schema_limit, requests[-1].rerank) == (
        0.4,
        10,
        True,
    )
    await client.async_search(
        "query", user_ids=["alice"], min_score=0, schema_limit=0, rerank=False
    )
    assert (requests[-1].min_score, requests[-1].schema_limit, requests[-1].rerank) == (
        0,
        0,
        False,
    )


async def test_T17_factory_concurrent_names_without_environment_mutation(monkeypatch):
    config = MemoryConfig()
    factory = ComponentFactory(config, embedding_service=FixedEmbed())
    factory._shared_vector_store = SimpleNamespace(_client=True)
    monkeypatch.setenv("HY_MEMORY_READER", "legacy")
    before = dict(os.environ)
    native, legacy = await asyncio.gather(
        factory.get_reader("native"), factory.get_reader("legacy")
    )
    assert native.impl.VERSION == "native" and legacy.impl.VERSION == "legacy"
    assert os.environ == before


async def test_T17_illegal_reconcile_configuration():
    with pytest.raises(ValueError):
        MemoryConfig.from_dict({"recall": {"reconcile_retriever": "typo"}})
    config = MemoryConfig()
    config.recall.reconcile_retriever = "typo"
    llm = CaptureLLM()
    result = await MemoryReconciler(config, llm).reconcile(
        ["new"], "alice", "a", object(), FixedEmbed()
    )
    assert not result.success and result.ops == [] and not llm.prompts


@pytest.mark.parametrize("query,limit", [("   ", 10), ("query", 0), ("", 10)])
async def test_skipped_before_reader_embedding_or_scene_llm(query, limit, monkeypatch):
    client = public_client(SimpleNamespace(config=MemoryConfig()))

    async def forbidden(*args, **kwargs):
        raise AssertionError("skipped request must perform no work")

    monkeypatch.setattr(client._registry, "get_reader", forbidden)
    monkeypatch.setattr("hy_memory.coding.judge.analyze_queries", forbidden)
    result = await client.async_search(
        query, reader="native", user_ids=["alice"], search_mode="agentic", limit=limit
    )
    assert result["native"]["skipped"]
    assert not result["memories"]["normal"]


class StrictStore:
    def __init__(self, nodes):
        self.nodes = {n.node_id: n for n in nodes}

    async def native_get_by_ids(self, ids):
        return [self.nodes[nid] for nid in ids if nid in self.nodes]


def hit(n):
    return dict(node=n, node_id=n.node_id, score=0.123456789, matched_id=n.node_id)


async def test_T07_reciprocal_and_multi_ancestor_are_not_cycles():
    a, b, head = node(), node(), node()
    for old in (a, b):
        old.superseded_by, old.is_latest = [head.node_id], False
        old.status = MemoryStatus.SUPERSEDED
    head.supersedes = [a.node_id, b.node_id]
    result = await resolve_native_hits(
        StrictStore([a, b, head]), [hit(a), hit(b)], plan()
    )
    assert len(result) == 1 and result[0]["node_id"] == head.node_id
    assert result[0]["score"] == 0.123456789 and result[0]["matched_id"] == a.node_id


@pytest.mark.parametrize(
    "case", ["missing", "cycle", "scope", "ambiguous", "hops", "nodes"]
)
async def test_T07_integrity_and_budgets(case):
    seed = node()
    nodes = [seed]
    if case == "missing":
        seed.superseded_by = ["missing"]
    elif case == "cycle":
        b = node(superseded_by=[seed.node_id])
        seed.superseded_by = [b.node_id]
        nodes.append(b)
    elif case == "scope":
        head = node(user_id="other")
        seed.superseded_by = [head.node_id]
        nodes.append(head)
    elif case == "ambiguous":
        nodes += [node(), node()]
        seed.superseded_by = [n.node_id for n in nodes[1:]]
    elif case == "hops":
        for _ in range(33):
            head = node()
            nodes[-1].superseded_by = [head.node_id]
            nodes[-1].is_latest = False
            nodes.append(head)
    else:
        nodes += [node() for _ in range(1024)]
        seed.superseded_by = [n.node_id for n in nodes[1:]]
    with pytest.raises(NativeRetrievalError):
        await resolve_native_hits(StrictStore(nodes), [hit(seed)], plan())


def test_T12_native_response_id_mismatch_and_nonfinite():
    store = QdrantVectorStore(MemoryConfig())
    n = node()
    payload = store._node_to_payload(n)
    for point in [
        SimpleNamespace(payload=payload, id="wrong", score=0.5),
        SimpleNamespace(
            payload=payload,
            id=store._node_id_to_point_id(n.node_id),
            score=float("nan"),
        ),
    ]:
        with pytest.raises(NativeRetrievalError):
            store._native_hits([point], "rrf")


@pytest.mark.parametrize(
    "error", [PermissionError("401"), TimeoutError("query timed out")]
)
async def test_T12_backend_failure_is_never_empty_success(error):
    store = QdrantVectorStore(MemoryConfig())

    def fail():
        raise error

    with pytest.raises(NativeRetrievalError) as failure:
        await store._native_call(fail)
    assert failure.value.reason == "backend"
    # Python 3.12's asyncio Future conversion reconstructs TimeoutError when
    # transferring it from the executor; its identity is not an SDK contract.
    # Preserve the diagnostic exception type and arguments on every runtime.
    cause = failure.value.__cause__
    assert cause is not None
    assert type(cause) is type(error)
    assert cause.args == error.args


async def test_hop_budget_stops_reads_early():
    nodes = [node() for _ in range(100)]
    for old, newer in zip(nodes, nodes[1:]):
        old.superseded_by = [newer.node_id]
        old.is_latest = False
    store = StrictStore(nodes)
    calls = []
    original = store.native_get_by_ids

    async def record(ids):
        calls.append(ids)
        return await original(ids)

    store.native_get_by_ids = record
    with pytest.raises(NativeRetrievalError) as failure:
        await resolve_native_hits(store, [hit(nodes[0])], plan())
    assert failure.value.reason == "budget" and len(calls) <= 32
