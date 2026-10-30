"""The file log is shipped to us in bug reports; it must not carry originals.

The detector is checked against a planted line first. Without that, "nothing
leaked" would also be the answer when nothing at all was written, which is how
an empty log passes for a private one.
"""

import asyncio
import json
import logging
from datetime import UTC, datetime

from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from one_memory.api import create_app
from one_memory.config import Settings
from one_memory.imports.sources.registry import build_source_catalog
from one_memory.log_file import log_path, start_logging_to_file
from one_memory.secrets import InMemorySecretStore
from tests.source_consent import authorized_request, thaw_source

TOKEN = "log-privacy-token"
SECRET_TURN = "我的社保号是 91827364550 而且周会避开周五"
SECRET_QUERY = "周会避开周五"


def detach(destination) -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "baseFilename", None) == str(destination.resolve()):
            root.removeHandler(handler)
            handler.close()


def test_the_detector_sees_an_original_that_does_reach_the_log(tmp_path):
    """Positive control for the two tests below."""
    destination = start_logging_to_file(tmp_path)
    try:
        logging.getLogger("one_memory.probe").warning("imported: %s", SECRET_TURN)
    finally:
        detach(destination)

    assert SECRET_TURN in destination.read_text(encoding="utf-8")


def test_importing_and_searching_leaves_no_original_query_or_token_behind(tmp_path):
    home = tmp_path / "home"
    root = home / ".codex/sessions"
    root.mkdir(parents=True)
    (root / "rollout-private.jsonl").write_text(
        json.dumps(
            {
                "type": "response_item",
                "timestamp": datetime(2026, 9, 1, tzinfo=UTC).isoformat(),
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "text", "text": SECRET_TURN}],
                },
            }
        )
        + "\n"
    )
    data = tmp_path / "data"
    data.mkdir()
    destination = start_logging_to_file(data)

    async def scenario():
        app = create_app(
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
        async with (
            app.router.lifespan_context(app),
            AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": f"Bearer {TOKEN}"},
            ) as client,
        ):
            authorized = await client.post(
                "/api/v1/sources/codex/authorize",
                json=await authorized_request(client, "codex"),
            )
            authorized.raise_for_status()
            await thaw_source(client, "codex", authorized.json())
            started = await client.post("/api/v1/import-jobs", json={"source_id": "codex"})
            started.raise_for_status()
            job = await app.state.import_supervisor.wait(started.json()["job_id"])
            assert job.status == "completed" and job.stored == 1
            # The search is logged as a request whatever the SDK answers; the
            # query it carried must not be part of that record.
            searched = await client.post("/api/v2/context/search", json={"query": SECRET_QUERY})
            assert searched.status_code in {200, 503}

    try:
        asyncio.run(scenario())
    finally:
        detach(destination)

    written = log_path(data).read_text(encoding="utf-8")
    assert SECRET_TURN not in written
    assert SECRET_QUERY not in written
    assert TOKEN not in written
    assert "91827364550" not in written


def test_a_failing_import_reports_the_file_without_quoting_it(tmp_path):
    home = tmp_path / "home"
    root = home / ".codex/sessions"
    root.mkdir(parents=True)
    (root / "rollout-broken.jsonl").write_text(f"{{not json {SECRET_TURN}\n")
    data = tmp_path / "data"
    data.mkdir()
    destination = start_logging_to_file(data)

    async def scenario():
        app = create_app(
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
        async with (
            app.router.lifespan_context(app),
            AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": f"Bearer {TOKEN}"},
            ) as client,
        ):
            authorized = await client.post(
                "/api/v1/sources/codex/authorize",
                json=await authorized_request(client, "codex"),
            )
            authorized.raise_for_status()
            await thaw_source(client, "codex", authorized.json())
            started = await client.post("/api/v1/import-jobs", json={"source_id": "codex"})
            started.raise_for_status()
            await app.state.import_supervisor.wait(started.json()["job_id"])

    try:
        asyncio.run(scenario())
    finally:
        detach(destination)

    written = log_path(data).read_text(encoding="utf-8")
    assert SECRET_TURN not in written
    assert "91827364550" not in written
