"""Exercise the lexical operation interface with real SQLite FTS5."""

import json
import sqlite3
from contextlib import closing

import pytest

from hy_memory.data.chroma_lexical_index import ChromaLexicalIndex


class Collection:
    """Deterministic authoritative store boundary; SQLite itself is not mocked."""

    def __init__(self, rows):
        self.rows = {key: dict(value) for key, value in rows.items()}
        self.calls = []

    def count(self):
        return len(self.rows)

    def get(self, *, ids=None, limit=None, offset=0, include=None):
        self.calls.append({"ids": ids, "include": include})
        selected = list(self.rows) if ids is None else [key for key in ids if key in self.rows]
        selected = selected[offset:offset + limit] if limit is not None else selected[offset:]
        return {
            "ids": selected,
            "metadatas": [dict(self.rows[key]) for key in selected],
            "documents": [self.rows[key].get("content", "") for key in selected],
        }


def metadata(content="ECONNRESET", *, node_id="node", **updates):
    return {
        "content": content, "tags": "[]", "node_id": node_id,
        "user_id": "u1", "agent_id": "a1", "isolation_key": "u1:a1:s1",
        "source": "allowed", "layer": "l2_fact", "status": "active",
        "is_latest": True, **updates,
    }


def scope(**included):
    return {"in": {"status": ["active"], "is_latest": [True], **included}, "not_in": {}}


def ready(tmp_path, rows):
    collection = Collection(rows)
    index = ChromaLexicalIndex(str(tmp_path), "memories", "collection-1")
    assert index.audit(collection)["state"] == "ready"
    return index, collection


def ids(index, collection, query, *, selected=None, top_k=10):
    return [
        hit["node_id"] for hit in index.search(collection, query, top_k, selected or scope())
    ]


def test_real_fts_search_filters_before_limit_and_materializes_one_batch(tmp_path):
    rows = {
        f"p{number}": metadata(node_id=f"blocked{number}", source="blocked")
        for number in range(100)
    }
    rows["target"] = metadata(node_id="target")
    index, collection = ready(tmp_path, rows)
    collection.calls.clear()
    assert ids(index, collection, "ECONNRESET", selected=scope(source=["allowed"]), top_k=1) == ["target"]
    assert collection.calls == [{"ids": ["target"], "include": ["metadatas", "documents"]}]


def test_current_content_and_tags_override_historical_search_text(tmp_path):
    index, collection = ready(tmp_path, {
        "p": metadata("newkeyword", tags=json.dumps(["ECONNRESET"]), search_text="oldkeyword"),
    })
    assert ids(index, collection, "oldkeyword") == []
    assert ids(index, collection, "newkeyword") == ["node"]
    assert ids(index, collection, "ECONNRESET") == ["node"]


def test_legacy_document_fallback_materializes_consistent_content_and_node_id(tmp_path):
    row = metadata()
    del row["content"]
    del row["node_id"]
    collection = Collection({"point-id": row})
    get = collection.get

    def legacy_get(**kwargs):
        page = get(**kwargs)
        page["documents"] = ["ECONNRESET" for _ in page["ids"]]
        return page

    collection.get = legacy_get
    index = ChromaLexicalIndex(str(tmp_path), "memories", "collection-1")
    assert index.audit(collection)["state"] == "ready"
    hits = index.search(collection, "ECONNRESET", 10, scope())
    assert hits[0]["node_id"] == "point-id"
    assert hits[0]["metadata"]["node_id"] == "point-id"
    assert hits[0]["metadata"]["content"] == "ECONNRESET"


def test_mutation_removes_old_content_and_adds_new_terms(tmp_path):
    index, collection = ready(tmp_path, {"p": metadata("oldkeyword")})

    def update():
        collection.rows["p"]["content"] = "newkeyword"
        return "primary-success", ["p"]

    assert index.mutate(collection, update) == "primary-success"
    assert ids(index, collection, "oldkeyword") == []
    assert ids(index, collection, "newkeyword") == ["node"]
    assert index.get_status()["state"] == "ready"


def test_filter_only_change_does_not_reanalyze(tmp_path, monkeypatch):
    index, collection = ready(tmp_path, {"p": metadata()})

    def forbidden(*args, **kwargs):
        raise AssertionError("filter-only mutation analyzed text")

    monkeypatch.setattr("hy_memory.data.chroma_lexical_index.analyze_document", forbidden)

    def update():
        collection.rows["p"]["source"] = "repaired"
        return True, ["p"]

    assert index.mutate(collection, update) is True
    assert index.get_status()["state"] == "ready"
    assert ids(index, collection, "ECONNRESET", selected=scope(source=["allowed"])) == []
    assert ids(index, collection, "ECONNRESET", selected=scope(source=["repaired"])) == ["node"]


def test_delete_removes_projection(tmp_path):
    index, collection = ready(tmp_path, {"p": metadata()})

    def delete():
        del collection.rows["p"]
        return 1, ["p"]

    assert index.mutate(collection, delete) == 1
    assert ids(index, collection, "ECONNRESET") == []
    assert index.get_status()["record_count"] == 0


