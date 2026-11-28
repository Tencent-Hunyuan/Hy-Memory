import asyncio
from datetime import datetime
import json

from hy_memory.models.memory import (
    MemoryLayer,
    MemoryNode,
    MemoryStatus,
    SourceType,
)
from hy_memory.pipelines.balanced_catalog import (
    CATALOG_MAX_PER_DOMAIN,
    CATALOG_MAX_TOTAL,
    CATALOG_VERSION,
    CATALOG_SCHEMA_KIND,
    attach_balanced_catalog_to_l0,
    catalog_reject_reason,
    collect_forget_targets,
    compact_verified_catalog_entries,
    eligible_catalog_facts,
    normalize_catalog_selection,
    prepare_catalog_entries,
    run_balanced_catalog_generation,
    suppress_forget_targets,
    source_bank_fingerprint,
)
from hy_memory.pipelines._retrieval.profile_evidence import reverse_lookup_l6
from hy_memory.pipelines.system2_tools import run_existing_schema_compaction


def run(value):
    return asyncio.run(value)


def fact(node_id, content, *, owner="user", status=MemoryStatus.ACTIVE, latest=True):
    return MemoryNode(
        node_id=node_id,
        user_id="u1",
        agent_id="a1",
        layer=MemoryLayer.L2_FACT,
        content=content,
        owner=owner,
        status=status,
        is_latest=latest,
        tags=["tag"],
    )


def catalog(node_id="catalog", content="System2 verified persona catalog:\n- [habits_items] X"):
    return MemoryNode(
        node_id=node_id,
        user_id="u1",
        agent_id="",
        layer=MemoryLayer.L6_SCHEMA,
        content=content,
        status=MemoryStatus.ACTIVE,
        source_type=SourceType.COMPOSITE,
        custom={
            "schema_kind": CATALOG_SCHEMA_KIND,
            "balanced_catalog_version": CATALOG_VERSION,
            "source_bank_fingerprint": "fp",
            "selected_count": 1,
        },
    )


def test_normalize_accepts_wrapped_and_unwrapped_domains():
    wrapped = {"domains": {"habits_items": [2, 1], "health_accessibility": [3]}}
    unwrapped = {"habits_items": [2, 1], "health_accessibility": [3]}
    assert normalize_catalog_selection(wrapped, candidate_count=3) == normalize_catalog_selection(
        unwrapped, candidate_count=3
    )


def test_verifier_rejects_forget_and_generic_questions():
    assert catalog_reject_reason(
        "The user asked the assistant to forget that they like jazz."
    ) == "forget"
    assert catalog_reject_reason(
        "The user asked why jazz harmony sounds unusual."
    ) == "generic_question"
    assert catalog_reject_reason(
        "The user collects first-edition jazz biographies."
    ) == ""


def test_collect_forget_targets_accepts_only_explicit_markers():
    nodes = [
        fact("marker", "The user asked the assistant to forget that they enjoy iced coffee."),
        fact("ordinary", "The user sometimes forgets to bring an umbrella."),
    ]
    assert collect_forget_targets(nodes) == ["they enjoy iced coffee"]


def test_full_anchor_suppression_removes_target_not_weak_overlap():
    entries = [
        {"node_id": "drop", "domain": "preferences_collections",
         "content": "The user enjoys iced coffee."},
        {"node_id": "keep", "domain": "work_education",
         "content": "The user works in a coffee shop supply company."},
    ]
    kept, suppressed = suppress_forget_targets(entries, ["they enjoy iced coffee"])
    assert [row["node_id"] for row in kept] == ["keep"]
    assert suppressed == [{"node_id": "drop", "domain": "preferences_collections"}]


def test_prepare_catalog_entries_keeps_exact_l2_text():
    facts = [
        fact("f1", "The user carries a steel bottle every day."),
        fact("f2", "The user asked the assistant to forget a preference."),
    ]
    entries, rejected = prepare_catalog_entries(
        {"habits_items": [1, 2]}, facts
    )
    assert [row["node_id"] for row in entries] == ["f1"]
    assert entries[0]["content"] == facts[0].content
    assert rejected[0]["reason"] == "forget"


