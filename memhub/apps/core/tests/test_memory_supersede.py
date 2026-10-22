"""HY 取代一条记忆时，产品里的旧记忆变成「已被取代」并指向新记忆。

旧记忆不删除、不再当作当前记忆：召回、默认列表和宿主上下文都只给链头，
按编号仍能读到旧记忆和它的替换链。
"""

import asyncio
import sqlite3
import time
from contextlib import closing

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from one_memory.api import create_app
from one_memory.config import Settings
from one_memory.context_memory import read_memory_context
from one_memory.context_models import ContextSearchRequest
from one_memory.imports.grants import SourceGrantStore
from one_memory.imports.models import EvidenceInput, EvidenceOrigin, OriginMetadata, WrittenBy
from one_memory.imports.storage import EvidenceStore
from one_memory.memory_governance import (
    HyRevise,
    HySupersede,
    MemoryDraft,
    MemoryGovernanceStore,
    MemoryKind,
    MemoryMutationConflictError,
    MemoryMutationRequestConflictError,
    MemoryStatus,
)
from one_memory.memory_library import read_memory_library
from one_memory.memory_mutation_receipts import read_memory_mutation_receipt
from one_memory.recall import GovernedRecallTarget, RecallRequest, RecallState
from tests.person_door import as_person

OLD = "用户的发布前检查要求一次权限评审"
NEW = "用户的发布前检查现在要求两次权限评审"


def _evidence(database, message_id="m1", verbatim="发布前要做权限评审。"):
    return (
        EvidenceStore(database)
        .put(
            EvidenceInput(
                source_id="cursor",
                conversation_id="conversation",
                message_id=message_id,
                correlation_id=message_id,
                role="user",
                written_by=WrittenBy.PERSON,
                verbatim=verbatim,
                origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
            )
        )
        .record.evidence_id
    )


def _confirmed(store, evidence_id, content, key):
    created = as_person(store).create(
        MemoryDraft(
            user_id="local",
            kind=MemoryKind.CHAT,
            content=content,
            scopes=("all-apps",),
            evidence_ids=(evidence_id,),
        ),
        request_id=f"create-{key}",
        memory_id=key,
    )
    return created


def _pair(tmp_path):
    database = tmp_path / "one-memory.sqlite3"
    SourceGrantStore(database, home=tmp_path)
    store = MemoryGovernanceStore(database)
    evidence_id = _evidence(database)
    old = _confirmed(store, evidence_id, OLD, "old")
    new = _confirmed(store, evidence_id, NEW, "new")
    return database, store, old, new


def _supersede(store, old, *by, request_id=None):
    return store.write_from_hy(
        HySupersede(
            memory_id=old.memory_id,
            request_id=request_id or f"hy-supersede:task:{old.memory_id}",
            expected_revision=old.revision,
            superseded_by=by,
        ),
        user_id="local",
        derivation=store.hy_derivation(),
    )


def test_superseding_keeps_the_old_memory_and_links_both_ways(tmp_path) -> None:
    _, store, old, new = _pair(tmp_path)
    assert old.superseded_by == () and new.supersedes == ()

    replaced = _supersede(store, old, new.memory_id)

    assert replaced.status is MemoryStatus.SUPERSEDED
    assert replaced.deleted_at is None
    assert replaced.content == OLD
    assert replaced.superseded_by == (new.memory_id,)
    assert replaced.revision == old.revision + 1
    head = store.get(new.memory_id, user_id="local")
    assert head.status is MemoryStatus.CONFIRMED
    assert head.supersedes == (old.memory_id,)
    assert head.superseded_by == ()
    assert store.get(old.memory_id, user_id="local") == replaced
    assert store.history(old.memory_id, user_id="local")[0].action == "hy_supersede"
    assert store.deletion(old.memory_id, user_id="local") is None


