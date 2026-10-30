import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from one_memory.api import create_app
from one_memory.config import Settings
from one_memory.file_imports import FileImportStore
from one_memory.imports.models import EvidenceContentKind, WrittenBy
from one_memory.secrets import InMemorySecretStore
from tests.source_consent import connection_request


def test_existing_memory_preview_keeps_unknown_author_and_exact_original(tmp_path):
    files = FileImportStore(tmp_path / "imports")
    text = "  可能只适合这个项目，预算<5，不是>5。\n原始讨论未提供。\n"
    receipt = files.prepare(text.encode(), "report.md", input_kind="memory_content")
    assert receipt.source_id == "memory-content-export"
    assert receipt.content_kind == "memory_content"
    assert receipt.messages == receipt.unknown_author_messages == 1
    assert receipt.human_messages == receipt.agent_messages == 0
    assert receipt.preview[0].text == text
    assert receipt.preview[0].written_by == "unknown"
    assert receipt.preview[0].created_at is None
    stored = json.loads(
        (files.root / "staging" / str(receipt.upload_id) / "messages.jsonl").read_text()
    )
    assert stored["content_kind"] == "memory_content"
    assert stored["role"] == "memory" and stored["written_by"] == "unknown"
    assert stored["verbatim"] == text


@pytest.mark.parametrize(
    "record",
    [
        {"content": "valid", "author": "tool"},
        {"content": "valid", "time": "2026-09-12T09:00:00"},
        {"content": "valid", "confidence": 1},
        {"content": ""},
    ],
)
def test_invalid_existing_memory_records_never_leave_a_partial_upload(tmp_path, record):
    files = FileImportStore(tmp_path / "imports")
    payload = json.dumps(
        {"format": "one-memory-content-v1", "memories": [{"content": "first"}, record]}
    ).encode()
    with pytest.raises(ValueError):
        files.prepare(payload, "memories.json", input_kind="memory_content")
    assert not files.root.exists()


def test_memory_content_requires_explicit_mode_and_rejects_vendor_guessing(tmp_path):
    files = FileImportStore(tmp_path / "imports")
    payload = b'{"format":"one-memory-content-v1","memories":[{"content":"existing"}]}'
    with pytest.raises(ValueError):
        files.prepare(payload, "memories.json")
    with pytest.raises(ValueError):
        files.prepare(
            b'{"memories":"unknown vendor schema"}', "memories.json", input_kind="memory_content"
        )
    with pytest.raises(ValueError):
        files.prepare(payload, "memories.zip", input_kind="memory_content")
    with pytest.raises(ValueError):
        files.prepare(
            b'{"format":"one-memory-content-v1","memories":[{"content":"a","content":"b"}]}',
            "memories.json",
            input_kind="memory_content",
        )


def test_chat_and_memory_interpretations_of_same_bytes_have_distinct_publication(tmp_path):
    files = FileImportStore(tmp_path / "imports")
    payload = b"User: These are reported notes, not a verified decision."
    chat = files.prepare(payload, "report.txt")
    memory = files.prepare(payload, "report.txt", input_kind="memory_content")
    files.commit(chat.upload_id)
    files.commit(memory.upload_id)
    assert chat.checksum == memory.checksum
    assert chat.source_id != memory.source_id
    assert len(list((files.root / "ready").glob("*/*.jsonl"))) == 2


def test_memory_content_normal_api_import_preserves_kind_and_dedup_revoke(tmp_path):
    async def scenario():
        app = create_app(
            Settings(
                data_dir=tmp_path / "data",
                source_home=tmp_path / "home",
                api_token=SecretStr("test"),
                automatic_memory_processing=False,
                live_sync_enabled=False,
            ),
            secret_store=InMemorySecretStore(),
        )
        async with (
            app.router.lifespan_context(app),
            AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer test"},
            ) as client,
        ):
            value = {
                "format": "one-memory-content-v1",
                "memories": [
                    {"content": "Agent提议，尚未确认。", "author": "agent", "time": None},
                    {"content": "预算<5，原始聊天未提供。"},
                ],
            }
            uploaded = await client.post(
                "/api/v1/import-files?filename=report.json&input_kind=memory_content",
                content=json.dumps(value).encode(),
            )
            assert uploaded.status_code == 201
            receipt = uploaded.json()
            assert receipt["agent_messages"] == receipt["unknown_author_messages"] == 1
            assert app.state.evidence_store.count() == 0
            assert (await client.get("/api/v1/import-jobs")).json() == []
            await client.post(
                f"/api/v1/import-files/{receipt['upload_id']}/commit", json={"confirmed": True}
            )
            linked = await client.post(
                "/api/v1/sources/connect",
                json=await connection_request(client, [receipt["source_id"]], enable_sync=True),
            )
            assert linked.status_code == 200 and linked.json()["failed_sources"] == {}
            job = await app.state.import_supervisor.wait(linked.json()["jobs"][0]["job_id"])
            assert job.stored == 2 and job.errors == 0
            source = next(
                s
                for s in (await client.get("/api/v1/sources")).json()
                if s["source_id"] == receipt["source_id"]
            )
            assert source["sync_completeness"] == "one_time_snapshot"
            found = app.state.evidence_store.search_conversations(
                "原始聊天", user_id="local", limit=5
            )
            evidence = app.state.evidence_store.get(found[0].evidence_id)
            assert evidence.content_kind is EvidenceContentKind.MEMORY_CONTENT
            assert evidence.written_by is WrittenBy.UNKNOWN and evidence.created_at is None
            assert evidence.verbatim == value["memories"][1]["content"]
            again = await client.post(
                "/api/v1/import-jobs", json={"source_id": receipt["source_id"]}
            )
            assert (await app.state.import_supervisor.wait(again.json()["job_id"])).stored == 0
            assert app.state.memory_governance_store.list(user_id="local") == []
            await client.post(f"/api/v1/sources/{receipt['source_id']}/revoke")
            assert (
                await client.get(f"/api/v1/sources/{receipt['source_id']}/import-receipt")
            ).status_code == 403

    asyncio.run(scenario())
