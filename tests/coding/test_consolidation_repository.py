"""Atomic consolidation contract against SQLite and real PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
from threading import Barrier, Event

import pytest

from hy_memory.coding.evidence import _own_details, project, reference
from hy_memory.coding.types import CodingMemory, ConsolidationConflict, ConsolidationScope, WorkingFact


def seed(repo, *, sessions=("s1", "s2", "s3"), **scope_fields):
    scope = ConsolidationScope("u", "a", "default", "ws", "main", **scope_fields)
    for session in sessions:
        repo.upsert_fact(WorkingFact(
            fact_id=session, user_id="u", agent_id="a", session_id=session,
            workspace_id="ws", branch="main", statement="evidence " + session,
            details="own " + session, memory_at=datetime(2026, 1, 1),
        ))
    return scope


def product(snapshot, key="derived"):
    scope = snapshot.scope
    return CodingMemory(
        experience_id=key, user_id=scope.user_id, agent_id=scope.agent_id,
        source=scope.source, workspace_id=scope.workspace_id, branch=scope.branch,
        task="reusable", solution="preserve lock order", boundary_envs="same database",
        consolidation_id=snapshot.consolidation_id, extraction_source="consolidation",
        created_at=datetime(2026, 1, 2), updated_at=datetime(2026, 1, 2),
        evidence_refs=[{**r.to_dict(), "role": "supports"} for r in snapshot.read_set[:2]],
    )


def test_exact_snapshot_and_own_evidence(repositories):
    _, create, _ = repositories
    repo = create()
    scope = seed(repo)
    fact = repo.get_facts(["s1"], "u")[0]
    before = reference(project(fact))
    fact.details += "\n--- inherited from superseded facts ---\nancestor secret"
    fact.fact_chain_summary = "chain secret"
    fact.status = "superseded"
    fact.fact_link = "s2"
    repo.upsert_fact(fact)
    snapshot = repo.consolidation_snapshot(scope, ["s3", "s1", "s2", "s1"])
    assert len(snapshot.materials) == 3
    assert snapshot.session_ids == ["s1", "s2", "s3"]
    assert before == reference(project(fact))
    assert "secret" not in str(snapshot.materials)
    assert _own_details(fact.details) == "own s1"
    with pytest.raises(ValueError):
        repo.consolidation_snapshot(scope, ["s1", "missing"])
    with pytest.raises(ValueError):
        repo.consolidation_snapshot(ConsolidationScope("u", "a", "default", None, None), ["s1", "s2"])


def test_publication_visibility_session_subset_and_metadata(repositories):
    _, create, _ = repositories
    repo = create()
    scope = seed(repo)
    snapshot = repo.consolidation_snapshot(scope, ["s1", "s2", "s3"])
    memory = product(snapshot)
    receipt = repo.publish_consolidation(snapshot, [memory], "attempt")
    assert receipt.is_current and receipt.basis_valid and receipt.activation_epoch == 1
    assert receipt.experiences[0].to_dict() == memory.to_dict()
    assert memory.memory_at is None and memory.session_id is None
    assert repo.stats("u", "ws", "main")["count"] == 1
    assert repo.list_user_tasks("u", 10) == [memory.task]
    assert len(repo.list_by_workspace("u", "ws", "main", 1, 0)) == 1
    assert repo.scoped_experience_ids("u", ["a"], ["s1", "s2"], "ws", "main")[0] == set()
    assert repo.scoped_experience_ids("u", ["a"], ["s1", "s2", "s3"], "ws", "main")[0] == {"derived"}
    assert repo.get_consolidation(receipt.consolidation_id, "other") is None
    assert "consolidation_id" not in CodingMemory("normal", "u").to_dict()
    # Generated products are not recursively consumed.
    assert repo.consolidation_snapshot(scope, snapshot.session_ids).basis_digest == snapshot.basis_digest


@pytest.mark.parametrize("mutation", ["fact_edit", "fact_delete", "experience_edit", "experience_delete"])
def test_all_read_set_changes_invalidate_atomically(repositories, mutation):
    _, create, _ = repositories
    repo = create()
    scope = seed(repo)
    ordinary = CodingMemory("ordinary", "u", agent_id="a", session_id="s3", workspace_id="ws",
                            branch="main", task="original", solution="result")
    repo.upsert_experience(ordinary)
    snapshot = repo.consolidation_snapshot(scope, ["s1", "s2", "s3"])
    repo.publish_consolidation(snapshot, [product(snapshot)], "one")
    if mutation == "fact_edit":
        fact = repo.get_facts(["s3"], "u")[0]
        fact.statement = "changed unreferenced input"
        repo.upsert_fact(fact)
    elif mutation == "fact_delete":
        repo.delete_fact("s3", "u")
    elif mutation == "experience_edit":
        ordinary.solution = "changed unreferenced experience"
        repo.upsert_experience(ordinary)
    else:
        repo.delete_experience("ordinary", "u")
    assert repo.get_experiences(["derived"], "u", None, None) == []
    assert repo.get_experience("derived", "u") is None
    assert "derived" not in repo.scoped_experience_ids("u", None, None, None, None)[0]
    assert not any(m.consolidation_id for m in repo.list_by_workspace("u", "ws", None, 10, 0))
    assert repo.stats("u", None, None)["count"] == (1 if mutation in ("fact_edit", "fact_delete", "experience_edit") else 0)
    receipt = repo.get_consolidation(snapshot.consolidation_id, "u")
    assert receipt.to_dict()["status"] == "needs_review"
    assert len(receipt.experiences) == 1  # explicitly marked diagnostic body
    statuses = {r["status"] for r in receipt.read_set}
    assert ("missing" if mutation.endswith("delete") else "changed") in statuses


def test_relations_and_new_material_do_not_invalidate(repositories):
    _, create, _ = repositories
    repo = create()
    scope = seed(repo)
    snapshot = repo.consolidation_snapshot(scope, ["s1", "s2"])
    repo.publish_consolidation(snapshot, [product(snapshot)], "one")
    fact = repo.get_facts(["s1"], "u")[0]
    fact.fact_chain_summary = "summary"
    fact.fact_link = "s2"
    fact.derived_experience_ids = ["ordinary"]
    fact.status = "superseded"
    fact.details += "\n--- inherited from superseded facts ---\nancestor"
    repo.upsert_fact(fact)
    repo.upsert_fact(WorkingFact("new", "u", agent_id="a", session_id="s1",
                                workspace_id="ws", branch="main", statement="new input"))
    assert repo.get_consolidation(snapshot.consolidation_id, "u").basis_valid
    assert repo.consolidation_snapshot(scope, ["s1", "s2"]).basis_digest != snapshot.basis_digest


def test_review_regeneration_does_not_reveal_previous_manifest(repositories):
    _, create, _ = repositories
    repo = create()
    scope = seed(repo)
    snapshot = repo.consolidation_snapshot(scope, ["s1", "s2"])
    repo.publish_consolidation(snapshot, [product(snapshot, "old")], "one")
    original = repo.get_facts(["s1"], "u")[0]
    changed = deepcopy(original)
    changed.statement = "B"
    repo.upsert_fact(changed)
    repo.upsert_fact(original)
    assert not repo.get_consolidation(snapshot.consolidation_id, "u").basis_valid
    restored = repo.consolidation_snapshot(scope, ["s1", "s2"])
    assert restored.reusable is None
    receipt = repo.publish_consolidation(restored, [product(restored, "new")], "two")
    assert receipt.activation_epoch == 2 and receipt.experience_ids == ["new"]
    assert repo.get_experience("old", "u") is None
    assert repo.scoped_experience_ids("u", None, None, None, None)[0] == {"new"}


def test_withdrawal_reuse_and_empty_decision(repositories):
    _, create, _ = repositories
    repo = create()
    scope = seed(repo)
    a = repo.consolidation_snapshot(scope, ["s1", "s2"])
    repo.publish_consolidation(a, [product(a, "one"), product(a, "two")], "one")
    repo.delete_experience("one", "u")
    replay = repo.consolidation_snapshot(scope, ["s2", "s1", "s1"])
    receipt = repo.publish_consolidation(replay, [], "ignored", reuse=True)
    assert [m.experience_id for m in receipt.experiences] == ["two"]
    assert receipt.withdrawn_ids == ["one"]
    repo.delete_experience("two", "u")
    receipt = repo.get_consolidation(a.consolidation_id, "u")
    assert receipt.decision == "published" and receipt.experiences == []
    repo.upsert_fact(WorkingFact("extra", "u", agent_id="a", session_id="s1",
                                workspace_id="ws", branch="main", statement="extra"))
    b = repo.consolidation_snapshot(scope, ["s1", "s2"])
    empty = repo.publish_consolidation(b, [], "empty")
    assert empty.decision == "no_result" and empty.is_current
    repo.delete_fact("extra", "u")
    back = repo.consolidation_snapshot(scope, ["s1", "s2"])
    result = repo.publish_consolidation(back, [], "reactivate", reuse=True)
    assert result.consolidation_id == a.consolidation_id
    assert result.activation_epoch == 3 and result.experiences == []
    assert result.withdrawn_ids == ["one", "two"] and result.decision == "published"


def test_frozen_source_and_aba_conflicts(repositories):
    _, create, _ = repositories
    repo = create()
    scope = seed(repo)
    a = repo.consolidation_snapshot(scope, ["s1", "s2"])
    repo.publish_consolidation(a, [product(a)], "a")
    stale = repo.consolidation_snapshot(scope, ["s1", "s2"])
    repo.upsert_fact(WorkingFact("extra", "u", agent_id="a", session_id="s1",
                                workspace_id="ws", branch="main", statement="extra"))
    b = repo.consolidation_snapshot(scope, ["s1", "s2"])
    repo.publish_consolidation(b, [], "b")
    repo.delete_fact("extra", "u")
    back = repo.consolidation_snapshot(scope, ["s1", "s2"])
    repo.publish_consolidation(back, [], "a-again", reuse=True)
    with pytest.raises(ConsolidationConflict, match="activation"):
        repo.publish_consolidation(stale, [], "stale", reuse=True)
    fact = repo.get_facts(["s1"], "u")[0]
    fact.statement = "changed"
    repo.upsert_fact(fact)
    with pytest.raises(ConsolidationConflict, match="evidence"):
        repo.publish_consolidation(back, [], "bad", reuse=True)


def test_ordinary_writes_and_backfill_cannot_modify_derived(repositories):
    _, create, _ = repositories
    repo = create()
    scope = seed(repo)
    snapshot = repo.consolidation_snapshot(scope, ["s1", "s2"])
    memory = product(snapshot)
    repo.publish_consolidation(snapshot, [memory], "one")
    memory.consolidation_id = None
    with pytest.raises(ValueError):
        repo.upsert_experience(memory)
    with pytest.raises(ValueError):
        repo.set_experience_source_facts("derived", ["s1"])


def test_publication_rolls_back_the_entire_product_batch(repositories):
    _, create, _ = repositories
    repo = create()
    scope = seed(repo)
    snapshot = repo.consolidation_snapshot(scope, ["s1", "s2"])
    good, bad = product(snapshot, "good"), product(snapshot, "bad")
    bad.workspace_id = "different-workspace"
    with pytest.raises(ValueError, match="scope"):
        repo.publish_consolidation(snapshot, [good, bad], "invalid-batch")
    assert repo.get_experience("good", "u") is None
    assert repo.get_consolidation(snapshot.consolidation_id, "u") is None
    assert repo.stats("u", None, None)["count"] == 0


def test_same_basis_simultaneous_publication_has_one_manifest(repositories):
    _, create, _ = repositories
    first, second = create(), create()
    scope = seed(first)
    barrier = Barrier(2)

    def publish(repo, key):
        snapshot = repo.consolidation_snapshot(scope, ["s1", "s2"])
        barrier.wait(timeout=10)
        return repo.publish_consolidation(snapshot, [product(snapshot, key)], key)

    with ThreadPoolExecutor(2) as pool:
        left, right = pool.submit(publish, first, "left"), pool.submit(publish, second, "right")
        a, b = left.result(timeout=20), right.result(timeout=20)
    assert a.experience_ids == b.experience_ids
    assert first.stats("u", None, None)["count"] == 1


def test_waiting_source_edit_invalidates_the_just_published_receipt(repositories, monkeypatch):
    _, create, _ = repositories
    publisher, editor = create(), create()
    scope = seed(publisher)
    snapshot = publisher.consolidation_snapshot(scope, ["s1", "s2"])
    source_locked, edit_waiting, release = Event(), Event(), Event()
    original_group_lock = publisher._lock_groups
    original_transaction = editor._business_transaction

    def hold_publish(groups):
        source_locked.set()
        assert release.wait(10)
        original_group_lock(groups)

    @contextmanager
    def waiting_edit(**kwargs):
        edit_waiting.set()
        with original_transaction(**kwargs):
            yield

    monkeypatch.setattr(publisher, "_lock_groups", hold_publish)
    monkeypatch.setattr(editor, "_business_transaction", waiting_edit)
    fact = editor.get_facts(["s1"], "u")[0]
    fact.statement = "edit after publish"
    with ThreadPoolExecutor(2) as pool:
        publishing = pool.submit(publisher.publish_consolidation, snapshot, [product(snapshot)], "p")
        assert source_locked.wait(10)
        editing = pool.submit(editor.upsert_fact, fact)
        assert edit_waiting.wait(10)
        release.set()
        publishing.result(timeout=20)
        editing.result(timeout=20)
    assert not editor.get_consolidation(snapshot.consolidation_id, "u").basis_valid


def test_withdrawal_and_historical_reactivation_do_not_resurrect(repositories, monkeypatch):
    _, create, _ = repositories
    publisher, withdrawer = create(), create()
    scope = seed(publisher)
    a = publisher.consolidation_snapshot(scope, ["s1", "s2"])
    publisher.publish_consolidation(a, [product(a)], "a")
    publisher.upsert_fact(WorkingFact("extra", "u", agent_id="a", session_id="s1",
                                     workspace_id="ws", branch="main", statement="extra"))
    b = publisher.consolidation_snapshot(scope, ["s1", "s2"])
    publisher.publish_consolidation(b, [], "b")
    publisher.delete_fact("extra", "u")
    back = publisher.consolidation_snapshot(scope, ["s1", "s2"])
    group_locked, withdraw_waiting, release = Event(), Event(), Event()
    lock = publisher._lock_groups
    withdraw_lock = withdrawer._lock_groups

    def hold_group(groups):
        lock(groups)
        group_locked.set()
        assert release.wait(10)

    def wait_group(groups):
        withdraw_waiting.set()
        withdraw_lock(groups)

    monkeypatch.setattr(publisher, "_lock_groups", hold_group)
    monkeypatch.setattr(withdrawer, "_lock_groups", wait_group)
    with ThreadPoolExecutor(2) as pool:
        activating = pool.submit(publisher.publish_consolidation, back, [], "again", reuse=True)
        assert group_locked.wait(10)
        deleting = pool.submit(withdrawer.delete_experience, "derived", "u")
        # SQLite waits at BEGIN IMMEDIATE, before _lock_groups.
        if repositories[0].business_store.backend == "postgres":
            assert withdraw_waiting.wait(10)
        release.set()
        activating.result(timeout=20)
        deleting.result(timeout=20)
    receipt = publisher.get_consolidation(a.consolidation_id, "u")
    assert receipt.experiences == [] and receipt.withdrawn_ids == ["derived"]
    assert publisher.get_experience("derived", "u") is None


def test_old_schema_upgrade(repositories):
    cfg, create, _ = repositories
    repo = create()
    scope = seed(repo)
    repo._execute("DROP TABLE consolidation_read_set")
    repo._execute("DROP TABLE consolidation_receipt")
    repo._execute("ALTER TABLE coding_memory_meta DROP COLUMN evidence_refs")
    repo._execute("ALTER TABLE coding_memory_meta DROP COLUMN consolidation_id")
    if cfg.business_store.backend == "postgres":
        repo._execute("UPDATE business_schema_version SET version=1")
    repo.close()
    migrated = create()
    snapshot = migrated.consolidation_snapshot(scope, ["s1", "s2"])
    assert migrated.publish_consolidation(snapshot, [], "migrated").decision == "no_result"
