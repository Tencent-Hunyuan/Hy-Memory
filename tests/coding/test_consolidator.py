"""Whole-batch model validation and candidate-vector publication failures."""

import asyncio
import json
from copy import deepcopy
from threading import Event

import pytest

from hy_memory.coding.consolidator import ExperienceConsolidator
from hy_memory.coding.types import CodingWriteError, ConsolidationConflict, ConsolidationOutcomeUnknown
from ._helpers import MockLLMProvider, FailingLLMProvider
from .test_consolidation_repository import seed


def response(**changes):
    item = {
        "task": "avoid lock inversion", "search_keys": ["deadlock"],
        "solution": "acquire source locks before group locks; proposed, not tested",
        "boundary_envs": "transactional database; opposite environment requires review",
        "confidence": 0.7, "verification_status": "unverified",
        "supports": [{"kind": "fact", "material_id": "s1", "reason": "observed lock inversion"}],
        "conditions": [{"kind": "fact", "material_id": "s2", "reason": "transaction boundary applies"}],
        "counterexamples": [],
    }
    item.update(changes)
    return {"experiences": [item]}


async def prepared(coding_store):
    scope = seed(coding_store._repository)
    provider = MockLLMProvider(json.dumps(response()))
    consolidator = ExperienceConsolidator(coding_store, provider, coding_store.config)
    return scope, provider, consolidator


async def test_complete_input_and_reuse_are_order_independent(coding_store):
    scope, provider, consolidator = await prepared(coding_store)
    result = await consolidator.consolidate(scope, ["s1", "s2", "s3"])
    assert result.status == "published"
    assert len(result.receipt.read_set) == 3
    assert "evidence s3" in provider.calls[0]["prompt"]
    assert provider.calls[0]["max_tokens"] == 4096
    memory = result.receipt.experiences[0]
    assert {ref["session_id"] for ref in memory.evidence_refs} == {"s1", "s2"}
    assert all(ref["verification_status"] == "unverified" for ref in memory.evidence_refs)
    replay = await consolidator.consolidate(scope, ["s3", "s2", "s1", "s1"])
    assert replay.reused and replay.receipt.experience_ids == result.receipt.experience_ids
    assert len(provider.calls) == 1
    query = await coding_store.embed_service.embed(memory.task)
    assert memory.experience_id in {h["experience_id"] for h in await coding_store.search_by_query_embedding(query, "u")}
    assert await coding_store.search_by_query_embedding(query, "u", session_ids=["s1", "s2"]) == []


@pytest.mark.parametrize("output", [
    "", " ", "{", "{}", "null", '```json\n{"experiences":[]}\n```',
    '{"experiences":[],"experiences":[]}', '{"experiences":null}',
    json.dumps(response(task="")), json.dumps(response(confidence=True)),
    json.dumps(response(confidence=2)), json.dumps(response(verification_status="planned")),
    json.dumps(response(supports=[])), json.dumps(response(conditions=[])),
    json.dumps(response(supports=[{"kind": "fact", "material_id": "unknown", "reason": "not an input"}])),
    json.dumps({"experiences": [response()["experiences"][0], {"task": "partial invalid item"}]}),
])
async def test_bad_output_fails_the_entire_batch(coding_store, output):
    scope, _, _ = await prepared(coding_store)
    consolidator = ExperienceConsolidator(coding_store, MockLLMProvider(output), coding_store.config)
    with pytest.raises(CodingWriteError):
        await consolidator.consolidate(scope, ["s1", "s2"])
    assert (await coding_store.stats("u"))["count"] == 0
    assert coding_store._conn.execute("SELECT COUNT(*) FROM consolidation_receipt").fetchone()[0] == 0


async def test_truncation_model_failure_budget_and_missing_sessions(coding_store):
    scope, provider, consolidator = await prepared(coding_store)
    for sessions in (["s1", "s1"], ["s1", "missing"]):
        with pytest.raises(ValueError):
            await consolidator.consolidate(scope, sessions)
    assert provider.calls == []
    coding_store.config.coding.consolidation_max_input_tokens = 10
    with pytest.raises(CodingWriteError, match="budget"):
        await consolidator.consolidate(scope, ["s1", "s2"])
    assert provider.calls == []
    coding_store.config.coding.consolidation_max_input_tokens = 16000
    provider = MockLLMProvider({"content": json.dumps(response()), "finish_reason": "length"})
    with pytest.raises(CodingWriteError) as truncated:
        await ExperienceConsolidator(coding_store, provider, coding_store.config).consolidate(scope, ["s1", "s2"])
    assert truncated.value.error_code == "OUTPUT_TRUNCATED"
    with pytest.raises(CodingWriteError) as failed:
        await ExperienceConsolidator(coding_store, FailingLLMProvider(), coding_store.config).consolidate(scope, ["s1", "s2"])
    assert failed.value.error_code == "LLM_ERROR"


