import json
import sqlite3

import pytest

from one_memory.imports.grants import GRANT_SCHEMA
from one_memory.imports.models import EvidenceInput, EvidenceOrigin, OriginMetadata, WrittenBy
from one_memory.imports.storage import EvidenceStore
from one_memory.matters.models import Citation, Claim, MatterDraft, MatterPatch
from one_memory.matters.selection import select_evidence
from one_memory.matters.store import MatterConflict, MatterStore
from one_memory.matters.validation import validate_claim


@pytest.fixture
def library(tmp_path):
    path = tmp_path / "one-memory.sqlite3"
    evidence = EvidenceStore(path)
    with sqlite3.connect(path) as db:
        db.executescript(GRANT_SCHEMA)
        db.execute(
            "INSERT INTO source_grants "
            "(user_id,source_id,root_path,requested_path,root_identity,"
            "candidate_kind,granted_at,revoked_at,revision) "
            "VALUES ('local','cursor','/source','/source','fixture',"
            "'default',?,NULL,1)",
            ("2026-09-11T00:00:00Z",),
        )
    records = []
    for ordinal, author, text in [
        ("1", WrittenBy.PERSON, "We will release Atlas on September 23. Keep the evidence local."),
        (
            "2",
            WrittenBy.AGENT,
            "I implemented the import adapter, but the real host test is still pending.",
        ),
        (
            "3",
            WrittenBy.PERSON,
            "Use a strong model for extraction and synthesis before comparing smaller models.",
        ),
    ]:
        result = evidence.put(
            EvidenceInput(
                source_id="cursor",
                conversation_id="c1",
                workspace="/projects/atlas",
                project="Atlas",
                source_ordinal=ordinal,
                role="user" if author is WrittenBy.PERSON else "assistant",
                written_by=author,
                verbatim=text,
                origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
            )
        )
        records.append(result.record)
    return path, records


def draft(records):
    record = records[0]
    return MatterDraft(
        title="Atlas development",
        purpose="Release Atlas",
        boundary="Atlas project",
        anchors=[record.evidence_id],
        claims=[
            Claim(
                statement="Atlas is planned for September 23.",
                kind="decision",
                scope="Atlas release",
                citations=[
                    Citation(
                        evidence_id=record.evidence_id,
                        quote="We will release Atlas on September 23.",
                    )
                ],
            )
        ],
    )


def test_unknown_message_times_are_not_replaced_by_import_times(library):
    path, _ = library
    sample = select_evidence(path, per_source=2, max_chars=30_000)
    assert sample.records
    assert all(r.created_at is None for r in sample.records)
    assert sample.coverage[0].unknown_time_records == 3
    assert sample.selection_basis == "observation_order_not_message_time"


def test_selection_preserves_full_long_messages_and_numerical_order(library):
    path, _ = library
    with sqlite3.connect(path) as db:
        db.execute("UPDATE evidence SET source_ordinal='10' WHERE role='assistant'")
        db.execute("UPDATE evidence SET verbatim=? WHERE source_ordinal='3'", ("Long " * 2000,))
    sample = select_evidence(path, per_source=1, max_chars=30_000)
    assert len(sample.records[1].text) == 10_000
    assert sample.records[-1].role == "assistant"
    assert sample.truncated_records == 0


def test_selection_excludes_revoked_sources_and_other_users(library):
    path, _ = library
    with sqlite3.connect(path) as db:
        db.execute("UPDATE source_grants SET revoked_at='2026-09-11'")
    assert select_evidence(path).records == []


def test_claim_requires_exact_quote_and_conservative_agent_status(library):
    _, records = library
    sample_records = {r.evidence_id: r for r in records}
    claim = Claim(
        statement="The adapter is done.",
        kind="progress",
        scope="Atlas",
        citations=[Citation(evidence_id=records[1].evidence_id, quote=records[1].verbatim)],
    )
    checked = validate_claim(claim, sample_records)
    assert checked.basis == "agent_report"
    assert checked.verification == "citation_checked"
    wrong = claim.model_copy(
        update={
            "citations": [
                Citation(evidence_id=records[1].evidence_id, quote="It passed every host test.")
            ]
        }
    )
    with pytest.raises(ValueError, match="quote"):
        validate_claim(wrong, sample_records)