def test_verified_catalog_keeps_four_per_domain_and_thirty_two_total():
    entries = []
    for domain_index, domain in enumerate((
        "identity_relationships",
        "health_accessibility",
        "preferences_collections",
        "habits_items",
        "work_education",
        "culture_creative_experience",
        "values_coping",
        "current_constraints_goals",
    )):
        for item_index in range(4):
            entries.append({
                "domain": domain,
                "node_id": f"d{domain_index}-{item_index}",
                "content": f"fact {domain_index}-{item_index}",
            })
    compacted = compact_verified_catalog_entries(entries)
    assert len(compacted) == CATALOG_MAX_TOTAL == 32
    for domain in {entry["domain"] for entry in compacted}:
        assert sum(entry["domain"] == domain for entry in compacted) \
            == CATALOG_MAX_PER_DOMAIN == 4
    assert [entry["node_id"] for entry in compacted[:4]] == [
        "d0-0", "d0-1", "d0-2", "d0-3",
    ]


def test_verified_compaction_backfills_after_rejected_nominee():
    facts = [
        fact("question", "The user asked why steel bottles are recyclable."),
        fact("keep1", "The user carries a steel bottle every day."),
        fact("keep2", "The user packs a canvas lunch bag for work."),
        fact("keep3", "The user keeps a paper planner on the desk."),
    ]
    selected, rejected = prepare_catalog_entries(
        {"habits_items": [1, 2, 3, 4]}, facts,
    )
    compacted = compact_verified_catalog_entries(selected)
    assert rejected[0]["node_id"] == "question"
    assert [entry["node_id"] for entry in compacted] == [
        "keep1", "keep2", "keep3",
    ]


def test_eligible_facts_are_active_latest_user_l2_only():
    nodes = [
        fact("keep", "The user likes tea."),
        fact("old", "The user liked coffee.", latest=False),
        fact("shadow", "The user likes milk.", status=MemoryStatus.SHADOW),
        fact("agent", "The user likes cake.", owner="assistant"),
        MemoryNode(node_id="l3", layer=MemoryLayer.L3_SUMMARY, content="The user summary"),
    ]
    assert [node.node_id for node in eligible_catalog_facts(nodes)] == ["keep"]


def test_attach_catalog_changes_only_one_l0():
    l0_node = MemoryNode(
        node_id="l0", user_id="u1", agent_id="", layer=MemoryLayer.L0_BASIC_INFO,
        content="The user's name is Alice.",
    )
    original = [{"node_id": "l0", "node": l0_node, "content": l0_node.content, "score": 1.0}]
    updated, info = attach_balanced_catalog_to_l0(original, catalog())
    assert info["attached"] is True
    assert len(updated) == 1
    assert updated[0]["content"] == l0_node.content
    assert "System2 verified persona catalog" in updated[0]["_balanced_catalog_suffix"]
    assert original[0]["content"] == l0_node.content


def test_attach_catalog_builds_synthetic_l0_when_basic_profile_is_missing():
    cat = catalog()
    rows, info = attach_balanced_catalog_to_l0([], cat)
    assert info["attached"] is True
    assert info["synthetic_l0"] is True
    assert len(rows) == 1
    assert rows[0]["node"] is None
    assert rows[0]["layer"] == MemoryLayer.L0_BASIC_INFO.value
    assert rows[0]["content"] == cat.content
    assert rows[0]["source"] == "balanced_catalog_synthetic_l0"


def test_attach_catalog_refuses_multiple_l0():
    cat = catalog()
    rows = [{"node_id": "1"}, {"node_id": "2"}]
    assert attach_balanced_catalog_to_l0(rows, cat)[1]["attached"] is False


class FakeVectorStore:
    def __init__(self, facts):
        self.facts = facts
        self.calls = []

    async def list_by_user(self, **kwargs):
        self.calls.append(kwargs)
        return self.facts

    async def get_by_id(self, node_id):
        return next((node for node in self.facts if node.node_id == node_id), None)


