from __future__ import annotations

import io
import json
from contextlib import nullcontext
from types import SimpleNamespace

from one_memory import bm25_maintenance, cli


def test_upgrade_requires_explicit_confirmation() -> None:
    output = io.StringIO()

    assert cli.run(["bm25-maintenance", "upgrade", "--json"], output=output) == 2
    assert "--yes" in output.getvalue()


def test_plan_and_upgrade_forward_stable_options(tmp_path, monkeypatch) -> None:
    calls: list[tuple[bool, int]] = []

    def maintain(_data_dir, *, execute, batch_size):
        calls.append((execute, batch_size))
        return {
            "schema_version": 1,
            "product": "memhub-qdrant-bm25-maintenance",
            "state": "ready" if execute else "upgrade_required",
            "success": execute,
            "changed": execute,
        }

    monkeypatch.setattr("one_memory.bm25_maintenance.maintain_bm25", maintain)
    monkeypatch.setattr(
        cli,
        "load_settings",
        lambda: SimpleNamespace(data_dir=tmp_path),
    )
    plan = io.StringIO()
    upgrade = io.StringIO()

    assert (
        cli.run(
            ["bm25-maintenance", "plan", "--batch-size", "17", "--json"],
            output=plan,
        )
        == 3
    )
    assert (
        cli.run(
            ["bm25-maintenance", "upgrade", "--batch-size", "23", "--yes", "--json"],
            output=upgrade,
        )
        == 0
    )
    assert calls == [(False, 17), (True, 23)]
    assert json.loads(plan.getvalue())["state"] == "upgrade_required"
    assert json.loads(upgrade.getvalue())["success"] is True


def test_plan_and_upgrade_refuse_a_runtime_that_is_not_stopped(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        bm25_maintenance,
        "inspect_runtime",
        lambda _data_dir: SimpleNamespace(
            state="running",
            code="runtime_owned",
        ),
    )

    for execute in (False, True):
        result = bm25_maintenance.maintain_bm25(tmp_path, execute=execute)
        assert result["state"] == "blocked"
        assert result["reason"] == "bm25_maintenance_runtime_must_be_stopped"
        assert result["runtime_state"] == "running"
        assert result["product"] == "memhub-qdrant-bm25-maintenance"


def test_a_qdrant_home_delegates_to_the_public_sdk_contract(tmp_path, monkeypatch) -> None:
    database = tmp_path / "one-memory.sqlite3"
    database.touch()
    storage = SimpleNamespace(vector_provider="qdrant")
    space = SimpleNamespace(
        space_id="live-space",
        collection_base="memhub",
        profile_id=None,
        profile_revision=None,
    )
    client = SimpleNamespace(
        plan_qdrant_bm25_upgrade=lambda: {
            "product": "hy-memory-qdrant-bm25-upgrade",
            "state": "upgrade_required",
            "reason": "sparse_schema_missing",
        },
        upgrade_qdrant_bm25=lambda **kwargs: {
            "product": "hy-memory-qdrant-bm25-upgrade",
            "state": "ready",
            "reason": "ready",
            "success": True,
            "changed": True,
            "received": kwargs,
        },
    )
    lease = SimpleNamespace(client=client)
    monkeypatch.setattr(
        bm25_maintenance,
        "inspect_runtime",
        lambda _data_dir: SimpleNamespace(
            state="stopped",
            code="runtime_receipt_missing",
        ),
    )
    monkeypatch.setattr(
        bm25_maintenance,
        "ConfigStore",
        lambda _data_dir: object(),
    )
    monkeypatch.setattr(
        bm25_maintenance,
        "KeyringSecretStore",
        lambda: object(),
    )
    monkeypatch.setattr(
        bm25_maintenance,
        "StorageBackendStore",
        lambda _config, _secrets: SimpleNamespace(resolved=lambda: storage),
    )
    monkeypatch.setattr(
        bm25_maintenance,
        "ModelProfileStore",
        lambda _database: object(),
    )
    monkeypatch.setattr(
        bm25_maintenance,
        "EmbeddingSpaceStore",
        lambda _database: SimpleNamespace(live=lambda: space),
    )
    monkeypatch.setattr(
        bm25_maintenance,
        "builtin_retrieval_snapshot",
        lambda: SimpleNamespace(),
    )
    monkeypatch.setattr(
        bm25_maintenance,
        "retrieval_view",
        lambda snapshot: snapshot,
    )
    monkeypatch.setattr(
        bm25_maintenance,
        "acquire_client",
        lambda *_args, **_kwargs: nullcontext(lease),
    )

    plan = bm25_maintenance.maintain_bm25(tmp_path, execute=False)
    upgraded = bm25_maintenance.maintain_bm25(
        tmp_path,
        execute=True,
        batch_size=31,
    )

    assert plan["state"] == "upgrade_required"
    assert plan["space_id"] == "live-space"
    assert plan["product"] == "memhub-qdrant-bm25-maintenance"
    assert upgraded["success"] is True
    assert upgraded["product"] == "memhub-qdrant-bm25-maintenance"
    assert upgraded["received"] == {
        "writers_stopped": True,
        "batch_size": 31,
    }