def test_a_chain_fold_links_every_generation(tmp_path) -> None:
    database, store, first, second = _pair(tmp_path)
    head = _confirmed(store, _evidence(database, "m2", "现在是三次。"), "三次评审", "head")

    _supersede(store, first, second.memory_id)
    _supersede(store, store.get(second.memory_id, user_id="local"), head.memory_id)

    first, second, head = (store.get(key, user_id="local") for key in ("old", "new", "head"))
    assert (first.status, second.status, head.status) == (
        MemoryStatus.SUPERSEDED,
        MemoryStatus.SUPERSEDED,
        MemoryStatus.CONFIRMED,
    )
    assert first.superseded_by == ("new",) and first.supersedes == ()
    assert second.supersedes == ("old",) and second.superseded_by == ("head",)
    assert head.supersedes == ("new",)


def test_superseding_again_only_adds_the_link(tmp_path) -> None:
    database, store, old, new = _pair(tmp_path)
    other = _confirmed(store, _evidence(database, "m3", "另一条"), "另一条替换", "other")
    once = _supersede(store, old, new.memory_id)

    again = _supersede(store, once, other.memory_id, request_id="hy-supersede:task-2:old")

    assert again.revision == once.revision
    assert again.superseded_by == tuple(sorted((new.memory_id, other.memory_id)))


def test_d16_supersede_contract_distinguishes_request_revision_and_replacement_errors(
    tmp_path,
) -> None:
    """The D-16 remediation caller gets an actionable, non-ambiguous refusal.

    A replay is idempotent; a reused request id with a different memory is a
    programming/correlation error; a stale revision is a retryable lifecycle
    race; a missing replacement is a dependency gap. The processing boundary
    must be able to tell these apart without parsing status text.
    """
    database, store, old, new = _pair(tmp_path)
    other = _confirmed(store, _evidence(database, "m3", "另一条"), "另一条替换", "other")
    request_id = "d16-retract:old:new"
    change = HySupersede(
        memory_id=old.memory_id,
        request_id=request_id,
        expected_revision=old.revision,
        superseded_by=(new.memory_id,),
    )

    first = store.write_from_hy(
        change,
        user_id="local",
        derivation=store.hy_derivation().citing(old.evidence_ids),
    )
    replay = store.write_from_hy(
        change,
        user_id="local",
        derivation=store.hy_derivation().citing(old.evidence_ids),
    )
    assert replay == first

    with pytest.raises(MemoryMutationRequestConflictError):
        store.write_from_hy(
            HySupersede(
                memory_id=other.memory_id,
                request_id=request_id,
                expected_revision=other.revision,
                superseded_by=(new.memory_id,),
            ),
            user_id="local",
            derivation=store.hy_derivation().citing(other.evidence_ids),
        )

    with pytest.raises(MemoryMutationConflictError, match="^memory_revision_conflict$"):
        store.write_from_hy(
            HySupersede(
                memory_id=other.memory_id,
                request_id="d16-retract:other:stale",
                expected_revision=other.revision + 1,
                superseded_by=(new.memory_id,),
            ),
            user_id="local",
            derivation=store.hy_derivation().citing(other.evidence_ids),
        )

    with pytest.raises(MemoryMutationConflictError, match="^memory_replacement_not_found$"):
        store.write_from_hy(
            HySupersede(
                memory_id=other.memory_id,
                request_id="d16-retract:other:missing",
                expected_revision=other.revision,
                superseded_by=("missing-head",),
            ),
            user_id="local",
            derivation=store.hy_derivation().citing(other.evidence_ids),
        )


def test_d16_supersede_result_survives_restart_and_replay(tmp_path) -> None:
    """A retraction that disappears after reopen is not a governance fact."""
    database, store, old, new = _pair(tmp_path)
    request_id = "d16-retract:restart"
    change = HySupersede(
        memory_id=old.memory_id,
        request_id=request_id,
        expected_revision=old.revision,
        superseded_by=(new.memory_id,),
    )

    first = store.write_from_hy(
        change,
        user_id="local",
        derivation=store.hy_derivation().citing(old.evidence_ids),
    )

    reopened = MemoryGovernanceStore(database)
    after_restart = reopened.get(old.memory_id, user_id="local")
    assert after_restart is not None
    assert after_restart.status is MemoryStatus.SUPERSEDED
    assert after_restart.revision == first.revision
    assert after_restart.superseded_by == (new.memory_id,)
    assert old.memory_id not in {
        memory.memory_id for memory in reopened.list(user_id="local", status=MemoryStatus.CONFIRMED)
    }
    replayed = reopened.write_from_hy(
        change,
        user_id="local",
        derivation=reopened.hy_derivation().citing(old.evidence_ids),
    )
    assert replayed == after_restart