class FakeGraphStore:
    def __init__(self, nodes=None):
        self.nodes = {node.node_id: node for node in (nodes or [])}
        self.upserts = []
        self.links = []
        self.updates = []
        self.fail_update_ids = set()

    async def get_all_nodes(self, isolation_key, layer=None, status=None, limit=100):
        return [
            node for node in self.nodes.values()
            if (layer is None or node.layer == layer)
            and (status is None or node.status == status)
        ][:limit]

    async def get_nodes_by_user(self, user_id, layer=None, status=None, limit=100):
        return [
            node for node in self.nodes.values()
            if node.user_id == user_id
            and (layer is None or node.layer == layer)
            and (status is None or node.status == status)
        ][:limit]

    async def get_node(self, node_id):
        return self.nodes.get(node_id)

    async def upsert_memory_node(self, node):
        self.nodes[node.node_id] = node
        self.upserts.append(node.node_id)
        return node.node_id

    async def ensure_vdbref(self, node_id, layer):
        return None

    async def add_derived_from(self, memory_node_id, vdbref_node_id):
        self.links.append((memory_node_id, vdbref_node_id))
        return True

    async def update_node(self, node_id, updates):
        self.updates.append((node_id, updates))
        if node_id in self.fail_update_ids:
            return False
        node = self.nodes[node_id]
        if "status" in updates:
            node.status = MemoryStatus(updates["status"])
        return True

    async def delete_node(self, node_id):
        self.nodes.pop(node_id, None)
        return True


def test_same_bank_catalog_is_zero_llm_zero_write(monkeypatch):
    facts = [fact("f1", "The user carries a steel bottle every day.")]
    fingerprint = source_bank_fingerprint(facts)
    current = catalog()
    current.custom["source_bank_fingerprint"] = fingerprint
    graph = FakeGraphStore([current])
    monkeypatch.setenv("MEMORY_EVOLING_BALANCED_CATALOG_ENABLED", "true")

    class FailProvider:
        def __init__(self, config):
            raise AssertionError("LLM must not be constructed")

    monkeypatch.setattr(
        "hy_memory.pipelines.balanced_catalog.LLMProvider", FailProvider
    )
    result = run(run_balanced_catalog_generation(
        user_id="u1", vector_store=FakeVectorStore(facts),
        graph_store=graph, config=object(),
    ))
    assert result["reason"] == "current_catalog_exists"
    assert result["prompt_tokens"] == 0
    assert graph.upserts == []
    assert graph.links == []
    assert graph.updates == []


def test_same_bank_legacy_catalog_is_rebuilt_at_current_version(monkeypatch):
    facts = [fact("f1", "The user carries a steel bottle every day.")]
    fingerprint = source_bank_fingerprint(facts)
    current = catalog("old-v2")
    current.custom["balanced_catalog_version"] = 2
    current.custom["source_bank_fingerprint"] = fingerprint
    graph = FakeGraphStore([current])
    FakeProvider.response = json.dumps({"domains": {"habits_items": [1]}})
    FakeProvider.calls = 0
    monkeypatch.setenv("MEMORY_EVOLING_BALANCED_CATALOG_ENABLED", "true")
    monkeypatch.setattr(
        "hy_memory.pipelines.balanced_catalog.LLMProvider", FakeProvider
    )
    result = run(run_balanced_catalog_generation(
        user_id="u1", vector_store=FakeVectorStore(facts),
        graph_store=graph, config=object(),
    ))
    assert result["success"] is True
    assert result["catalog_node_id"] != "old-v2"
    assert graph.nodes["old-v2"].status == MemoryStatus.SUPERSEDED
    assert graph.nodes[result["catalog_node_id"]].custom[
        "balanced_catalog_version"
    ] == CATALOG_VERSION == 4
    assert FakeProvider.calls == 1