def test_agent_only_statement_cannot_become_user_decision(library):
    _, records = library
    claim = Claim(
        statement="User decided to ship.",
        kind="decision",
        scope="Atlas",
        citations=[Citation(evidence_id=records[1].evidence_id, quote=records[1].verbatim)],
    )
    assert validate_claim(claim, {r.evidence_id: r for r in records}).kind == "proposal"


def test_persistence_scope_confirmation_is_not_fact_confirmation(library):
    path, records = library
    store = MatterStore(path)
    saved = store.save(draft(records), model="test-model")
    confirmed = store.patch(saved.matter_id, MatterPatch(expected_version=1, scope_confirmed=True))
    assert confirmed.scope_confirmed
    assert confirmed.claims[0].verification == "citation_checked"
    assert MatterStore(path).get(saved.matter_id).version == 2
    with pytest.raises(MatterConflict):
        store.patch(saved.matter_id, MatterPatch(expected_version=1, title="Stale edit"))


def test_revocation_hides_derived_text_and_context_immediately(library):
    path, records = library
    store = MatterStore(path)
    saved = store.save(draft(records), model="test-model")
    assert "September 23" in store.context(saved.matter_id).text
    with sqlite3.connect(path) as db:
        db.execute("UPDATE source_grants SET revoked_at='2026-09-11'")
    invalid = store.get(saved.matter_id)
    assert invalid.availability == "source_unavailable"
    assert invalid.claims == []
    assert "Atlas" not in invalid.model_dump_json()
    assert "September 23" not in store.context(saved.matter_id).text


def test_evidence_revision_and_deletion_invalidate_old_version(library):
    path, records = library
    store = MatterStore(path)
    saved = store.save(draft(records), model="test-model")
    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE evidence SET revision=revision+1 WHERE evidence_id=?", (records[0].evidence_id,)
        )
    assert store.get(saved.matter_id).availability == "evidence_changed"
    with sqlite3.connect(path) as db:
        db.execute("DELETE FROM evidence WHERE evidence_id=?", (records[0].evidence_id,))
    assert store.get(saved.matter_id).availability == "source_unavailable"


def test_failed_save_is_atomic_and_cannot_invent_sources(library):
    path, records = library
    store = MatterStore(path)
    proposal = draft(records)
    proposal.claims[0].citations[0].evidence_id = "invented"
    with pytest.raises(ValueError):
        store.save(proposal, model="test-model")
    assert store.list() == []


def test_context_preserves_status_and_source_ids(library):
    path, records = library
    proposal = draft(records)
    proposal.claims.append(
        Claim(
            statement="The adapter was implemented; host verification is pending.",
            kind="progress",
            scope="Atlas",
            citations=[Citation(evidence_id=records[1].evidence_id, quote=records[1].verbatim)],
        )
    )
    store = MatterStore(path)
    saved = store.save(proposal, model="test-model")
    context = store.context(saved.matter_id)
    assert "agent_report" in context.text
    assert records[1].evidence_id in context.text
    assert context.instruction_policy == "reference_only"
    assert len(store.history(saved.matter_id)) == 1


def test_correction_is_a_new_human_evidence_not_an_overwrite(library):
    path, records = library
    store = MatterStore(path)
    saved = store.save(draft(records), model="test-model")
    receipt = store.add_note(
        saved.matter_id, "The release is now September 25.", expected_version=1
    )
    assert receipt.source_id == "one-memory-manual"
    assert receipt.written_by == "person"
    assert receipt.evidence_id != records[0].evidence_id
    refreshed = store.get(saved.matter_id)
    assert refreshed.availability == "needs_update"
    assert "September 23" not in store.context(saved.matter_id).text


def test_sources_cannot_be_injected_through_claim_metadata(library):
    path, records = library
    store = MatterStore(path)
    proposal = draft(records)
    proposal.claims[0].citations[0].source_id = "forged-app"
    result = store.save(proposal, model="test-model")
    assert result.claims[0].citations[0].source_id == "cursor"
    assert "forged-app" not in json.dumps(result.model_dump(mode="json"))