def test_a_superseded_memory_leaves_every_current_read_path(tmp_path) -> None:
    database, store, old, new = _pair(tmp_path)

    # Positive first: while it is current, every path returns it.
    def current_ids():
        recall = GovernedRecallTarget(store).recall(RecallRequest(query="权限评审"))
        semantic = asyncio.run(
            GovernedRecallTarget(
                store, semantic_search=lambda _: _async((old.memory_id,))
            ).recall_async(RecallRequest(query="发布之前要做什么？"))
        )
        context = read_memory_context(
            database,
            ContextSearchRequest(query="权限评审", kinds=("memory",)),
            scopes=frozenset({"all-apps"}),
            deadline=time.monotonic() + 3,
        )
        return {
            "listed": {m.memory_id for m in store.list(user_id="local")},
            "library": {m.memory_id for m in read_memory_library(database).items},
            "recall": {hit.memory_id for hit in recall.hits},
            "semantic": {hit.memory_id for hit in semantic.hits},
            "matching": {
                m.memory_id
                for m in store.find_confirmed_matching_terms(user_id="local", terms=("权限评审",))[
                    0
                ]
            },
            "context": {item.memory_id for item in context.items},
        }

    before = current_ids()
    assert all(old.memory_id in ids for ids in before.values()), before

    _supersede(store, old, new.memory_id)

    after = current_ids()
    assert all(old.memory_id not in ids for ids in after.values()), after
    assert all(new.memory_id in after[path] for path in ("listed", "library", "recall")), after
    assert GovernedRecallTarget(store).recall(RecallRequest(query="权限评审")).state is (
        RecallState.GROUNDED
    )
    # Still there by id and when asked for by status.
    assert store.get(old.memory_id, user_id="local").status is MemoryStatus.SUPERSEDED
    assert [m.memory_id for m in store.list(user_id="local", status=MemoryStatus.SUPERSEDED)] == [
        old.memory_id
    ]
    library = read_memory_library(database, status=MemoryStatus.SUPERSEDED)
    assert [m.memory_id for m in library.items] == [old.memory_id]
    assert library.items[0].superseded_by == (new.memory_id,)


async def _async(value):
    return value


def _act(store, operation, memory_id, request_id, revision, arguments):
    if operation != "hy_reconcile":
        return getattr(as_person(store), operation)(
            memory_id,
            user_id="local",
            request_id=request_id,
            expected_revision=revision,
            **arguments,
        )
    return store.write_from_hy(
        HyRevise(
            memory_id=memory_id,
            request_id=request_id,
            expected_revision=revision,
            content=arguments["content"],
            evidence_ids=tuple(arguments["evidence_ids"]),
        ),
        user_id="local",
        derivation=store.hy_derivation().citing(arguments["evidence_ids"]),
    )


@pytest.mark.parametrize(
    "operation",
    ["undo", "correct", "set_scopes", "hy_reconcile"],
)
def test_product_actions_on_a_superseded_memory_are_refused(tmp_path, operation) -> None:
    _, store, old, new = _pair(tmp_path)
    arguments = {
        "undo": {},
        "correct": {"content": "改一下"},
        "set_scopes": {"scopes": ("cursor",)},
        "hy_reconcile": {"content": "改一下", "evidence_ids": old.evidence_ids},
    }[operation]
    # Positive first: the same call on the current memory is not refused as superseded.
    try:
        _act(store, operation, new.memory_id, f"{operation}-current", new.revision, arguments)
    except MemoryMutationConflictError as error:
        assert str(error) != "memory_superseded"
    replaced = _supersede(store, old, new.memory_id)

    with pytest.raises(MemoryMutationConflictError, match="^memory_superseded$"):
        _act(
            store,
            operation,
            old.memory_id,
            f"{operation}-after-supersede",
            replaced.revision,
            arguments,
        )
    assert store.get(old.memory_id, user_id="local") == replaced