def test_cloned_catalog_is_adopted_by_exact_content_without_llm(monkeypatch):
    facts = [
        fact("new-f1", "The user carries a steel bottle every day."),
        fact("new-f2", "The user collects European design books."),
    ]
    old = catalog(
        "cloned-old",
        content=(
            "System2 verified persona catalog:\n"
            "- [preferences_collections] The user collects European design books.\n"
            "- [habits_items] The user carries a steel bottle every day."
        ),
    )
    old.custom.update({
        "balanced_catalog_version": CATALOG_VERSION,
        "source_bank_fingerprint": "source-namespace-fingerprint",
        "selected_count": 2,
        "domains": {
            "preferences_collections": ["source-f2"],
            "habits_items": ["source-f1"],
        },
    })
    graph = FakeGraphStore([old])
    monkeypatch.setenv("MEMORY_EVOLING_BALANCED_CATALOG_ENABLED", "true")

    class FailProvider:
        def __init__(self, config):
            raise AssertionError("clone adoption must not construct the LLM")

    monkeypatch.setattr(
        "hy_memory.pipelines.balanced_catalog.LLMProvider", FailProvider
    )
    result = run(run_balanced_catalog_generation(
        user_id="u1", vector_store=FakeVectorStore(facts),
        graph_store=graph, config=object(),
    ))
    assert result["success"] is True
    assert result["reason"] == "cloned_catalog_adopted"
    assert result["selected_count"] == 2
    assert result["prompt_tokens"] == 0
    adopted = graph.nodes[result["catalog_node_id"]]
    assert adopted.custom["clone_adoption_version"] == "exact-content-remap-v1"
    assert adopted.agent_id == ""
    assert adopted.evidence_chain == ["new-f2", "new-f1"]
    assert adopted.custom["domains"]["preferences_collections"] == ["new-f2"]
    assert graph.nodes["cloned-old"].status == MemoryStatus.SUPERSEDED


def test_agent_scoped_catalog_migrates_to_user_scope_without_llm(monkeypatch):
    facts = [fact("f1", "The user carries a steel bottle every day.")]
    old = catalog(
        "agent-scoped-old",
        content=(
            "System2 verified persona catalog:\n"
            "- [habits_items] The user carries a steel bottle every day."
        ),
    )
    old.agent_id = "agent-a"
    old.evidence_chain = ["f1"]
    old.custom.update({
        "source_bank_fingerprint": source_bank_fingerprint(facts),
        "selected_count": 1,
        "domains": {"habits_items": ["f1"]},
    })
    graph = FakeGraphStore([old])
    monkeypatch.setenv("MEMORY_EVOLING_BALANCED_CATALOG_ENABLED", "true")

    class FailProvider:
        def __init__(self, config):
            raise AssertionError("agent-scope migration must not call the LLM")

    monkeypatch.setattr(
        "hy_memory.pipelines.balanced_catalog.LLMProvider", FailProvider,
    )
    result = run(run_balanced_catalog_generation(
        user_id="u1", vector_store=FakeVectorStore(facts),
        graph_store=graph, config=object(),
    ))

    assert result["reason"] == "cloned_catalog_adopted"
    migrated = graph.nodes[result["catalog_node_id"]]
    assert migrated.agent_id == ""
    assert migrated.custom["profile_scope"] == "user"
    assert graph.nodes["agent-scoped-old"].status == MemoryStatus.SUPERSEDED


def test_catalog_with_any_still_valid_custom_id_is_not_clone_adopted(monkeypatch):
    facts = [fact("f1", "The user carries a steel bottle every day.")]
    old = catalog(
        "same-namespace-old",
        content=(
            "System2 verified persona catalog:\n"
            "- [habits_items] The user carries a steel bottle every day."
        ),
    )
    old.custom.update({
        "balanced_catalog_version": CATALOG_VERSION,
        "source_bank_fingerprint": "old-bank",
        "selected_count": 1,
        "domains": {"habits_items": ["f1"]},
    })
    graph = FakeGraphStore([old])
    FakeProvider.response = json.dumps({"domains": {"habits_items": [1]}})
    FakeProvider.calls = 0
    monkeypatch.setenv("MEMORY_EVOLING_BALANCED_CATALOG_ENABLED", "true")
    monkeypatch.setattr(
        "hy_memory.pipelines.balanced_catalog.LLMProvider", FakeProvider
    )
    result = run(run_balanced_catalog_generation(
        user_id="u1", vector_store=FakeVectorStore(facts),
        graph_store=graph, config=object(),
    ))
    assert result["success"] is True
    assert result.get("reason") != "cloned_catalog_adopted"
    assert FakeProvider.calls == 1


class FakeResponse:
    def __init__(self, content):
        self.content = content
        self.prompt_tokens = 100
        self.completion_tokens = 20


class FakeProvider:
    response = ""
    calls = 0

    def __init__(self, config):
        pass

    async def complete_messages(self, messages, max_tokens):
        type(self).calls += 1
        return FakeResponse(type(self).response)