async def test_valid_no_result_is_a_reusable_published_decision(coding_store):
    scope, _, _ = await prepared(coding_store)
    provider = MockLLMProvider('{"experiences":[]}')
    consolidator = ExperienceConsolidator(coding_store, provider, coding_store.config)
    result = await consolidator.consolidate(scope, ["s1", "s2"])
    replay = await consolidator.consolidate(scope, ["s2", "s1"])
    assert result.status == replay.status == "no_result"
    assert result.receipt.is_current and replay.reused
    assert len(provider.calls) == 1


@pytest.mark.parametrize("failure", ["raise", "partial", "wrong_ids"])
async def test_vector_failure_cannot_publish(coding_store, monkeypatch, failure):
    scope, _, consolidator = await prepared(coding_store)
    original = coding_store._vec.upsert_batch

    async def fail(nodes):
        await original(nodes[:1])
        if failure == "raise":
            raise RuntimeError("partial write before network failure")
        return [nodes[0].node_id] if failure == "partial" else ["wrong"] * len(nodes)

    monkeypatch.setattr(coding_store._vec, "upsert_batch", fail)
    with pytest.raises(CodingWriteError) as error:
        await consolidator.consolidate(scope, ["s1", "s2"])
    assert error.value.error_code == "VECTOR_WRITE_FAILED"
    assert (await coding_store.stats("u"))["count"] == 0
    query = await coding_store.embed_service.embed("avoid lock inversion")
    assert await coding_store.search_by_query_embedding(query, "u") == []


async def test_second_product_vector_failure_does_not_publish_the_first(coding_store, monkeypatch):
    scope, _, _ = await prepared(coding_store)
    output = response()
    output["experiences"].append(response(task="independent second topic")["experiences"][0])
    consolidator = ExperienceConsolidator(coding_store, MockLLMProvider(json.dumps(output)), coding_store.config)
    original = coding_store.prewrite_consolidated
    attempted = []

    async def second_fails(memory):
        attempted.append(memory.experience_id)
        await original(memory)
        if len(attempted) == 2:
            raise RuntimeError("second product vector acknowledgement lost")

    monkeypatch.setattr(coding_store, "prewrite_consolidated", second_fails)
    with pytest.raises(RuntimeError, match="second product"):
        await consolidator.consolidate(scope, ["s1", "s2"])
    assert len(set(attempted)) == 2
    assert (await coding_store.stats("u"))["count"] == 0
    assert await coding_store.search_by_query_embedding(await coding_store.embed_service.embed("avoid lock inversion"), "u") == []


@pytest.mark.parametrize("vectors", [[], [[1.0]], [[float("nan")] * 1536], [[0.0] * 1536]])
async def test_invalid_embeddings_are_rejected(coding_store, monkeypatch, vectors):
    scope, _, consolidator = await prepared(coding_store)

    async def invalid(texts):
        return vectors if not vectors else vectors * len(texts)

    monkeypatch.setattr(coding_store.embed_service, "embed_batch", invalid)
    with pytest.raises(CodingWriteError) as error:
        await consolidator.consolidate(scope, ["s1", "s2"])
    assert error.value.error_code == "EMBEDDING_FAILED"


async def test_unpublished_candidates_and_unreferenced_invalidation_hide_matched_keys(coding_store, monkeypatch):
    scope, _, consolidator = await prepared(coding_store)
    original = coding_store._repository.publish_consolidation
    query = await coding_store.embed_service.embed("avoid lock inversion")
    entered, release = asyncio.Event(), asyncio.Event()
    prewrite = coding_store.prewrite_consolidated

    async def hold(memory):
        await prewrite(memory)
        entered.set()
        await release.wait()

    monkeypatch.setattr(coding_store, "prewrite_consolidated", hold)
    task = asyncio.create_task(consolidator.consolidate(scope, ["s1", "s2", "s3"]))
    await asyncio.wait_for(entered.wait(), 10)
    assert await coding_store.search_by_query_embedding(query, "u") == []
    release.set()
    result = await task
    assert result.receipt.experience_ids
    fact = coding_store._repository.get_facts(["s3"], "u")[0]
    fact.statement = "changed but never cited"
    coding_store._repository.upsert_fact(fact)
    assert await coding_store.search_by_query_embedding(query, "u") == []
    assert original is not None