def test_deleting_a_superseded_memory_still_works_and_queues_hy_cleanup(tmp_path) -> None:
    _, store, old, new = _pair(tmp_path)
    replaced = _supersede(store, old, new.memory_id)

    deleted = as_person(store).delete(
        old.memory_id,
        user_id="local",
        request_id="delete-old",
        expected_revision=replaced.revision,
    )

    assert deleted.status is MemoryStatus.DELETED
    assert store.deletion(old.memory_id, user_id="local").status == "pending"
    assert store.get(new.memory_id, user_id="local").status is MemoryStatus.CONFIRMED


#: The columns governed_memories had before supersession existed. Compared
#: against a fixed list because a column added on the store's first open is
#: already there by the time any before/after snapshot in a test is taken.
_COLUMNS_BEFORE_SUPERSESSION = [
    "memory_id",
    "user_id",
    "kind",
    "status",
    "content",
    "scopes_json",
    "origin",
    "tags_json",
    "revision",
    "created_at",
    "updated_at",
    "deleted_at",
    "undo_cursor_revision",
]


def test_the_link_is_stored_additively_without_rewriting_existing_rows(tmp_path) -> None:
    database, store, old, new = _pair(tmp_path)
    # A library written before this change: the link table does not exist yet.
    with closing(sqlite3.connect(database)) as db, db:
        db.execute("DROP TABLE governed_memory_supersessions")
    with closing(sqlite3.connect(database)) as db:
        before = db.execute("SELECT * FROM governed_memories ORDER BY memory_id").fetchall()
        columns = [row[1] for row in db.execute("PRAGMA table_info(governed_memories)")]
    assert columns == _COLUMNS_BEFORE_SUPERSESSION

    MemoryGovernanceStore(database)

    with closing(sqlite3.connect(database)) as db:
        assert db.execute("SELECT * FROM governed_memories ORDER BY memory_id").fetchall() == before
        assert [row[1] for row in db.execute("PRAGMA table_info(governed_memories)")] == columns
        assert db.execute(
            "SELECT name FROM sqlite_master WHERE name='governed_memory_supersessions'"
        ).fetchone()


def test_an_earlier_receipt_stays_readable_after_its_memory_is_superseded(tmp_path) -> None:
    database, store, old, new = _pair(tmp_path)
    old = as_person(store).set_scopes(
        old.memory_id,
        user_id="local",
        request_id="scope-old",
        expected_revision=old.revision,
        scopes=("all-apps", "cursor"),
    )
    before = read_memory_mutation_receipt(database, request_id="scope-old")
    assert before.state == "committed" and before.result_status == "confirmed"

    _supersede(store, old, new.memory_id)

    after = read_memory_mutation_receipt(database, request_id="scope-old")
    assert after == before
    assert set(type(after).model_fields) == {
        "schema_version",
        "request_id",
        "state",
        "operation",
        "memory_id",
        "result_revision",
        "result_status",
        "memory_state",
        "migrated_from",
    }


