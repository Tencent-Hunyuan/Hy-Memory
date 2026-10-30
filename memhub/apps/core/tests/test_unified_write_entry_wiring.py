"""The composition root routes every Evidence-first entry through one queue.

Companion to `test_memory_server_unified_entry.py` (C2's adapter). That file
proves the adapter works in isolation; this one pins the *wiring* in `api.py` /
`memory_additions.py`, which is the C0-owned half:

  red state  - before the wiring, `on_stable_evidence` was the supervisor's
               `enqueue_evidence` and a committed record had no
               `memory_write_jobs` row;
  positive   - after it, both the import callback and a hand-added memory submit to
               the durable queue under `entry:<evidence_id>`;
  idempotent - replaying the same evidence returns the original job.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sqlite3
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from one_memory.api import create_app
from one_memory.config import Settings
from one_memory.imports.models import (
    EvidenceInput,
    EvidenceOrigin,
    OriginMetadata,
    WrittenBy,
)
from one_memory.imports.sources.registry import RootCandidateKind
from one_memory.secrets import InMemorySecretStore


def _application(tmp_path):
    return create_app(
        Settings(
            data_dir=tmp_path / "data",
            source_home=tmp_path,
            api_token=SecretStr("wire-test"),
            imports_enabled=False,
            automatic_memory_processing=True,
            live_sync_enabled=False,
        ),
        secret_store=InMemorySecretStore(),
    )


def _configure_frozen(app, tmp_path, *, source_id: str) -> None:
    """Grant a source through the real store and leave processing frozen."""

    home = tmp_path / "grant-home"
    home.mkdir(exist_ok=True)
    root = home / "projects"
    root.mkdir(exist_ok=True)
    grants = app.state.source_grant_store
    grants.grant(
        user_id="local",
        source_id=source_id,
        root=root,
        candidate_kind=RootCandidateKind.DEFAULT,
    )


def _committed_record(app, *, source_id: str, message_id: str):
    return app.state.evidence_store.put(
        EvidenceInput(
            source_id=source_id,
            conversation_id=f"conversation-{source_id}",
            message_id=message_id,
            role="user",
            written_by=WrittenBy.PERSON,
            verbatim="API-level Harness Freeze probe",
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    ).record


def test_unified_intake_refuses_frozen_source_before_creating_a_job(tmp_path, monkeypatch) -> None:
    """The API-built intake must enforce the Harness Freeze switch.

    This is the composition-root half of `cc239dd1`: the adapter refuses
    correctly, but a missing constructor argument would make the production
    path silently bypass it.
    """

    async def scenario():
        app = _application(tmp_path)
        async with app.router.lifespan_context(app):
            app.state.memory_write_intake.queue.min_free_bytes = 0
            _configure_frozen(app, tmp_path, source_id="fixture")
            record = _committed_record(app, source_id="fixture", message_id="off-1")
            assert app.state.memory_write_intake.source_writes_permitted is not None
            assert app.state.memory_write_intake.source_writes_permitted("fixture") is False

            try:
                await asyncio.to_thread(
                    app.state.memory_write_intake.submit_evidence,
                    record,
                    first_round=1,
                    last_round=1,
                    priority="normal",
                )
            except RuntimeError as error:
                assert "model_content_source_disabled" in str(error)
            else:
                raise AssertionError("frozen Harness must refuse new intake")

            with sqlite3.connect(app.state.evidence_store.database_path) as db:
                assert db.execute("SELECT COUNT(*) FROM memory_write_jobs").fetchone()[0] == 0
                assert db.execute("SELECT COUNT(*) FROM memory_tasks").fetchone()[0] == 0
                assert (
                    db.execute(
                        "SELECT COUNT(*) FROM evidence WHERE evidence_id=?",
                        (record.evidence_id,),
                    ).fetchone()[0]
                    == 1
                )

    asyncio.run(scenario())


def test_unified_intake_write_on_creates_one_job(tmp_path, monkeypatch) -> None:
    async def scenario():
        app = _application(tmp_path)
        async with app.router.lifespan_context(app):
            app.state.memory_write_intake.queue.min_free_bytes = 0
            source_id = "fixture"
            home = tmp_path / "grant-home"
            home.mkdir(exist_ok=True)
            root = home / "projects"
            root.mkdir(exist_ok=True)
            app.state.source_grant_store.grant(
                user_id="local",
                source_id=source_id,
                root=root,
                candidate_kind=RootCandidateKind.DEFAULT,
            )
            app.state.config_store.set_sync(source_id, True)
            record = _committed_record(app, source_id=source_id, message_id="on-1")
            assert app.state.memory_write_intake.source_writes_permitted is not None
            assert app.state.memory_write_intake.source_writes_permitted(source_id) is True

            receipt = await asyncio.to_thread(
                app.state.memory_write_intake.submit_evidence,
                record,
                first_round=1,
                last_round=1,
                priority="normal",
            )
            assert receipt.enqueued is True
            with sqlite3.connect(app.state.evidence_store.database_path) as db:
                assert db.execute("SELECT COUNT(*) FROM memory_write_jobs").fetchone()[0] == 1

    asyncio.run(scenario())


def test_import_callback_goes_through_the_durable_intake(tmp_path) -> None:
    """The import callback must NOT be the supervisor's direct enqueue."""

    async def scenario():
        app = _application(tmp_path)
        async with app.router.lifespan_context(app):
            callback = app.state.import_supervisor.on_stable_evidence
            assert callback is not None
            # A bound method would name the supervisor's method; the wiring uses
            # the local `enqueue_stable_evidence` closure instead. Compare by
            # qualname because `a.method is a.method` is always False for a
            # freshly-bound method and would make this check vacuous.
            assert callback.__qualname__.endswith(".enqueue_stable_evidence")
            assert callback.__module__ == "one_memory.api"
            # The shared intake exists and is what the callback closes over.
            assert app.state.memory_write_intake.queue is not None
            # Same object for the memory-add path, so both entries share one queue.
            assert app.state.memory_addition_store.intake is app.state.memory_write_intake

    asyncio.run(scenario())


