import json
import sqlite3
from contextlib import closing

import pytest

from one_memory import ingest_journal as journal
from one_memory.imports.models import EvidenceInput, EvidenceOrigin, OriginMetadata, WrittenBy
from one_memory.imports.storage import EvidenceStore


@pytest.fixture
def setup(tmp_path):
    path = tmp_path / "test.sqlite3"
    store = EvidenceStore(path)
    with closing(sqlite3.connect(path)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        yield db, store, path


def put(store, db, text="private-body", **changes):
    values = dict(
        source_id="cursor",
        conversation_id="thread",
        message_id="message",
        role="user",
        written_by=WrittenBy.PERSON,
        verbatim=text,
        origin_meta={EvidenceOrigin.FILE: OriginMetadata(raw_meta={"root": "private-root"})},
    )
    values.update(changes)
    return store.put(EvidenceInput(**values), connection=db).record.evidence_id


def test_baseline_and_empty_initialize_are_explicit_transactional_and_idempotent(setup):
    db, store, _ = setup
    with pytest.raises(RuntimeError):
        journal.initialize(db)
    db.execute("BEGIN IMMEDIATE")
    key = put(store, db)
    journal.initialize(db)
    event = journal.current(db, key)
    assert event.kind == "migration_baseline" and not event.generation_eligible
    assert event.seq == event.incarnation_seq == journal.bound(db)
    journal.initialize(db)
    assert journal.current(db, key) == event
    assert db.in_transaction
    db.rollback()
    assert not db.execute(
        "SELECT 1 FROM sqlite_master WHERE name='evidence_ingest_events'"
    ).fetchone()
    db.execute("BEGIN IMMEDIATE")
    journal.initialize(db)
    assert journal.bound(db) == 0
    db.commit()


def test_old_date_new_import_and_revise_noop_and_rollbacks(setup):
    db, store, path = setup
    db.execute("BEGIN IMMEDIATE")
    journal.initialize(db)
    db.commit()
    db.execute("BEGIN IMMEDIATE")
    key = put(store, db, created_at="1990-01-01T00:00:00Z")
    event = journal.record(db, key, cause="import")
    assert event.kind == "create" and event.generation_eligible
    assert journal.record(db, key, cause="import") == event
    with closing(sqlite3.connect(path)) as observer:
        assert journal.bound(observer) == 0
    db.commit()
    db.execute("BEGIN IMMEDIATE")
    put(store, db, "new-body", created_at="1990-01-01T00:00:00Z")
    revised = journal.record(db, key, cause="import")
    assert revised.seq > event.seq and revised.generation_eligible and revised.kind == "revise"
    assert revised.incarnation == event.incarnation
    assert revised.fingerprint != event.fingerprint
    db.rollback()
    db.execute("BEGIN")
    assert journal.current(db, key) == event and journal.matches_current(db, event)
    assert not journal.matches_current(db, revised)


@pytest.mark.parametrize(
    "column,value",
    [
        ("workspace", "new-space"),
        ("project", "new-project"),
        ("source_id", "other-source"),
        ("user_id", "other-user"),
        ("conversation_id", "new-thread"),
        ("source_ordinal", "ordinal:2"),
        ("created_at", "2000-01-01T00:00:00+00:00"),
    ],
)
def test_import_metadata_changes_invalidate_without_authorizing_content(setup, column, value):
    db, store, _ = setup
    db.execute("BEGIN IMMEDIATE")
    key = put(store, db)
    journal.initialize(db)
    previous = journal.current(db, key)
    db.execute(f"UPDATE evidence SET {column}=? WHERE evidence_id=?", (value, key))
    assert not journal.matches_current(db, previous)
    updated = journal.record(db, key, cause="import")
    assert updated.kind == "metadata" and not updated.generation_eligible
    assert updated.fingerprint != previous.fingerprint and updated.seq > previous.seq
    assert updated.incarnation == previous.incarnation
    assert journal.matches_current(db, updated)


@pytest.mark.parametrize("cause", ["import", "maintenance"])
@pytest.mark.parametrize(
    "column,value",
    [("verbatim", "changed raw body"), ("role", "assistant")],
)
def test_material_change_eligibility_depends_on_trusted_cause(setup, cause, column, value):
    db, store, _ = setup
    db.execute("BEGIN IMMEDIATE")
    key = put(store, db)
    journal.initialize(db)
    old = journal.current(db, key)
    db.execute(f"UPDATE evidence SET {column}=? WHERE evidence_id=?", (value, key))
    new = journal.record(db, key, cause=cause)
    assert new.fingerprint != old.fingerprint
    assert new.generation_eligible is (cause == "import")
    assert new.kind == ("revise" if cause == "import" else "metadata")


def test_legacy_written_by_change_is_not_a_material_event(setup):
    db, store, _ = setup
    db.execute("BEGIN IMMEDIATE")
    key = put(store, db)
    journal.initialize(db)
    old = journal.current(db, key)
    db.execute("UPDATE evidence SET written_by='agent' WHERE evidence_id=?", (key,))

    assert journal.record(db, key, cause="import") == old


def test_complete_origin_metadata_changes_and_observation_noise(setup):
    db, store, _ = setup
    db.execute("BEGIN IMMEDIATE")
    key = put(store, db)
    journal.initialize(db)
    old = journal.current(db, key)
    db.execute("UPDATE evidence SET imported_at='2020-01-01',updated_at='2020-01-01'")
    db.execute("UPDATE evidence_origins SET observed_at='2020-01-01'")
    assert journal.record(db, key, cause="maintenance") == old
    db.execute(
        "UPDATE evidence_origins SET raw_meta_json=?",
        (json.dumps({"root": "private-root", "nested": {"all": [1, 2, 3]}}),),
    )
    new = journal.record(db, key, cause="import")
    assert new.origin_hash != old.origin_hash and not new.generation_eligible
    db.execute("UPDATE evidence_origins SET source_version='new'")
    newer = journal.record(db, key, cause="maintenance")
    assert newer.origin_hash != new.origin_hash
    db.execute(
        "INSERT INTO evidence_origins VALUES (?,?,?,?,?)", (key, "hook", "{}", None, "2020-01-01")
    )
    newest = journal.record(db, key, cause="import")
    assert newest.origin_hash != newer.origin_hash and not newest.generation_eligible
    db.execute("DELETE FROM evidence_origins WHERE origin='hook'")
    assert journal.record(db, key, cause="import").fingerprint == newer.fingerprint
    assert journal.current(db, key).seq > newer.seq


def test_delete_reinsert_vacuum_do_not_reuse_identity(setup):
    db, store, _ = setup
    db.execute("BEGIN IMMEDIATE")
    journal.initialize(db)
    key = put(store, db)
    old = journal.record(db, key, cause="import")
    with pytest.raises(ValueError):
        journal.record(db, key, cause="delete")
    db.execute("DELETE FROM evidence_origins WHERE evidence_id=?", (key,))
    db.execute("DELETE FROM evidence WHERE evidence_id=?", (key,))
    deleted = journal.record(db, key, cause="delete")
    assert deleted.kind == "delete" and not deleted.generation_eligible
    assert deleted.incarnation == old.incarnation and deleted.seq > old.seq
    assert journal.record(db, key, cause="delete") == deleted
    db.commit()
    db.execute("VACUUM")
    db.execute("BEGIN IMMEDIATE")
    assert put(store, db) == key
    new = journal.record(db, key, cause="import")
    assert new.seq > deleted.seq and new.incarnation_seq == new.seq
    assert new.incarnation != old.incarnation and not journal.matches_current(db, old)
    assert journal.event(db, old.seq) == old
    db.commit()


def test_savepoint_rollback_and_failed_initialization_preserve_outer_transaction(setup):
    db, store, _ = setup
    db.execute("BEGIN IMMEDIATE")
    key = put(store, db)
    db.execute("UPDATE evidence_origins SET raw_meta_json='not-json'")
    with pytest.raises(ValueError):
        journal.initialize(db)
    assert db.in_transaction
    assert db.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 1
    db.execute("UPDATE evidence_origins SET raw_meta_json='{}'")
    journal.initialize(db)
    before = journal.current(db, key)
    db.execute("SAVEPOINT caller")
    db.execute("UPDATE evidence SET verbatim='different'")
    speculative = journal.record(db, key, cause="import")
    db.execute("ROLLBACK TO caller")
    db.execute("RELEASE caller")
    assert journal.current(db, key) == before
    db.execute("UPDATE evidence SET verbatim='different again'")
    final = journal.record(db, key, cause="import")
    assert final.event_id != speculative.event_id
    assert db.in_transaction


def test_no_plaintext_in_journal_and_canonical_parser_reused(setup, monkeypatch):
    db, store, _ = setup
    db.execute("BEGIN IMMEDIATE")
    key = put(store, db)
    original = EvidenceStore._get_with_connection
    calls = []

    def parse(connection, evidence_id):
        calls.append(evidence_id)
        return original(connection, evidence_id)

    monkeypatch.setattr(EvidenceStore, "_get_with_connection", staticmethod(parse))
    journal.initialize(db)
    assert calls == [key]
    dump = repr([tuple(row) for row in db.execute("SELECT * FROM evidence_ingest_events")])
    assert "private-body" not in dump and "private-root" not in dump
    assert len(journal.current(db, key).fingerprint) == 64
    db.commit()
    with pytest.raises(RuntimeError):
        journal.record(db, key, cause="import")
    db.execute("BEGIN IMMEDIATE")
    with pytest.raises(ValueError):
        journal.record(db, key, cause="untrusted")
    with pytest.raises(KeyError):
        journal.record(db, "unknown", cause="import")


def test_policy_api_user_scope_watermark_and_live_fingerprint(setup):
    db, store, _ = setup
    db.execute("BEGIN IMMEDIATE")
    journal.initialize(db)
    key = put(store, db)
    observed = journal.record(db, key, cause="import")
    assert journal.current(db, "local", key) == observed
    assert journal.current(db, "other", key) is None
    assert journal.watermark(db) == observed.seq
    db.execute("UPDATE evidence SET user_id='other' WHERE evidence_id=?", (key,))
    assert not journal.matches_current(db, observed)
    updated = journal.record(db, key, cause="maintenance")
    assert journal.current(db, "local", key) is None
    assert journal.current(db, "other", key) == updated
    assert not updated.generation_eligible
    assert journal.matches_current(db, updated)


def test_maintenance_create_cannot_later_be_promoted_by_duplicate_import(setup):
    db, store, _ = setup
    db.execute("BEGIN IMMEDIATE")
    journal.initialize(db)
    # Normal put now records an import. Model an actual maintenance insertion here.
    item = EvidenceInput(
        source_id="cursor",
        conversation_id="thread",
        message_id="message",
        role="user",
        written_by=WrittenBy.PERSON,
        verbatim="private-body",
        origin_meta={EvidenceOrigin.FILE: OriginMetadata(raw_meta={"root": "private-root"})},
    )
    key = item.evidence_id
    db.execute(
        """INSERT INTO evidence
        (evidence_id,idempotency_key,user_id,source_id,conversation_id,message_id,
         role,written_by,verbatim,content_hash,revision,imported_at,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,1,'2026-09-19','2026-09-19')""",
        (
            key,
            item.idempotency_key,
            item.user_id,
            item.source_id,
            item.conversation_id,
            item.message_id,
            item.role,
            item.written_by,
            item.verbatim,
            item.content_hash,
        ),
    )
    db.execute(
        "INSERT INTO evidence_origins VALUES (?,?,?,?,?)",
        (key, "file", '{"root":"private-root"}', None, "2026-09-19"),
    )
    created = journal.record(db, key, cause="maintenance")
    assert not created.generation_eligible
    assert put(store, db) == key
    assert journal.record(db, key, cause="import") == created
    db.commit()
    db.execute("BEGIN IMMEDIATE")
    journal.initialize(db)
    assert journal.current(db, "local", key) == created


def test_rebuild_of_empty_evidence_retains_journal_tombstone(setup):
    db, store, _ = setup
    db.execute("BEGIN IMMEDIATE")
    journal.initialize(db)
    key = put(store, db)
    old = journal.record(db, key, cause="import")
    db.execute("DELETE FROM evidence WHERE evidence_id=?", (key,))
    tombstone = journal.record(db, key, cause="delete")
    schema = db.execute("SELECT sql FROM sqlite_master WHERE name='evidence'").fetchone()[0]
    db.execute("DROP TABLE evidence")
    db.execute(schema)
    db.commit()
    db.execute("VACUUM")
    db.execute("BEGIN IMMEDIATE")
    put(store, db)
    new = journal.record(db, key, cause="import")
    assert new.seq > tombstone.seq > old.seq
    assert new.incarnation != old.incarnation
    assert journal.event(db, tombstone.seq) == tombstone


def test_default_row_factory_restored_and_journal_reads_do_not_initialize(setup):
    db, store, _ = setup
    with pytest.raises(sqlite3.OperationalError):
        journal.watermark(db)
    db.execute("BEGIN IMMEDIATE")
    key = put(store, db)
    db.row_factory = None
    journal.initialize(db)
    assert db.row_factory is None
    assert journal.current(db, "local", key).generation_eligible is False
    assert db.row_factory is None
    assert journal.matches_current(db, journal.current(db, "local", key))
    assert db.row_factory is None