def test_new_conversation_record_invalidates_context_without_changing_old_evidence(library):
    path, records = library
    store = MatterStore(path)
    saved = store.save(draft(records), model="test-model")
    EvidenceStore(path).put(
        EvidenceInput(
            source_id="cursor",
            conversation_id="c1",
            source_ordinal="4",
            role="user",
            written_by=WrittenBy.PERSON,
            verbatim="Update: postpone the release until the host test is complete.",
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    )
    assert store.get(saved.matter_id).availability == "needs_update"
    assert "September 23" not in store.context(saved.matter_id).text
    assert any("postpone" in record.text for record in store.refresh_records(saved.matter_id)[1])


def test_legacy_scope_confirmation_never_grants_formal_agent_access(library):
    path, records = library
    store = MatterStore(path)
    saved = store.save(draft(records), model="test-model")
    assert store.search_contexts("Atlas") == []
    store.patch(saved.matter_id, MatterPatch(expected_version=1, scope_confirmed=True))
    assert store.search_contexts("Atlas") == []
    store.patch(saved.matter_id, MatterPatch(expected_version=2, agent_access=True))
    assert store.search_contexts("Atlas") == []
    store.patch(saved.matter_id, MatterPatch(expected_version=3, agent_access=False))
    assert store.search_contexts("Atlas") == []


def test_model_metadata_cannot_grant_agent_access(library):
    path, records = library
    store = MatterStore(path)
    saved = store.save(draft(records), model="test-model")
    assert not saved.agent_access
    store.patch(saved.matter_id, MatterPatch(expected_version=1, agent_access=True))
    updated = store.save(
        draft(records), model="test-model", matter_id=saved.matter_id, expected_version=2
    )
    assert updated.agent_access


def test_configured_origin_is_exact_and_never_wildcard():
    from pydantic import ValidationError

    from one_memory.auth import renderer_origins
    from one_memory.config import Settings

    assert "http://127.0.0.1:5174" in renderer_origins(
        Settings(development_origin="http://127.0.0.1:5174")
    )
    for origin in [
        "*",
        "http://evil.test:5174",
        "http://127.0.0.1:5174/",
        "http://127.0.0.1:5174?x=1",
    ]:
        with pytest.raises(ValidationError):
            Settings(development_origin=origin)


def test_note_and_evidence_roll_back_together_on_write_failure(library, monkeypatch):
    path, records = library
    store = MatterStore(path)
    saved = store.save(draft(records), model="test-model")

    def fail_write(*args):
        raise RuntimeError("simulated write failure")

    monkeypatch.setattr(store, "_write", fail_write)
    with pytest.raises(RuntimeError, match="simulated"):
        store.add_note(saved.matter_id, "Do not retain this failed edit.", expected_version=1)
    with sqlite3.connect(path) as db:
        assert (
            db.execute(
                "SELECT COUNT(*) FROM evidence WHERE source_id='one-memory-manual'"
            ).fetchone()[0]
            == 0
        )
        assert db.execute("SELECT COUNT(*) FROM matter_notes").fetchone()[0] == 0
    assert store.get(saved.matter_id).version == 1


def test_evidence_put_can_participate_in_callers_transaction(library):
    path, _ = library
    evidence = EvidenceStore(path)
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN IMMEDIATE")
        record = EvidenceInput(
            source_id="cursor",
            conversation_id="c2",
            message_id="rollback",
            role="user",
            written_by=WrittenBy.PERSON,
            verbatim="A transaction should be atomic.",
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
        evidence.put(record, connection=db)
        assert (
            db.execute("SELECT COUNT(*) FROM evidence WHERE conversation_id='c2'").fetchone()[0]
            == 1
        )
        db.rollback()
    assert evidence.get(record.evidence_id) is None


def test_matter_api_auth_versions_and_scoped_agent_access(library, tmp_path):
    import asyncio

    from httpx import ASGITransport, AsyncClient
    from pydantic import SecretStr

    from one_memory.api import create_app
    from one_memory.config import Settings

    async def scenario():
        path, records = library
        app = create_app(
            Settings(
                data_dir=path.parent,
                source_home=tmp_path,
                api_token=SecretStr("matter-test"),
                imports_enabled=False,
                automatic_memory_processing=False,
                live_sync_enabled=False,
            )
        )
        async with app.router.lifespan_context(app):
            saved = app.state.matter_store.save(draft(records), model="test-model")
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                assert (await client.get("/api/v1/matters")).status_code == 401
                client.headers["Authorization"] = "Bearer matter-test"
                assert len((await client.get("/api/v1/matters")).json()) == 1
                assert (
                    await client.post("/api/v1/matters/context/search", json={"query": "Atlas"})
                ).json() == []
                enabled = await client.patch(
                    f"/api/v1/matters/{saved.matter_id}",
                    json={"expected_version": 1, "agent_access": True},
                )
                assert enabled.status_code == 200
                contexts = (
                    await client.post("/api/v1/matters/context/search", json={"query": "Atlas"})
                ).json()
                assert contexts == [], "legacy experiments are not formal HY context"
                preview = (await client.get(f"/api/v1/matters/{saved.matter_id}/context")).json()
                assert preview["instruction_policy"] == "reference_only"
                note = await client.post(
                    f"/api/v1/matters/{saved.matter_id}/notes",
                    json={"expected_version": 2, "text": "Changed to September 25."},
                )
                assert note.status_code == 201
                assert note.json()["written_by"] == "person"
                assert (
                    await client.post("/api/v1/matters/context/search", json={"query": "Atlas"})
                ).json() == []
                conflict = await client.patch(
                    f"/api/v1/matters/{saved.matter_id}",
                    json={"expected_version": 2, "title": "Stale title"},
                )
                assert conflict.status_code == 409

    asyncio.run(scenario())


def test_demo_imports_are_disabled_at_backend_even_if_frontend_requests_them(tmp_path):
    import asyncio

    from one_memory.api import create_app
    from one_memory.config import Settings

    async def scenario():
        app = create_app(
            Settings(
                data_dir=tmp_path,
                source_home=tmp_path,
                imports_enabled=False,
                automatic_memory_processing=False,
                live_sync_enabled=False,
            )
        )
        async with app.router.lifespan_context(app):
            assert app.state.source_catalog == {}
            await app.state.memory_backfill
            assert app.state.memory_backfill_error is None

    asyncio.run(scenario())


def test_new_message_during_generation_is_not_silently_acknowledged(library):
    path, records = library
    store = MatterStore(path)
    with store._connect() as db:
        snapshot = [store._source(db, record.evidence_id) for record in records]
    EvidenceStore(path).put(
        EvidenceInput(
            source_id="cursor",
            conversation_id="c1",
            source_ordinal="4",
            role="user",
            written_by=WrittenBy.PERSON,
            verbatim="New decision while the model is still running: do not ship today.",
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    )
    saved = store.save(draft(records), model="test-model", source_records=snapshot)
    assert saved.availability == "needs_update"
    assert "September 23" not in store.context(saved.matter_id).text


def test_repeated_discovery_can_match_existing_dossier_without_granting_access(library):
    path, records = library
    store = MatterStore(path)
    initial = store.save(draft(records), model="test-model")
    matched = store.matching(draft(records))
    assert matched.matter_id == initial.matter_id
    assert matched.agent_access is False


def test_same_frozen_generation_keeps_identity_when_proposed_title_changes(library):
    path, records = library
    store = MatterStore(path)
    first = store.save(draft(records), model="test", generation_key="single:frozen-sample-1")
    revised = draft(records).model_copy(update={"title": "Atlas release and development"})
    second = store.save(revised, model="test", generation_key="single:frozen-sample-1")
    assert second.matter_id == first.matter_id
    assert second.version == 2
    assert len(store.list()) == 1
    assert len(store.history(first.matter_id)) == 2
    assert not second.agent_access


def test_same_generation_does_not_overwrite_user_edits_silently(library):
    path, records = library
    store = MatterStore(path)
    first = store.save(draft(records), model="test", generation_key="single:sample")
    store.patch(
        first.matter_id, MatterPatch(expected_version=1, title="My Atlas", scope_confirmed=True)
    )
    with pytest.raises(MatterConflict, match="version"):
        store.save(draft(records), model="test", generation_key="single:sample", expected_version=1)
    assert store.get(first.matter_id).title == "My Atlas"


def test_short_human_quote_is_valid_but_whitespace_is_not(library):
    _, records = library
    short = Claim(
        statement="Atlas",
        kind="proposal",
        citations=[Citation(evidence_id=records[0].evidence_id, quote="Atlas")],
    )
    assert validate_claim(short, {records[0].evidence_id: records[0]}).citations[0].quote == "Atlas"
    short.citations[0].quote = " "
    with pytest.raises(ValueError, match="quote"):
        validate_claim(short, {records[0].evidence_id: records[0]})


def test_unknown_time_agent_progress_stays_a_historical_report(library):
    _, records = library
    claim = Claim(
        statement="The adapter is done.",
        kind="progress",
        citations=[Citation(evidence_id=records[1].evidence_id, quote=records[1].verbatim)],
    )
    assert validate_claim(claim, {records[1].evidence_id: records[1]}).state == "historical"