@pytest.mark.parametrize("committed", [False, True])
async def test_unknown_commit_keeps_vectors_and_can_be_verified(coding_store, monkeypatch, committed):
    scope, _, consolidator = await prepared(coding_store)
    original = coding_store._repository.publish_consolidation

    def uncertain(*args, **kwargs):
        if committed:
            original(*args, **kwargs)
        raise ConsolidationOutcomeUnknown("commit acknowledgement lost")

    monkeypatch.setattr(coding_store._repository, "publish_consolidation", uncertain)
    result = await consolidator.consolidate(scope, ["s1", "s2"])
    assert result.status == "outcome_unknown"
    receipt = coding_store._repository.get_consolidation(result.consolidation_id, "u")
    assert (receipt is not None) == committed
    raw = await coding_store._vec.search(
        query_embedding=await coding_store.embed_service.embed("avoid lock inversion"),
        user_id="u", limit=10,
    )
    assert raw  # Never clean vectors on uncertain commit.


async def test_source_race_cleans_only_losing_attempt(coding_store, monkeypatch):
    scope, _, consolidator = await prepared(coding_store)
    original = coding_store.prewrite_consolidated

    async def changed(memory):
        await original(memory)
        fact = coding_store._repository.get_facts(["s2"], "u")[0]
        fact.statement = "changed before commit"
        coding_store._repository.upsert_fact(fact)

    monkeypatch.setattr(coding_store, "prewrite_consolidated", changed)
    with pytest.raises(ConsolidationConflict):
        await consolidator.consolidate(scope, ["s1", "s2"])
    assert (await coding_store.stats("u"))["count"] == 0


async def test_cancel_during_publication_returns_stable_unknown_outcome(coding_store, monkeypatch):
    scope, _, consolidator = await prepared(coding_store)
    original = coding_store._repository.publish_consolidation
    entered, release, committed = Event(), Event(), Event()

    def pause(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        result = original(*args, **kwargs)
        committed.set()
        return result

    monkeypatch.setattr(coding_store._repository, "publish_consolidation", pause)
    task = asyncio.create_task(consolidator.consolidate(scope, ["s1", "s2"]))
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        task.cancel()
        result = await task
        assert result.status == "outcome_unknown" and result.consolidation_id
    finally:
        release.set()
        assert await asyncio.to_thread(committed.wait, 10)
    receipt = coding_store._repository.get_consolidation(result.consolidation_id, "u")
    assert receipt.is_current and receipt.experience_ids


async def test_hidden_historical_vectors_do_not_starve_the_current_batch(coding_store):
    scope, _, _ = await prepared(coding_store)
    current_id = None
    for generation in range(12):
        fact = coding_store._repository.get_facts(["s1"], "u")[0]
        fact.statement = f"basis generation {generation}"
        coding_store._repository.upsert_fact(fact)
        output = response(task="exact query" if generation < 11 else "relevant current experience", search_keys=[])
        provider = MockLLMProvider(json.dumps(output))
        result = await ExperienceConsolidator(coding_store, provider, coding_store.config).consolidate(scope, ["s1", "s2"])
        current_id = result.receipt.experience_ids[0]
    query = await coding_store.embed_service.embed("exact query")
    hits = await coding_store.search_by_query_embedding(query, "u", top=8)
    assert [hit["experience_id"] for hit in hits] == [current_id]
    assert (await coding_store.stats("u"))["count"] == 1


async def test_withdrawal_and_late_vector_rewrite_never_leak_keys(coding_store, monkeypatch):
    scope, _, consolidator = await prepared(coding_store)
    result = await consolidator.consolidate(scope, ["s1", "s2"])
    memory = deepcopy(result.receipt.experiences[0])

    async def failed_delete(node_id):
        raise RuntimeError("vector service unavailable")

    monkeypatch.setattr(coding_store._vec, "delete", failed_delete)
    assert await coding_store.delete(memory.experience_id, "u")
    await coding_store.prewrite_consolidated(memory)  # Delayed write arrives after SQL withdrawal.
    replay = await consolidator.consolidate(scope, ["s1", "s2"])
    assert replay.receipt.experiences == []
    assert replay.receipt.withdrawn_ids == [memory.experience_id]
    assert replay.receipt.decision == "published"
    assert await coding_store.search_by_query_embedding(await coding_store.embed_service.embed(memory.task), "u") == []