def test_the_api_shows_the_chain_refuses_edits_and_still_deletes_through_hy(tmp_path) -> None:
    async def scenario() -> None:
        app = create_app(
            Settings(
                data_dir=tmp_path / "data",
                source_home=tmp_path,
                api_token=SecretStr("local-token"),
            )
        )
        async with (
            app.router.lifespan_context(app),
            AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={
                    "Authorization": "Bearer local-token",
                    "Origin": "http://127.0.0.1:5173",
                },
            ) as client,
        ):
            store = app.state.memory_governance_store
            evidence_id = _evidence(app.state.evidence_store.database_path)
            door = app.state.person_writes
            old = _confirmed(door, evidence_id, OLD, "old")
            new = _confirmed(door, evidence_id, NEW, "new")
            listed = await client.get("/api/v1/memories")
            assert {m["memory_id"] for m in listed.json()} == {"old", "new"}
            replaced = _supersede(store, old, new.memory_id)

            listed = await client.get("/api/v1/memories")
            assert [m["memory_id"] for m in listed.json()] == ["new"]
            library = await client.get("/api/v1/memory-library")
            assert [m["memory_id"] for m in library.json()["items"]] == ["new"]
            only_old = await client.get("/api/v1/memory-library", params={"status": "superseded"})
            assert [m["memory_id"] for m in only_old.json()["items"]] == ["old"]
            detail = (await client.get("/api/v1/memories/old")).json()["memory"]
            assert detail["status"] == "superseded"
            assert detail["superseded_by"] == ["new"]
            assert detail["supersedes"] == []
            head = (await client.get("/api/v1/memories/new")).json()["memory"]
            assert head["supersedes"] == ["old"] and head["superseded_by"] == []
            recalled = await client.post(
                "/api/v1/recall", json={"query": "权限评审", "scope": "all-apps"}
            )
            assert [hit["memory_id"] for hit in recalled.json()["hits"]] == ["new"]

            for action in ("undo",):
                refused = await client.post(
                    f"/api/v1/memories/old/actions/{action}",
                    json={"request_id": f"{action}-x", "expected_revision": replaced.revision},
                )
                assert (refused.status_code, refused.json()["detail"]) == (
                    409,
                    "memory_superseded",
                )
            refused = await client.post(
                "/api/v1/memories/old/correct",
                json={
                    "request_id": "correct-x",
                    "expected_revision": replaced.revision,
                    "content": "改一下",
                },
            )
            assert (refused.status_code, refused.json()["detail"]) == (409, "memory_superseded")

            cascaded: list[str] = []

            async def finish_deletion(memory_id: str) -> None:
                cascaded.append(memory_id)

            app.state.sdk_memory_runtime.finish_deletion = finish_deletion
            deleted = await client.post(
                "/api/v1/memories/old/actions/delete",
                json={"request_id": "delete-x", "expected_revision": replaced.revision},
            )
            assert deleted.status_code == 200 and deleted.json()["status"] == "deleted"
            assert cascaded == ["old"]
            cleanup = await client.get("/api/v1/memories/old/deletion")
            assert cleanup.json()["status"] == "completed"

            spec = app.openapi()["components"]["schemas"]
            assert "superseded" in spec["MemoryStatus"]["enum"]
            assert {"supersedes", "superseded_by"} <= set(spec["GovernedMemory"]["properties"])

    asyncio.run(scenario())


def test_supersession_integrity_reports_a_cycle_and_stays_quiet_when_healthy(tmp_path) -> None:
    """The read-only audit must flag a real cycle and not cry wolf otherwise.

    A well-formed replacement chain is a DAG whose component has one current
    head. `supersession_integrity()` reports strongly connected components
    (real cycles) separately from acyclic headless components, so an operator
    or the reconciliation path can see the red before any repair. It must not
    flag a healthy chain, and it must not treat a person's deletion as
    corruption.
    """
    database = tmp_path / "one-memory.sqlite3"
    store = MemoryGovernanceStore(database)
    door = as_person(store)

    def create(content: str, key: str):
        evidence_id = (
            EvidenceStore(database)
            .put(
                EvidenceInput(
                    source_id="cursor",
                    conversation_id="integrity",
                    message_id=key,
                    role="user",
                    written_by=WrittenBy.PERSON,
                    verbatim="原文",
                    origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
                )
            )
            .record.evidence_id
        )
        return door.create(
            MemoryDraft(
                user_id="local",
                kind=MemoryKind.CHAT,
                content=content,
                scopes=("all-apps",),
                evidence_ids=(evidence_id,),
            ),
            request_id=key,
        )

    def supersede(old, new, request_id: str) -> None:
        store.write_from_hy(
            HySupersede(
                memory_id=old.memory_id,
                request_id=request_id,
                expected_revision=old.revision,
                superseded_by=(new.memory_id,),
            ),
            user_id="local",
            derivation=store.hy_derivation(),
        )

    # A healthy chain A <- B <- C is clean.
    a, b, c = create("A", "a"), create("B", "b"), create("C", "c")
    supersede(a, b, "chain-ab")
    supersede(b, c, "chain-bc")
    assert store.supersession_integrity(user_id="local").ok

    # A mutual supersede is a real cycle. Add a *confirmed* outgoing head Z so
    # the component is cyclic AND has a current member: a "no confirmed head"
    # heuristic would miss this, a strongly-connected-component check finds it.
    x, y, z = create("X", "x"), create("Y", "y"), create("Z", "z")
    supersede(x, y, "cycle-xy")
    supersede(store.get(y.memory_id, user_id="local"), x, "cycle-yx")
    supersede(store.get(x.memory_id, user_id="local"), z, "cycle-xz")
    report = store.supersession_integrity(user_id="local")
    assert report.cycles == (tuple(sorted((x.memory_id, y.memory_id))),)
    assert report.headless == ()
    assert report.ok is False