def test_live_import_callback_submits_realtime_priority(tmp_path) -> None:
    from one_memory.imports.runner import live_sync_arrival

    async def scenario():
        app = _application(tmp_path)
        async with app.router.lifespan_context(app):
            source_id = "fixture"
            home = tmp_path / "live-home"
            root = home / "projects"
            root.mkdir(parents=True)
            app.state.source_grant_store.grant(
                user_id="local",
                source_id=source_id,
                root=root,
                candidate_kind=RootCandidateKind.DEFAULT,
            )
            app.state.config_store.set_sync(source_id, True)
            user = _committed_record(app, source_id=source_id, message_id="live-user")
            # Live writes by the turn rule: 5 complete turns, the fifth
            # completed by the next user message.
            for index in range(10):
                record = app.state.evidence_store.put(
                    EvidenceInput(
                        source_id=source_id,
                        conversation_id=user.conversation_id,
                        message_id=f"live-{index}",
                        role="assistant" if index % 2 == 0 else "user",
                        written_by=WrittenBy.AGENT if index % 2 == 0 else WrittenBy.PERSON,
                        verbatim=f"Live line {index}",
                        origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
                    )
                ).record
            callback = app.state.import_supervisor.on_stable_evidence
            assert callback is not None
            token = live_sync_arrival.set(True)
            try:
                await callback(record)
            finally:
                live_sync_arrival.reset(token)
            with sqlite3.connect(app.state.evidence_store.database_path) as db:
                rows = db.execute(
                    "SELECT priority,evidence_ids_json FROM memory_write_jobs "
                    "WHERE idempotency_key LIKE ?",
                    ("turn:v1:%",),
                ).fetchall()
            assert [(priority, len(json.loads(ids))) for priority, ids in rows] == [("live", 10)]

    asyncio.run(scenario())


def test_memory_add_creates_one_durable_row_and_replays_idempotently(
    tmp_path,
) -> None:
    async def scenario():
        app = _application(tmp_path)
        async with app.router.lifespan_context(app):
            client = AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer wire-test"},
            )
            request_id = str(uuid4())
            body = {"request_id": request_id, "text": "记住这条", "scope": "all-apps"}
            created = await client.post("/api/v2/memories", json=body)
            assert created.status_code == 201
            evidence_id = created.json()["evidence_id"]
            key = f"entry:{evidence_id}"

            # Positive control: the entry now has a durable row.
            for _ in range(200):
                await asyncio.sleep(0.05)
                with sqlite3.connect(app.state.evidence_store.database_path) as db:
                    rows = db.execute(
                        "SELECT idempotency_key FROM memory_write_jobs WHERE idempotency_key=?",
                        (key,),
                    ).fetchall()
                if rows:
                    break
            assert rows == [(key,)]

            # Idempotent replay: the same request submitted again must not add
            # a second row.
            replay = await client.post("/api/v2/memories", json=body)
            assert replay.status_code == 201
            assert replay.json()["evidence_id"] == evidence_id
            await asyncio.sleep(0.2)
            with sqlite3.connect(app.state.evidence_store.database_path) as db:
                count = db.execute(
                    "SELECT COUNT(*) FROM memory_write_jobs WHERE idempotency_key=?",
                    (key,),
                ).fetchone()[0]
            assert count == 1

    asyncio.run(scenario())


