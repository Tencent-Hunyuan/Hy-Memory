"""Read-only mutation receipts from real governance history, without model calls."""

import asyncio
import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from one_memory.imports.grants import SourceGrantStore
from one_memory.imports.models import EvidenceInput, EvidenceOrigin, OriginMetadata
from one_memory.imports.sources.registry import RootCandidateKind
from one_memory.imports.storage import EvidenceStore
from one_memory.library_upgrade import prepare_library
from one_memory.memory_governance import (
    HyRevise,
    MemoryDraft,
    MemoryGovernanceStore,
    MemoryMutationConflictError,
)
from one_memory.memory_mutation_receipts import (
    MemoryMutationReceiptForbidden,
    MemoryMutationReceiptUnavailable,
    read_memory_mutation_receipt,
)
from tests.person_door import as_person


@pytest.fixture
def env(tmp_path):
    database = tmp_path / "fixture.sqlite3"
    evidence = EvidenceStore(database)
    grants = SourceGrantStore(database, home=tmp_path)
    root = tmp_path / "source"
    root.mkdir()
    grants.grant(
        user_id="local", source_id="cursor", root=root, candidate_kind=RootCandidateKind.DEFAULT
    )
    record = evidence.put(
        EvidenceInput(
            source_id="cursor",
            conversation_id="fixture",
            message_id="fixture",
            role="user",
            written_by="person",
            verbatim="PRIVATE ORIGINAL",
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    ).record
    governance = MemoryGovernanceStore(database)
    memory = as_person(governance).create(
        MemoryDraft(
            user_id="local",
            kind="chat",
            content="PRIVATE MEMORY",
            scopes=("all-apps",),
            evidence_ids=(record.evidence_id,),
        ),
        request_id="create-fixture",
    )
    return database, evidence, grants, governance, memory


def dump(database):
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
        return tuple(db.iterdump())


@pytest.mark.parametrize(
    "operation",
    ["create", "correct", "set_scopes", "undo", "delete", "hy_reconcile"],
)
def test_reports_exact_historical_operation_revision_status_without_body(env, operation):
    database, _, _, store, memory = env
    request_id = "create-fixture" if operation == "create" else "mutation-fixture"
    if operation == "undo":
        memory = as_person(store).correct(
            memory.memory_id,
            user_id="local",
            request_id="prior-correct",
            expected_revision=memory.revision,
            content="PRIVATE CHANGED",
        )
    kwargs = {"user_id": "local", "request_id": request_id, "expected_revision": memory.revision}
    if operation in {"correct", "hy_reconcile"}:
        kwargs["content"] = "PRIVATE CHANGED"
    if operation == "set_scopes":
        kwargs["scopes"] = ("private-scope",)
    if operation == "hy_reconcile":
        kwargs["evidence_ids"] = memory.evidence_ids
    if operation == "hy_reconcile":
        memory = store.write_from_hy(
            HyRevise(
                memory_id=memory.memory_id,
                request_id=request_id,
                expected_revision=memory.revision,
                content=kwargs["content"],
                evidence_ids=tuple(kwargs["evidence_ids"]),
            ),
            user_id="local",
            derivation=store.hy_derivation(memory.evidence_ids),
        )
    elif operation != "create":
        memory = getattr(as_person(store), operation)(memory.memory_id, **kwargs)
    before = dump(database)
    receipt = read_memory_mutation_receipt(database, request_id=request_id)
    assert receipt.model_dump() == {
        "schema_version": 2,
        "request_id": request_id,
        "state": "committed",
        "operation": operation,
        "memory_id": memory.memory_id,
        "result_revision": memory.revision,
        "result_status": memory.status.value,
        "memory_state": "deleted" if operation == "delete" else "present",
        "migrated_from": None,
    }
    assert (
        "PRIVATE" not in receipt.model_dump_json()
        and "private-scope" not in receipt.model_dump_json()
    )
    assert dump(database) == before


def test_later_edits_and_delete_do_not_rewrite_historical_receipt(env):
    database, _, _, store, memory = env
    changed = as_person(store).correct(
        memory.memory_id,
        user_id="local",
        request_id="correct",
        expected_revision=1,
        content="PRIVATE CHANGED",
    )
    as_person(store).delete(
        memory.memory_id, user_id="local", request_id="delete", expected_revision=changed.revision
    )
    result = read_memory_mutation_receipt(database, request_id="create-fixture")
    assert result.result_revision == 1 and result.result_status == "confirmed"
    assert result.memory_state == "deleted" and result.state == "committed"


def test_missing_or_refused_request_is_not_found_not_a_fabricated_rejection(env):
    database, _, _, store, memory = env
    with pytest.raises(MemoryMutationConflictError):
        as_person(store).delete(
            memory.memory_id, user_id="local", request_id="refused", expected_revision=99
        )
    for identifier in ("missing", "refused"):
        assert read_memory_mutation_receipt(database, request_id=identifier).model_dump() == {
            "schema_version": 2,
            "request_id": identifier,
            "state": "not_found",
            "operation": None,
            "memory_id": None,
            "result_revision": None,
            "result_status": None,
            "memory_state": None,
            "migrated_from": None,
        }


def test_other_user_request_is_not_disclosed(env):
    database, _, _, _, _ = env
    assert (
        read_memory_mutation_receipt(database, request_id="create-fixture", user_id="other").state
        == "not_found"
    )


def test_same_request_id_is_scoped_to_its_user(env):
    database, evidence, _, store, local = env
    record = evidence.put(
        EvidenceInput(
            user_id="other",
            source_id="cursor",
            conversation_id="other",
            message_id="other",
            role="user",
            written_by="person",
            verbatim="OTHER PRIVATE ORIGINAL",
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    ).record
    other = as_person(store).create(
        MemoryDraft(
            user_id="other",
            kind="chat",
            content="OTHER PRIVATE MEMORY",
            scopes=("all-apps",),
            evidence_ids=(record.evidence_id,),
        ),
        request_id="create-fixture",
    )
    assert (
        read_memory_mutation_receipt(database, request_id="create-fixture").memory_id
        == local.memory_id
    )
    assert (
        read_memory_mutation_receipt(
            database, request_id="create-fixture", user_id="other"
        ).memory_id
        == other.memory_id
    )


def test_missing_grant_is_compatible_but_broken_evidence_ownership_is_not(env):
    database, _, _, _, _ = env
    with sqlite3.connect(database) as db:
        db.execute("DELETE FROM source_grants")
    assert read_memory_mutation_receipt(database, request_id="create-fixture").state == "committed"
    with sqlite3.connect(database) as db:
        db.execute("UPDATE evidence SET user_id='other'")
    with pytest.raises(MemoryMutationReceiptUnavailable):
        read_memory_mutation_receipt(database, request_id="create-fixture")


def test_maximum_legacy_request_id_is_read_without_uuid_restriction(env):
    database, _, _, store, memory = env
    identifier = "x" * 256
    as_person(store).delete(
        memory.memory_id, user_id="local", request_id=identifier, expected_revision=1
    )
    assert read_memory_mutation_receipt(database, request_id=identifier).operation == "delete"


@pytest.mark.parametrize("inactive", [False, True])
def test_revoked_historical_source_forbids_even_deleted_memory_metadata(env, inactive):
    database, _, grants, store, memory = env
    as_person(store).delete(
        memory.memory_id, user_id="local", request_id="delete", expected_revision=1
    )
    grants.revoke(user_id="local", source_id="cursor")
    if inactive:
        with sqlite3.connect(database) as db:
            db.execute("UPDATE governed_memory_evidence SET active=0")
    with pytest.raises(MemoryMutationReceiptForbidden, match="^memory_mutation_receipt_forbidden$"):
        read_memory_mutation_receipt(database, request_id="delete")


@pytest.mark.parametrize(
    "damage",
    [
        "version",
        "memory",
        "evidence",
        "owner",
        "action",
        "revision",
        "status",
        "table",
        "migrated",
        "legacy",
        "created-deleted",
    ],
)
def test_damaged_history_is_unavailable_not_absent(env, damage):
    database, _, _, _, _ = env
    statements = {
        "version": "DELETE FROM governed_memory_versions",
        "memory": "DELETE FROM governed_memories",
        "evidence": "DELETE FROM evidence",
        "owner": "UPDATE governed_memories SET user_id='other'",
        "action": "UPDATE governed_memory_versions SET action='delete'",
        "revision": "UPDATE governed_memory_mutations SET result_revision=0",
        "status": "UPDATE governed_memory_versions SET status='invented'",
        "table": "DROP TABLE governed_memory_mutations",
        # A create never came from an old reject, and `legacy` only ever from a confirm.
        "migrated": "UPDATE governed_memory_versions SET migrated_from='reject'",
        "legacy": "UPDATE governed_memory_versions SET action='legacy'; "
        "UPDATE governed_memory_mutations SET operation='legacy'",
        "created-deleted": "UPDATE governed_memory_versions SET status='deleted'",
    }
    with sqlite3.connect(database) as db:
        db.executescript(statements[damage])
    with pytest.raises(
        MemoryMutationReceiptUnavailable, match="^memory_mutation_receipt_unavailable$"
    ):
        read_memory_mutation_receipt(database, request_id="create-fixture")


def test_missing_database_is_not_created(tmp_path):
    database = tmp_path / "missing.sqlite3"
    with pytest.raises(MemoryMutationReceiptUnavailable):
        read_memory_mutation_receipt(database, request_id="missing")
    assert not database.exists()


@pytest.mark.parametrize("request_id", ["", "x" * 257, None, 7])
def test_invalid_request_identity_is_rejected_before_read(tmp_path, request_id):
    with pytest.raises(ValueError, match="invalid_memory_mutation_request_id"):
        read_memory_mutation_receipt(tmp_path / "absent", request_id=request_id)


def test_revocation_between_read_snapshots_never_returns_old_receipt(env, monkeypatch):
    from one_memory import memory_mutation_receipts as module

    database, _, grants, _, _ = env
    original = module._read_snapshot
    calls = []

    def read(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(True)
        if len(calls) == 1:
            grants.revoke(user_id="local", source_id="cursor")
        return result

    monkeypatch.setattr(module, "_read_snapshot", read)
    with pytest.raises(MemoryMutationReceiptForbidden):
        module.read_memory_mutation_receipt(database, request_id="create-fixture")


@pytest.mark.parametrize("initially_missing", [False, True])
def test_changed_or_new_commit_between_snapshots_is_unavailable(
    env, monkeypatch, initially_missing
):
    from one_memory import memory_mutation_receipts as module

    database, _, _, store, memory = env
    original = module._read_snapshot
    reads = []

    def read(*args, **kwargs):
        value = original(*args, **kwargs)
        reads.append(True)
        if len(reads) == 1:
            as_person(store).delete(
                memory.memory_id, user_id="local", request_id="later", expected_revision=1
            )
        return value

    monkeypatch.setattr(module, "_read_snapshot", read)
    with pytest.raises(MemoryMutationReceiptUnavailable):
        module.read_memory_mutation_receipt(
            database, request_id="later" if initially_missing else "create-fixture"
        )


def test_authenticated_api_create_lookup_and_external_denial(tmp_path):
    from one_memory.api import create_app
    from one_memory.config import Settings
    from one_memory.secrets import InMemorySecretStore

    async def scenario():
        app = create_app(
            Settings(
                _env_file=None,
                data_dir=tmp_path / "data",
                source_home=tmp_path / "home",
                api_token="fixture-token",
                imports_enabled=False,
                live_sync_enabled=False,
                automatic_memory_processing=False,
                retrieval_index_enabled=False,
            ),
            source_catalog={},
            secret_store=InMemorySecretStore(),
        )
        async with (
            app.router.lifespan_context(app),
            AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
        ):
            identifier = str(uuid4())
            url = "/api/v1/memory-mutations/" + identifier
            assert (await client.get(url)).status_code == 401
            client.headers["Authorization"] = "Bearer fixture-token"
            assert (await client.get(url)).json()["state"] == "not_found"
            record = app.state.evidence_store.put(
                EvidenceInput(
                    source_id="fixture",
                    conversation_id="fixture",
                    message_id="fixture",
                    role="user",
                    written_by="person",
                    verbatim="PRIVATE ORIGINAL",
                    origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
                )
            ).record
            response = await client.post(
                "/api/v1/memories",
                json={
                    "request_id": identifier,
                    "draft": {
                        "user_id": "local",
                        "kind": "chat",
                        "content": "PRIVATE MEMORY",
                        "scopes": ["all-apps"],
                        "evidence_ids": [record.evidence_id],
                    },
                },
            )
            assert response.status_code == 200
            memory = response.json()
            receipt = await client.get(url)
            assert receipt.status_code == 200
            assert receipt.json() == {
                "schema_version": 2,
                "request_id": identifier,
                "state": "committed",
                "operation": "create",
                "memory_id": memory["memory_id"],
                "result_revision": 1,
                "result_status": "confirmed",
                "memory_state": "present",
                "migrated_from": None,
            }
            assert "PRIVATE" not in receipt.text
            denied = await client.get(
                url,
                headers={
                    "x-one-memory-read-context": "external",
                    "x-one-memory-installation-id": str(uuid4()),
                    "x-one-memory-installation-revision": "1",
                    "x-one-memory-allowed-scopes": '["all-apps"]',
                    "x-one-memory-allowed-tools": '["om_recall"]',
                },
            )
            assert denied.status_code == 403 and "memory_id" not in denied.text
            invalid = await client.get("/api/v1/memory-mutations/" + "x" * 257)
            assert invalid.status_code == 422
            assert invalid.json() == {"detail": "invalid_memory_mutation_request_id"}
            root = tmp_path / "fixture-source"
            root.mkdir()
            app.state.source_grant_store.grant(
                user_id="local",
                source_id="fixture",
                root=root,
                candidate_kind=RootCandidateKind.DEFAULT,
            )
            app.state.source_grant_store.revoke(user_id="local", source_id="fixture")
            revoked = await client.get(url)
            assert revoked.status_code == 403
            assert revoked.json() == {"detail": "memory_mutation_receipt_forbidden"}
            assert memory["memory_id"] not in revoked.text
            app.state.source_grant_store.grant(
                user_id="local",
                source_id="fixture",
                root=root,
                candidate_kind=RootCandidateKind.DEFAULT,
            )
            with sqlite3.connect(app.state.evidence_store.database_path) as db:
                db.execute("DELETE FROM governed_memory_versions WHERE revision=1")
            broken = await client.get(url)
            assert broken.status_code == 503
            assert broken.json() == {"detail": "memory_mutation_receipt_unavailable"}

    asyncio.run(scenario())


def test_v1_receipts_stored_before_the_upgrade_read_as_v2_after_migration(env):
    """旧库的回执由当时的台账现算：新建=candidate、confirm、reject 都要能继续读。"""
    from tests.test_memory_no_candidate import LEGACY_HISTORIES, _legacy_memory

    database, _, _, _, memory = env
    for name in ("candidate", "corrected-candidate", "rejected", "confirmed"):
        _legacy_memory(
            database,
            memory_id=f"legacy-{name}",
            history=LEGACY_HISTORIES[name],
            evidence_id=memory.evidence_ids[0],
            content=f"PRIVATE {name}",
        )

    MemoryGovernanceStore(database, prepared=prepare_library(database))

    def read(request_id):
        receipt = read_memory_mutation_receipt(database, request_id=request_id)
        assert (receipt.schema_version, receipt.state) == (2, "committed")
        return (
            receipt.operation,
            receipt.memory_id,
            receipt.result_revision,
            receipt.result_status,
            receipt.memory_state,
            receipt.migrated_from,
        )

    assert read("create:legacy-candidate") == (
        "create",
        "legacy-candidate",
        1,
        "confirmed",
        "present",
        None,
    )
    assert read("correct:legacy-corrected-candidate") == (
        "correct",
        "legacy-corrected-candidate",
        2,
        "confirmed",
        "present",
        None,
    )
    assert read("create:legacy-rejected") == (
        "create",
        "legacy-rejected",
        1,
        "confirmed",
        "deleted",
        None,
    )
    assert read("reject:legacy-rejected") == (
        "delete",
        "legacy-rejected",
        2,
        "deleted",
        "deleted",
        "reject",
    )
    assert read("confirm:legacy-confirmed") == (
        "legacy",
        "legacy-confirmed",
        2,
        "confirmed",
        "present",
        "confirm",
    )


@pytest.mark.parametrize(
    "damage",
    [
        "UPDATE governed_memory_versions SET migrated_from=NULL WHERE action='legacy'",
        "UPDATE governed_memory_versions SET migrated_from='reject' WHERE action='legacy'",
        "UPDATE governed_memory_versions SET migrated_from='confirm' WHERE action='delete'",
    ],
)
def test_a_migrated_receipt_whose_origin_does_not_fit_is_unavailable(env, damage):
    from tests.test_memory_no_candidate import LEGACY_HISTORIES, _legacy_memory

    database, _, _, _, memory = env
    for name in ("rejected", "confirmed"):
        _legacy_memory(
            database,
            memory_id=f"legacy-{name}",
            history=LEGACY_HISTORIES[name],
            evidence_id=memory.evidence_ids[0],
            content=f"PRIVATE {name}",
        )
    MemoryGovernanceStore(database, prepared=prepare_library(database))
    request_id = "reject:legacy-rejected" if "'delete'" in damage else "confirm:legacy-confirmed"
    assert read_memory_mutation_receipt(database, request_id=request_id).state == "committed"
    with sqlite3.connect(database) as db:
        db.execute(damage)
    with pytest.raises(MemoryMutationReceiptUnavailable):
        read_memory_mutation_receipt(database, request_id=request_id)


FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "packages"
    / "contracts"
    / "fixtures"
    / "memory-mutation-receipts.v2.json"
)
#: Core picks the id of a created memory; the fixture pins one so the CLI test
#: can bind a plan to it. Nothing else in a receipt is rewritten.
FIXTURE_MEMORY_ID = "0b6f7c1e-2d4a-4e8b-9c3d-5a1f0e2b7d64"
FIXTURE_REQUESTS = {
    "add": "7d1c9a52-0b3e-4f6a-8c2d-1e5b9f4a3c70",
    "correct": "2f8e4b61-9c0d-4a7e-b3f5-6d2a1c8e9b04",
    "delete": "91b4d2f6-7e3a-4d8c-a5b1-0f6e2c9d4a37",
}


def test_the_cli_receipt_fixture_is_what_core_actually_returns(tmp_path):
    """CLI 的契约测试读这份样例；样例一旦和 Core 的真实回执不一致，这里先红。"""
    import json

    from one_memory.api import create_app
    from one_memory.config import Settings
    from one_memory.secrets import InMemorySecretStore

    async def scenario():
        app = create_app(
            Settings(
                _env_file=None,
                data_dir=tmp_path / "data",
                source_home=tmp_path / "home",
                api_token="fixture-token",
                imports_enabled=False,
                live_sync_enabled=False,
                automatic_memory_processing=False,
                retrieval_index_enabled=False,
            ),
            source_catalog={},
            secret_store=InMemorySecretStore(),
        )
        receipts = {}
        async with (
            app.router.lifespan_context(app),
            AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer fixture-token"},
            ) as client,
        ):
            record = app.state.evidence_store.put(
                EvidenceInput(
                    source_id="fixture",
                    conversation_id="fixture",
                    message_id="fixture",
                    role="user",
                    written_by="person",
                    verbatim="PRIVATE ORIGINAL",
                    origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
                )
            ).record
            created = await client.post(
                "/api/v1/memories",
                json={
                    "request_id": FIXTURE_REQUESTS["add"],
                    "draft": {
                        "user_id": "local",
                        "kind": "chat",
                        "content": "PRIVATE MEMORY",
                        "scopes": ["all-apps"],
                        "evidence_ids": [record.evidence_id],
                        "origin": "manual",
                    },
                },
            )
            assert created.status_code == 200
            memory_id = created.json()["memory_id"]
            steps = {
                "add": None,
                "correct": (
                    f"/api/v1/memories/{memory_id}/correct",
                    {"expected_revision": 1, "content": "PRIVATE CHANGED"},
                ),
                "delete": (
                    f"/api/v1/memories/{memory_id}/actions/delete",
                    {"expected_revision": 2},
                ),
            }
            for name, step in steps.items():
                if step is not None:
                    path, body = step
                    done = await client.post(
                        path, json={"request_id": FIXTURE_REQUESTS[name], **body}
                    )
                    assert done.status_code == 200, (name, done.text)
                # Read at once: memory_state is the memory's state now, so a
                # later step would change an earlier receipt.
                response = await client.get("/api/v1/memory-mutations/" + FIXTURE_REQUESTS[name])
                assert response.status_code == 200, (name, response.text)
                receipts[name] = {**response.json(), "memory_id": FIXTURE_MEMORY_ID}
        return receipts

    receipts = asyncio.run(scenario())
    assert json.loads(FIXTURE.read_text(encoding="utf-8"))["receipts"] == receipts
