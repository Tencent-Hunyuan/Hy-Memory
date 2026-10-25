"""Search imported conversations with no generative model configured at all.

This answers the question a user would ask: I imported my history and I
never set up a model, can One Memory find things I said? This goes through the
product's own entry points only: authorize the source, run an import job,
search `/api/v1/context/search`.
"""

import asyncio
import json
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from one_memory.api import create_app
from one_memory.config import Settings
from one_memory.imports.sources.registry import build_source_catalog
from one_memory.secrets import InMemorySecretStore
from tests.source_consent import authorized_request, thaw_source

TOKEN = "raw-search-no-model"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
LICENCE = "我们最后决定 One Memory 用 MIT 协议发布，不再考虑 GPL。"
BACKUP = "备份先写到本地磁盘，再同步到对象存储。"
SYNONYM = "开源软件的授权条款最后怎么挑的"

pytestmark = [pytest.mark.integration, pytest.mark.serial]


def transcript(root, name, *texts):
    lines = [
        json.dumps(
            {
                "type": "response_item",
                "timestamp": datetime(2026, 9, 1, tzinfo=UTC).isoformat(),
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "text", "text": text}],
                },
            }
        )
        for text in texts
    ]
    (root / f"rollout-{name}.jsonl").write_text("\n".join(lines) + "\n")


def application(tmp_path):
    home = tmp_path / "home"
    root = home / ".codex/sessions"
    root.mkdir(parents=True)
    transcript(root, "licence", LICENCE)
    transcript(root, "backup", BACKUP)
    data = tmp_path / "data"
    data.mkdir()
    return create_app(
        Settings(
            _env_file=None,
            data_dir=data,
            source_home=home,
            api_token=SecretStr(TOKEN),
            automatic_memory_processing=False,
            live_sync_enabled=False,
        ),
        source_catalog={"codex": build_source_catalog(home=home, environment={})["codex"]},
        secret_store=InMemorySecretStore(),
    )


async def imported(app, client):
    assert (await client.get("/api/v1/model-profiles")).json() == [], "no model may be configured"
    authorized = await client.post(
        "/api/v1/sources/codex/authorize",
        json=await authorized_request(client, "codex"),
    )
    authorized.raise_for_status()
    await thaw_source(client, "codex", authorized.json())
    started = await client.post("/api/v1/import-jobs", json={"source_id": "codex"})
    started.raise_for_status()
    job = await app.state.import_supervisor.wait(started.json()["job_id"])
    assert job.status == "completed" and job.stored == 2


async def search(client, query, *, attempts=1):
    for remaining in range(attempts, 0, -1):
        response = await client.post("/api/v1/context/search", json={"query": query}, headers=AUTH)
        response.raise_for_status()
        body = response.json()
        if body["items"] or remaining == 1:
            return body
        await asyncio.sleep(0.5)
    raise AssertionError("unreachable")


def texts(body):
    return [
        message["text"]
        for item in body["items"]
        if item["kind"] == "raw"
        for message in item["messages"]
    ]


def test_shared_words_are_found_without_any_model_or_index(tmp_path):
    async def scenario():
        app = application(tmp_path)
        async with (
            app.router.lifespan_context(app),
            AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test", headers=AUTH
            ) as client,
        ):
            await imported(app, client)
            body = await search(client, "MIT 协议")
            assert body["state"] == "grounded"
            assert any(LICENCE in text for text in texts(body))

            missed = await search(client, SYNONYM)
            # Different words are not a lexical match, and there is no private
            # vector index to pretend otherwise.
            assert not texts(missed)

    asyncio.run(scenario())