def test_generation_creates_exact_catalog_and_evidence_links(monkeypatch):
    facts = [
        fact("f1", "The user carries a steel bottle every day."),
        fact("f2", "The user asked why steel bottles are recyclable."),
        fact("f3", "The user collects European design books."),
    ]
    graph = FakeGraphStore()
    FakeProvider.response = json.dumps({
        "domains": {
            "habits_items": [1, 2],
            "preferences_collections": [3],
        }
    })
    FakeProvider.calls = 0
    monkeypatch.setenv("MEMORY_EVOLING_BALANCED_CATALOG_ENABLED", "true")
    monkeypatch.setattr(
        "hy_memory.pipelines.balanced_catalog.LLMProvider", FakeProvider
    )
    result = run(run_balanced_catalog_generation(
        user_id="u1", vector_store=FakeVectorStore(facts),
        graph_store=graph, config=object(),
    ))
    assert result["success"] is True
    assert result["selected_count"] == 2
    assert result["rejected"][0]["node_id"] == "f2"
    assert result["rejected"][0]["reason"] == "generic_question"
    assert FakeProvider.calls == 1
    node = graph.nodes[result["catalog_node_id"]]
    assert node.agent_id == ""
    assert node.custom["schema_kind"] == CATALOG_SCHEMA_KIND
    assert node.custom["balanced_catalog_version"] == 4
    assert node.custom["catalog_max_per_domain"] == 4
    assert node.custom["catalog_max_total"] == 32
    assert node.embedding is None
    assert "The user carries a steel bottle every day." in node.content
    assert "The user collects European design books." in node.content
    # Rendering/evidence order is the frozen domain order, not source index.
    assert [edge[1] for edge in graph.links] == ["f3", "f1"]


def test_generation_uses_cross_agent_user_fact_bank(monkeypatch):
    facts = [
        fact("f-a", "The user carries a steel bottle every day."),
        fact("f-b", "The user collects European design books."),
    ]
    facts[0].agent_id = "agent-a"
    facts[1].agent_id = "agent-b"
    store = FakeVectorStore(facts)
    graph = FakeGraphStore()
    FakeProvider.response = json.dumps({
        "domains": {
            "habits_items": [1],
            "preferences_collections": [2],
        },
    })
    FakeProvider.calls = 0
    monkeypatch.setenv("MEMORY_EVOLING_BALANCED_CATALOG_ENABLED", "true")
    monkeypatch.setattr(
        "hy_memory.pipelines.balanced_catalog.LLMProvider", FakeProvider,
    )

    result = run(run_balanced_catalog_generation(
        user_id="u1", vector_store=store, graph_store=graph, config=object(),
    ))

    assert result["success"] is True
    node = graph.nodes[result["catalog_node_id"]]
    assert node.agent_id == ""
    assert set(node.evidence_chain) == {"f-a", "f-b"}
    assert "agent_id" not in store.calls[0]


def test_generation_suppresses_selected_forget_target(monkeypatch):
    facts = [
        fact("target", "The user enjoys iced coffee."),
        fact("safe", "The user collects European design books."),
        fact(
            "marker",
            "The user asked the assistant to forget that they enjoy iced coffee.",
        ),
    ]
    graph = FakeGraphStore()
    FakeProvider.response = json.dumps({
        "domains": {
            # Candidate order is deterministic by node_id: marker, safe, target.
            "preferences_collections": [2, 3],
        }
    })
    monkeypatch.setenv("MEMORY_EVOLING_BALANCED_CATALOG_ENABLED", "true")
    monkeypatch.setattr(
        "hy_memory.pipelines.balanced_catalog.LLMProvider", FakeProvider
    )
    result = run(run_balanced_catalog_generation(
        user_id="u1", vector_store=FakeVectorStore(facts),
        graph_store=graph, config=object(),
    ))
    assert result["success"] is True
    assert result["selected_count"] == 1
    assert result["suppressed_forget_target_entries"] == [
        {"node_id": "target", "domain": "preferences_collections"}
    ]
    node = graph.nodes[result["catalog_node_id"]]
    assert "iced coffee" not in node.content
    assert "European design books" in node.content
    assert node.custom["suppressed_forget_target_count"] == 1
    assert [edge[1] for edge in graph.links] == ["safe"]