def test_audit_compares_content_and_full_ids_not_only_count(tmp_path):
    index, collection = ready(tmp_path, {
        "p": metadata("oldkeyword", node_id="old"),
        "q": metadata("other", node_id="unchanged"),
    })
    del collection.rows["p"]
    collection.rows["r"] = metadata("newkeyword", node_id="replacement")
    collection.rows["q"]["source"] = "repaired"
    fresh = ChromaLexicalIndex(str(tmp_path), "memories", "collection-1")
    assert fresh.search(collection, "oldkeyword", 10, scope()) == []
    assert fresh.audit(collection)["state"] == "ready"
    assert ids(fresh, collection, "oldkeyword") == []
    assert ids(fresh, collection, "newkeyword") == ["replacement"]
    assert ids(fresh, collection, "other", selected=scope(source=["repaired"])) == ["unchanged"]


def test_same_name_new_collection_identity_cannot_reuse_old_projection(tmp_path):
    index, collection = ready(tmp_path, {"p": metadata("oldkeyword")})
    replacement = Collection({"q": metadata("newkeyword", node_id="new")})
    fresh = ChromaLexicalIndex(str(tmp_path), "memories", "collection-2")
    assert fresh.audit(replacement)["state"] == "ready"
    assert ids(fresh, replacement, "oldkeyword") == []
    assert ids(fresh, replacement, "newkeyword") == ["new"]


def test_rebuild_more_than_one_page_and_unchanged_audit_skips_analysis(tmp_path, monkeypatch):
    rows = {
        str(number): metadata(f"item {number} ECONNRESET", node_id=f"n{number:04}")
        for number in range(1201)
    }
    index, collection = ready(tmp_path, rows)
    before = ids(index, collection, "ECONNRESET")

    def forbidden(*args, **kwargs):
        raise AssertionError("unchanged audit reanalyzed text")

    with monkeypatch.context() as patched:
        patched.setattr("hy_memory.data.chroma_lexical_index.analyze_document", forbidden)
        assert index.audit(collection)["state"] == "ready"
    assert index.audit(collection, force=True)["record_count"] == 1201
    assert ids(index, collection, "ECONNRESET") == before


def test_scope_composes_all_conditions_and_source_exclusion_wins(tmp_path):
    rows = {
        "good": metadata(node_id="good"),
        "user": metadata(node_id="user", user_id="other"),
        "agent": metadata(node_id="agent", agent_id="other"),
        "layer": metadata(node_id="layer", layer="l3_summary"),
        "old": metadata(node_id="old", is_latest=False),
        "shadow": metadata(node_id="shadow", status="shadow"),
        "source": metadata(node_id="source", source="blocked"),
    }
    index, collection = ready(tmp_path, rows)
    selected = scope(user_id=["u1"], agent_id=["a1"], layer=["l2_fact"], source=["allowed", "blocked"])
    selected["not_in"] = {"source": ["blocked"]}
    assert ids(index, collection, "ECONNRESET", selected=selected) == ["good"]


def test_equal_score_ties_are_stable_by_node_id(tmp_path):
    index, collection = ready(tmp_path, {
        "z": metadata(node_id="zeta"), "a": metadata(node_id="alpha"),
    })
    assert ids(index, collection, "ECONNRESET", top_k=1) == ["alpha"]


def test_uncoordinated_stale_candidate_is_rejected_then_audited(tmp_path):
    index, collection = ready(tmp_path, {"p": metadata("oldkeyword")})
    collection.rows["p"]["content"] = "newkeyword"
    assert ids(index, collection, "oldkeyword") == []
    assert index.get_status()["state"] == "dirty"
    assert index.audit(collection)["state"] == "ready"
    assert ids(index, collection, "newkeyword") == ["node"]


def test_initialize_rebuilds_incompatible_sql_schema(tmp_path):
    index, collection = ready(tmp_path, {"p": metadata()})
    with closing(sqlite3.connect(index.path)) as connection:
        connection.execute("ALTER TABLE documents RENAME COLUMN text_hash TO old_hash")
        connection.execute("UPDATE index_meta SET schema_version=0")
        connection.commit()
    fresh = ChromaLexicalIndex(str(tmp_path), "memories", "collection-1")
    assert fresh.audit(collection)["state"] == "ready"
    assert ids(fresh, collection, "ECONNRESET") == ["node"]


def test_initialize_repairs_missing_fts_rows_without_count_change_in_chroma(tmp_path):
    index, collection = ready(tmp_path, {"p": metadata()})
    with closing(sqlite3.connect(index.path)) as connection:
        connection.execute("DELETE FROM documents_fts")
        connection.commit()
    fresh = ChromaLexicalIndex(str(tmp_path), "memories", "collection-1")
    assert fresh.audit(collection)["state"] == "ready"
    assert ids(fresh, collection, "ECONNRESET") == ["node"]


def test_query_failure_requires_successful_reaudit_for_this_instance(tmp_path, monkeypatch):
    index, collection = ready(tmp_path, {"p": metadata()})
    original = collection.get

    def unavailable(**kwargs):
        raise RuntimeError("synthetic authoritative outage")

    with monkeypatch.context() as patched:
        patched.setattr(collection, "get", unavailable)
        assert ids(index, collection, "ECONNRESET") == []
    assert collection.get == original
    assert index.get_status()["state"] != "ready"
    assert ids(index, collection, "ECONNRESET") == []
    assert index.audit(collection)["state"] == "ready"
    assert ids(index, collection, "ECONNRESET") == ["node"]


@pytest.mark.parametrize("query", ["", "  ", "\"", "()", "src/foo_bar.py", "OR NEAR", "v1.2.3"])
def test_user_input_never_becomes_raw_fts_syntax(tmp_path, query):
    index, collection = ready(tmp_path, {"p": metadata("synthetic")})
    assert isinstance(index.search(collection, query, 10, scope()), list)
    assert index.get_status()["state"] == "ready"