def test_startup_compensation_keeps_pending_until_the_submit_succeeds(
    tmp_path, monkeypatch
) -> None:
    """A committed Evidence row with a pending marker is replayed on startup.

    The first submit is made to fail; the pending marker must survive so the
    next startup can replay it, and the replay must produce exactly one durable
    `entry:<id>` row. This pins the two-phase startup contract in `api.py`
    (`list_pending_dispatch_files` -> read consume=False -> await submit ->
    consume on success); the old `take_all_stable_records` deleted the marker
    while reading, so a failed submit lost the record.
    """
    from one_memory.imports.models import (
        EvidenceInput,
        EvidenceOrigin,
        OriginMetadata,
        WrittenBy,
    )

    input_record = EvidenceInput(
        source_id="fixture",
        conversation_id="conversation",
        message_id="startup-1",
        role="user",
        written_by=WrittenBy.PERSON,
        verbatim="remember me",
        origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
    )
    reply_record = input_record.model_copy(
        update={
            "message_id": "startup-2",
            "role": "assistant",
            "written_by": WrittenBy.AGENT,
            "verbatim": "remembered",
        }
    )
    file_key = "a" * 64

    async def scenario() -> None:
        # First context: learn the store, then seed a committed Evidence row
        # plus its pending marker, exactly as a crash between commit and
        # enqueue would leave it.
        seed_app = _application(tmp_path)
        async with seed_app.router.lifespan_context(seed_app):
            store = seed_app.state.evidence_store
            # The startup replay is a write path: grant the fixture source and
            # leave its write switch on so this test reaches the compensation
            # path rather than being correctly refused by the write-off guard.
            home = tmp_path / "startup-grant-home"
            home.mkdir(exist_ok=True)
            root = home / "projects"
            root.mkdir(exist_ok=True)
            seed_app.state.source_grant_store.grant(
                user_id="local",
                source_id="fixture",
                root=root,
                candidate_kind=RootCandidateKind.DEFAULT,
            )
            seed_app.state.config_store.set_sync("fixture", True)
        with store._connect(writing=True) as connection:
            store.put(input_record, connection=connection)
            stored = store.put(reply_record, connection=connection).record
            store.stage_memory_dispatch(
                stored,
                file_key=file_key,
                connection=connection,
            )

        # Second startup: make the durable submit fail. The pending marker
        # must survive for the next startup (the old consume-on-read path
        # deleted it here and lost the record).
        fail_app = _application(tmp_path)

        def fail_submit(*_args, **_kwargs):
            raise RuntimeError("intake_unavailable")

        # Patch the instance the composition root will build by patching the
        # class method for the duration of this startup.
        from one_memory.memory_server import unified_entry

        real_submit = unified_entry.UnifiedWriteIntake.submit_records
        monkeypatch.setattr(unified_entry.UnifiedWriteIntake, "submit_records", fail_submit)
        # The two-phase path swallows a per-file submit failure and defers the
        # replay; the legacy consume-on-read path both deletes the marker and
        # lets the exception abort startup. Tolerate the abort here so the
        # assertion below is about the durable marker, not about the error.
        with contextlib.suppress(RuntimeError):
            async with fail_app.router.lifespan_context(fail_app):
                pass
        monkeypatch.setattr(unified_entry.UnifiedWriteIntake, "submit_records", real_submit)
        assert store.list_pending_dispatch_files() != (), (
            "a failed startup submit must leave the pending marker for replay"
        )

        # Third startup: the submit now succeeds and consumes the marker.
        app2 = _application(tmp_path)
        async with app2.router.lifespan_context(app2):
            for _ in range(200):
                await asyncio.sleep(0.05)
                with sqlite3.connect(app2.state.evidence_store.database_path) as db:
                    rows = db.execute(
                        "SELECT idempotency_key FROM memory_write_jobs "
                        "WHERE idempotency_key LIKE 'turn:v1:%'",
                    ).fetchall()
                if rows:
                    break
        assert len(rows) == 1 and rows[0][0].startswith("turn:v1:")
        assert app2.state.evidence_store.list_pending_dispatch_files() == ()

    asyncio.run(scenario())