def test_changed_bank_creates_new_version_and_retires_old(monkeypatch):
    old = catalog("old")
    old.custom["source_bank_fingerprint"] = "old-bank"
    graph = FakeGraphStore([old])
    facts = [fact("f1", "The user carries a steel bottle every day.")]
    FakeProvider.response = json.dumps({"domains": {"habits_items": [1]}})
    FakeProvider.calls = 0
    monkeypatch.setenv("MEMORY_EVOLING_BALANCED_CATALOG_ENABLED", "true")
    monkeypatch.setattr(
        "hy_memory.pipelines.balanced_catalog.LLMProvider", FakeProvider
    )
    result = run(run_balanced_catalog_generation(
        user_id="u1", vector_store=FakeVectorStore(facts),
        graph_store=graph, config=object(),
    ))
    assert result["success"] is True
    assert result["catalog_node_id"] != "old"
    assert graph.nodes["old"].status == MemoryStatus.SUPERSEDED
    assert result["retired_catalog_ids"] == ["old"]


def test_failed_retirement_rolls_back_new_catalog(monkeypatch):
    old = catalog("old")
    old.custom["source_bank_fingerprint"] = "old-bank"
    graph = FakeGraphStore([old])
    graph.fail_update_ids.add("old")
    facts = [fact("f1", "The user carries a steel bottle every day.")]
    FakeProvider.response = json.dumps({"domains": {"habits_items": [1]}})
    monkeypatch.setenv("MEMORY_EVOLING_BALANCED_CATALOG_ENABLED", "true")
    monkeypatch.setattr(
        "hy_memory.pipelines.balanced_catalog.LLMProvider", FakeProvider
    )
    result = run(run_balanced_catalog_generation(
        user_id="u1", vector_store=FakeVectorStore(facts),
        graph_store=graph, config=object(),
    ))
    assert result["success"] is False
    assert set(graph.nodes) == {"old"}
    assert graph.nodes["old"].status == MemoryStatus.ACTIVE


class ReverseGraph:
    async def find_referencing_memories(self, ids, limit=50):
        return [
            {
                "node_id": "catalog", "content": "catalog", "layer": "l6_schema",
                "custom_json": json.dumps({"schema_kind": CATALOG_SCHEMA_KIND}),
                "evidence_vdb_id": "f1",
            },
            {
                "node_id": "catalog_supplement_x", "content": "supplement",
                "layer": "l6_schema",
                "custom_json": json.dumps({
                    "schema_kind": "specific_persona_supplement",
                }),
                "evidence_vdb_id": "f1",
            },
            {
                "node_id": "schema", "content": "schema", "layer": "l6_schema",
                "custom_json": json.dumps({"schema_kind": "behavior_pattern"}),
                "evidence_vdb_id": "f1",
            },
        ]


def test_reverse_lookup_excludes_catalog():
    hits = run(reverse_lookup_l6(ReverseGraph(), ["f1"]))
    assert [hit["node_id"] for hit in hits] == ["schema"]


class NoopExecutor:
    pass


class NoopEmbed:
    async def embed_batch(self, texts):
        raise AssertionError("catalog should be excluded before embedding")


def test_schema_compaction_excludes_catalog(monkeypatch):
    monkeypatch.setattr(
        "hy_memory.pipelines.system2_tools._S2_SCHEMA_COMPACTION_ENABLED", True
    )
    graph = FakeGraphStore([catalog("c1"), catalog("c2")])
    result = run(run_existing_schema_compaction(
        graph_store=graph, tool_executor=NoopExecutor(), embed_service=NoopEmbed(),
        isolation_key="u1::a1::default_session",
    ))
    assert result["scanned"] == 0
    assert result["merged"] == 0


def test_normalize_catalog_selection_respects_max_total():
    """max_total 达到上限后必须停止选入。

    原来 `len(used) >= max_total` 的 break 只跳出当前 domain 的内层循环，
    外层 domain 循环继续往 used 里加，总量上限形同虚设。
    """
    from hy_memory.pipelines.balanced_catalog import CATALOG_DOMAINS

    raw = {domain: [1, 2, 3, 4, 5, 6] for domain in CATALOG_DOMAINS}
    out = normalize_catalog_selection(
        raw, candidate_count=20, max_per_domain=4, max_total=5,
    )
    total = sum(len(values) for values in out.values())
    assert total == 5, f"max_total=5 但选中了 {total} 项: {out}"
