import asyncio
import sqlite3
from datetime import UTC, datetime
from types import SimpleNamespace

from one_memory.api import (
    _history_import_gate_released,
    _run_backfill,
    _sedimentation_claim_enabled,
    _source_writes_on,
)
from one_memory.imports.models import EvidenceInput, EvidenceOrigin, OriginMetadata, WrittenBy
from one_memory.imports.storage import EvidenceStore
from one_memory.memory_server.config_state import ConfigStore
from one_memory.memory_server.durable_queue import DurableWriteQueue
from one_memory.memory_server.unified_entry import UnifiedWriteIntake


def _application(tmp_path, *, sync_enabled: bool):
    database = tmp_path / "one-memory.sqlite3"
    evidence = EvidenceStore(database)
    config = ConfigStore(tmp_path)
    config.set_sync("cursor", sync_enabled)
    queue = DurableWriteQueue(database, evidence=evidence)
    return SimpleNamespace(
        state=SimpleNamespace(
            settings=SimpleNamespace(automatic_memory_processing=True),
            config_store=config,
            source_grant_store=SimpleNamespace(
                authorized_root=lambda **_kwargs: tmp_path / "authorized"
            ),
            evidence_store=evidence,
            memory_write_queue=queue,
            memory_write_intake=UnifiedWriteIntake(
                queue,
                source_writes_permitted=config.sync_enabled,
            ),
            memory_backfill_error=None,
        )
    )


def _put_complete_turn(app) -> tuple[str, str]:
    values = []
    for message_id, role, written_by, verbatim in (
        ("user-1", "user", WrittenBy.PERSON, "Remember the strict queue."),
        ("assistant-1", "assistant", WrittenBy.AGENT, "I will keep it ordered."),
    ):
        values.append(
            app.state.evidence_store.put(
                EvidenceInput(
                    source_id="cursor",
                    conversation_id="conversation-1",
                    message_id=message_id,
                    role=role,
                    written_by=written_by,
                    verbatim=verbatim,
                    created_at=datetime.now(UTC),
                    origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
                )
            ).record.evidence_id
        )
    return tuple(values)


def test_backfill_puts_history_on_write_jobs_without_scheduling_memory_tasks(tmp_path) -> None:
    app = _application(tmp_path, sync_enabled=True)
    evidence_ids = _put_complete_turn(app)

    asyncio.run(_run_backfill(app))

    with sqlite3.connect(app.state.evidence_store.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM memory_write_jobs").fetchone()[0] == 1
        queued = {
            row[0]
            for row in connection.execute(
                "SELECT evidence_id FROM memory_write_job_evidence"
            ).fetchall()
        }
        assert queued == set(evidence_ids)
        task_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_tasks'"
        ).fetchone()
        assert (
            task_table is None
            or connection.execute("SELECT COUNT(*) FROM memory_tasks").fetchone()[0] == 0
        )


def test_source_pause_skips_history_until_the_source_is_reenabled(tmp_path) -> None:
    app = _application(tmp_path, sync_enabled=False)
    _put_complete_turn(app)

    asyncio.run(_run_backfill(app))
    assert app.state.memory_write_queue.unsubmitted_evidence_ids()

    app.state.config_store.set_sync("cursor", True)
    asyncio.run(_run_backfill(app))

    assert app.state.memory_write_queue.unsubmitted_evidence_ids() == ()


def test_the_manual_source_is_admitted_by_grant_without_a_toggle(tmp_path) -> None:
    """A source that is not a connected app has no toggle to read.

    `one-memory-manual` is admitted by its grant and never appears in the
    configuration document, so any gate that reads the per-app toggle answers
    False for it while the write gate answers True. The drain's claim gate used
    to be that gate, and every manual note's job was skipped on every tick.
    """
    config = ConfigStore(tmp_path)
    app = SimpleNamespace(
        state=SimpleNamespace(
            config_store=config,
            model_profile_store=SimpleNamespace(active_identity=lambda: ("profile", 1)),
            source_grant_store=SimpleNamespace(
                receipt=lambda **_kwargs: SimpleNamespace(revoked_at=None)
            ),
        )
    )

    assert _source_writes_on(app, "one-memory-manual") is True
    assert config.sync_enabled("one-memory-manual") is False

    assert _sedimentation_claim_enabled(app, "one-memory-manual") is True
    config.set_sedimentation_enabled(False)
    assert _sedimentation_claim_enabled(app, "one-memory-manual") is False


def test_the_manual_source_is_allowed_without_a_grant_row_until_revoked(tmp_path) -> None:
    """The SDK add path writes no grant row, and intake admits the record.

    Claim must answer the same: a missing row is allowed, an explicit
    revocation is not.
    """
    receipt: list[object] = [None]
    app = SimpleNamespace(
        state=SimpleNamespace(
            config_store=ConfigStore(tmp_path),
            model_profile_store=SimpleNamespace(active_identity=lambda: ("profile", 1)),
            source_grant_store=SimpleNamespace(receipt=lambda **_kwargs: receipt[0]),
        )
    )

    assert _source_writes_on(app, "one-memory-manual") is True
    assert _sedimentation_claim_enabled(app, "one-memory-manual") is True
    receipt[0] = SimpleNamespace(revoked_at="2026-10-27")
    assert _source_writes_on(app, "one-memory-manual") is False
    assert _sedimentation_claim_enabled(app, "one-memory-manual") is False


def test_claim_gate_waits_without_polling_when_no_model_is_active(tmp_path) -> None:
    config = ConfigStore(tmp_path)
    app = SimpleNamespace(
        state=SimpleNamespace(
            config_store=config,
            model_profile_store=SimpleNamespace(active_identity=lambda: None),
            source_grant_store=SimpleNamespace(
                receipt=lambda **_kwargs: SimpleNamespace(revoked_at=None)
            ),
        )
    )

    assert _sedimentation_claim_enabled(app, "one-memory-manual") is False


def test_first_import_gate_waits_for_every_selected_history_job() -> None:
    state = SimpleNamespace(
        status="importing",
        selected_source_ids=("cursor", "codex"),
        started_at=datetime(2026, 10, 18, tzinfo=UTC),
    )
    jobs = [
        SimpleNamespace(
            operation="history",
            source_id="cursor",
            created_at=datetime(2026, 10, 18, tzinfo=UTC),
            status="completed",
        ),
        SimpleNamespace(
            operation="history",
            source_id="codex",
            created_at=datetime(2026, 10, 18, tzinfo=UTC),
            status="running",
        ),
    ]
    app = SimpleNamespace(
        state=SimpleNamespace(
            onboarding_store=SimpleNamespace(get=lambda: state),
            import_job_store=SimpleNamespace(list_jobs=lambda: jobs),
        )
    )

    assert _history_import_gate_released(app) is False
    jobs[1].status = "failed"
    assert _history_import_gate_released(app) is True
