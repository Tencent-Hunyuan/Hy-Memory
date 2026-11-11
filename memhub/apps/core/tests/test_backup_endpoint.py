"""The `POST /api/v1/backup` endpoint the CLI's `backup create` calls.

Two properties matter and both are asserted:

1. It makes a real, readable copy — not just a 200 with a path. Since 1.0.7
   the copy is a v2 backup directory with the memories
   (`test_backup_includes_memories` covers those).
2. It refuses a target outside the data dir. The CLI can name any path, so an
   unrestricted write here would turn a read-only client into a file-write
   primitive.
"""

from __future__ import annotations

import json
import sqlite3

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from one_memory.api import create_app
from one_memory.config import Settings


def _client(tmp_path):
    """App plus an authorized client, matching the other API tests.

    The lifespan context is what populates `app.state.evidence_store`; without
    it the route raises AttributeError rather than exercising anything.
    """
    app = create_app(
        Settings(
            _env_file=None,
            data_dir=tmp_path,
            source_home=tmp_path,
            api_token=SecretStr("test-token"),
        )
    )
    return app, AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={
            "Authorization": "Bearer test-token",
            "Origin": "http://127.0.0.1:5173",
        },
    )


@pytest.mark.anyio
async def test_backup_writes_a_readable_copy(tmp_path) -> None:
    app, client = _client(tmp_path)
    async with app.router.lifespan_context(app), client:
        response = await client.post(
            "/api/v1/backup",
            json={"target": "backups/one.sqlite3"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    # A v2 backup is a directory: `one.sqlite3` names `one.memhub-backup`.
    backup = tmp_path / "backups" / "one.memhub-backup"
    assert body["target"] == str(backup.resolve())
    assert body["memories_included"] is True and body["schema_version"] == 2
    manifest = json.loads((backup / "manifest.json").read_text())
    assert body["bytes"] == manifest["total_bytes"] > 0
    target = backup / "management" / "one-memory.sqlite3"

    # A backup that cannot be opened is worse than none: it looks like safety
    # and is not.
    connection = sqlite3.connect(target)
    try:
        tables = connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
        ).fetchone()[0]
    finally:
        connection.close()
    assert tables > 0, "backup is not a readable database"


@pytest.mark.anyio
async def test_backup_refuses_a_target_outside_the_data_dir(tmp_path) -> None:
    app, client = _client(tmp_path)
    async with app.router.lifespan_context(app), client:
        response = await client.post(
            "/api/v1/backup",
            json={"target": "/tmp/definitely-outside.sqlite3"},
        )

    assert response.status_code == 422
    assert response.json()["detail"] == "backup_target_outside_data_dir"