def test_supersession_integrity_ignores_deleting_a_long_chains_head(tmp_path) -> None:
    """Deleting the current head is a person's action, not corruption.

    `test_supersession_integrity_ignores_a_user_deletion` pins the two-link
    case, but the audit used a weak-component scan that only stayed quiet there
    by accident: with three or more links, deleting the head left a component
    with no `confirmed` member and the report claimed `headless`, even though
    the person simply removed the newest memory. Every remaining node in that
    component is already `superseded`, which is the normal shape after such a
    deletion, so the audit must stay quiet at any chain length.
    """
    database = tmp_path / "one-memory.sqlite3"
    store = MemoryGovernanceStore(database)
    door = as_person(store)

    def create(content: str, key: str):
        evidence_id = (
            EvidenceStore(database)
            .put(
                EvidenceInput(
                    source_id="cursor",
                    conversation_id="integrity",
                    message_id=key,
                    role="user",
                    written_by=WrittenBy.PERSON,
                    verbatim="原文",
                    origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
                )
            )
            .record.evidence_id
        )
        return door.create(
            MemoryDraft(
                user_id="local",
                kind=MemoryKind.CHAT,
                content=content,
                scopes=("all-apps",),
                evidence_ids=(evidence_id,),
            ),
            request_id=key,
        )

    chain = [create(f"generation {index}", f"gen-{index}") for index in range(4)]
    for index in range(3):
        store.write_from_hy(
            HySupersede(
                memory_id=chain[index].memory_id,
                request_id=f"link-{index}",
                expected_revision=chain[index].revision,
                superseded_by=(chain[index + 1].memory_id,),
            ),
            user_id="local",
            derivation=store.hy_derivation(),
        )
    head = store.get(chain[-1].memory_id, user_id="local")
    assert head is not None
    door.delete(
        head.memory_id, user_id="local", request_id="delete-head", expected_revision=head.revision
    )
    report = store.supersession_integrity(user_id="local")
    assert report.cycles == ()
    assert report.headless == (), "deleting the chain head is a person's action, not corruption"
    assert report.ok is True


def test_supersession_integrity_still_flags_a_genuinely_headless_chain(tmp_path) -> None:
    """Positive control for the deletion fix: a genuine headless chain is still red."""
    database = tmp_path / "one-memory.sqlite3"
    store = MemoryGovernanceStore(database)
    with store._connection() as connection:
        for memory_id in ("h0", "h1", "h2"):
            connection.execute(
                "INSERT INTO governed_memories "
                "(memory_id,user_id,kind,status,content,scopes_json,revision,"
                "created_at,updated_at) VALUES (?,?,?,?,?,?,1,?,?)",
                (memory_id, "local", "chat", "superseded", "x", '["all-apps"]', "now", "now"),
            )
        # An acyclic chain whose members are all superseded and none deleted:
        # there is genuinely no current head, so the audit must still flag it.
        connection.execute(
            "INSERT INTO governed_memory_supersessions (memory_id,superseded_by,created_at) "
            "VALUES ('h0','h1','now')"
        )
        connection.execute(
            "INSERT INTO governed_memory_supersessions (memory_id,superseded_by,created_at) "
            "VALUES ('h1','h2','now')"
        )
    report = store.supersession_integrity(user_id="local")
    assert report.cycles == ()
    assert report.headless == (("h0", "h1", "h2"),)
    assert report.ok is False


def test_supersession_integrity_ignores_a_user_deletion(tmp_path) -> None:
    """Deleting the new memory is not corruption: no row is reported."""
    database = tmp_path / "one-memory.sqlite3"
    store = MemoryGovernanceStore(database)
    door = as_person(store)

    def create(content: str, key: str):
        evidence_id = (
            EvidenceStore(database)
            .put(
                EvidenceInput(
                    source_id="cursor",
                    conversation_id="integrity",
                    message_id=key,
                    role="user",
                    written_by=WrittenBy.PERSON,
                    verbatim="原文",
                    origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
                )
            )
            .record.evidence_id
        )
        return door.create(
            MemoryDraft(
                user_id="local",
                kind=MemoryKind.CHAT,
                content=content,
                scopes=("all-apps",),
                evidence_ids=(evidence_id,),
            ),
            request_id=key,
        )

    old, new = create("old fact", "old"), create("new fact", "new")
    store.write_from_hy(
        HySupersede(
            memory_id=old.memory_id,
            request_id="del-chain",
            expected_revision=old.revision,
            superseded_by=(new.memory_id,),
        ),
        user_id="local",
        derivation=store.hy_derivation(),
    )
    door.delete(
        new.memory_id, user_id="local", request_id="delete-new", expected_revision=new.revision
    )
    report = store.supersession_integrity(user_id="local")
    assert report.cycles == ()
    assert report.headless == ()
    assert report.ok is True


def test_supersession_scc_detector_is_iterative_and_keeps_fan_out_valid() -> None:
    """The SCC pass must not recurse (long chains) and must not flag valid fan-out.

    A supersession graph can be a long chain or one node replaced by several
    successors. The detector is iterative so a long chain cannot overflow the
    recursion limit, one node fanning out is acyclic (no cycle reported), and a
    real cycle is a component of size > 1.
    """
    from one_memory.memory_governance import (
        _strongly_connected_components,
        _weakly_connected_components,
    )

    chain = {f"n{i}": {f"n{i + 1}"} for i in range(5_000)}
    assert _strongly_connected_components(chain) == []

    ring = {f"n{i}": {f"n{(i + 1) % 5_000}"} for i in range(5_000)}
    rings = _strongly_connected_components(ring)
    assert len(rings) == 1 and len(rings[0]) == 5_000

    assert _strongly_connected_components({"a": {"a"}}) == [("a",)]

    fan_out = {"root": {f"child{i}" for i in range(10)}}
    assert _strongly_connected_components(fan_out) == []
    assert len(_weakly_connected_components(fan_out)) == 1


def test_supersession_integrity_is_user_scoped(tmp_path) -> None:
    """One user's cycle must not appear in another user's integrity report.

    The read-only audit joins both endpoints to the queried user, so a cycle
    belonging to user B never leaks into user A's report (and vice versa). This
    matters because the report is meant to be safe to surface per user.
    """
    database = tmp_path / "one-memory.sqlite3"
    store = MemoryGovernanceStore(database)
    with store._connection() as connection:
        for memory_id, user, status in (
            ("local-a", "local", "confirmed"),
            ("local-b", "local", "superseded"),
            ("other-a", "other", "superseded"),
            ("other-b", "other", "superseded"),
        ):
            connection.execute(
                "INSERT INTO governed_memories "
                "(memory_id,user_id,kind,status,content,scopes_json,revision,"
                "created_at,updated_at) VALUES (?,?,?,?,?,?,1,?,?)",
                (memory_id, user, "chat", status, "x", '["all-apps"]', "now", "now"),
            )
        # local: a healthy edge (b -> a, a is the confirmed head).
        connection.execute(
            "INSERT INTO governed_memory_supersessions (memory_id,superseded_by,created_at) "
            "VALUES ('local-b','local-a','now')"
        )
        # other: a real cycle.
        connection.execute(
            "INSERT INTO governed_memory_supersessions (memory_id,superseded_by,created_at) "
            "VALUES ('other-a','other-b','now')"
        )
        connection.execute(
            "INSERT INTO governed_memory_supersessions (memory_id,superseded_by,created_at) "
            "VALUES ('other-b','other-a','now')"
        )
    assert store.supersession_integrity(user_id="local").ok is True
    other = store.supersession_integrity(user_id="other")
    assert other.cycles == (("other-a", "other-b"),)
    assert other.ok is False
