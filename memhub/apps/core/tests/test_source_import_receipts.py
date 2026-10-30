import asyncio
import sqlite3
from contextlib import closing
from datetime import UTC, datetime

from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from one_memory import api as api_module
from one_memory.api import create_app
from one_memory.config import Settings
from one_memory.imports.models import EvidenceInput, EvidenceOrigin, OriginMetadata, WrittenBy
from one_memory.imports.sources.registry import build_source_catalog
from one_memory.imports.storage import EvidenceStore
from tests.source_consent import authorized_request


def make_app(tmp_path):
    home = tmp_path / "home"
    (home / ".claude/projects").mkdir(parents=True)
    catalog = build_source_catalog(home=home, environment={})
    return create_app(
        Settings(
            data_dir=tmp_path / "data",
            source_home=home,
            api_token=SecretStr("receipt-test"),
            automatic_memory_processing=False,
            live_sync_enabled=False,
        ),
        source_catalog={"claude-code": catalog["claude-code"]},
    )


def record(
    index, *, author=WrittenBy.PERSON, source="claude-code", user="local", time=None, text=None
):
    return EvidenceInput(
        user_id=user,
        source_id=source,
        conversation_id=f"c{index % 2}",
        message_id=str(index),
        role=(
            "assistant"
            if author == WrittenBy.AGENT
            else ("tool" if author == WrittenBy.TOOL else "user")
        ),
        written_by=author,
        created_at=time,
        verbatim=text or f"original {index}",
        origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
    )


def test_receipt_counts_saved_records_without_inventing_dates_or_other_sources(tmp_path):
    async def scenario():
        app = make_app(tmp_path)
        async with (
            app.router.lifespan_context(app),
            AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer receipt-test"},
            ) as client,
        ):
            endpoint = "/api/v1/sources/claude-code/import-receipt"
            assert (await client.get(endpoint)).status_code == 403
            (
                await client.post(
                    "/api/v1/sources/claude-code/authorize",
                    json=await authorized_request(client, "claude-code"),
                )
            ).raise_for_status()
            empty = (await client.get(endpoint)).json()
            assert empty["messages"] == 0 and empty["examples"] == []
            for item in [
                record(1, text="预算<5；不能>5。" * 1000),
                record(2, author=WrittenBy.AGENT, time=datetime(2026, 9, 1, tzinfo=UTC)),
                record(3, author=WrittenBy.UNKNOWN),
                record(4, author=WrittenBy.TOOL),
                record(5, text="预算<5；不能>5。" * 1000),
                record(6, source="another-source", text="excluded-source"),
                record(7, user="another-user", text="excluded-user"),
            ]:
                app.state.evidence_store.put(item)
            response = await client.get(endpoint)
            response.raise_for_status()
            data = response.json()
            assert data["source_id"] == "claude-code"
            assert data["messages"] == 5 and data["conversations"] == 2
            assert data["person_messages"] == 3 and data["agent_messages"] == 1
            assert data["unknown_author_messages"] == 0 and data["tool_messages"] == 1
            assert data["unknown_time_messages"] == 4
            assert len(data["examples"]) == 3
            assert "excluded-source" not in response.text and "excluded-user" not in response.text
            excerpt = next(example for example in data["examples"] if "预算" in example["excerpt"])
            assert excerpt["truncated"] and len(excerpt["excerpt"]) == 600
            assert excerpt["created_at"] is None
            assert "<5" in excerpt["excerpt"] and ">5" in excerpt["excerpt"]
            assert app.state.memory_governance_store.list(user_id="local") == []
            assert (await client.get("/api/v1/model-profiles")).json() == []
            (await client.post("/api/v1/sources/claude-code/revoke")).raise_for_status()
            denied = await client.get(endpoint)
            assert denied.status_code == 403 and "预算" not in denied.text
            assert (await client.get("/api/v1/sources/unknown/import-receipt")).status_code == 404
            assert (
                await client.get(endpoint, headers={"Authorization": "Bearer invalid"})
            ).status_code == 401

    asyncio.run(scenario())


def test_receipt_counts_same_named_cursor_sessions_per_workspace(tmp_path):
    from one_memory.source_import_receipts import build_source_import_receipt

    store = EvidenceStore(tmp_path / "receipt.sqlite3")
    for index, workspace in enumerate(("workspace:v1:project-a", "workspace:v1:project-b")):
        store.put(
            record(index, source="cursor", text=f"message {index}").model_copy(
                update={"workspace": workspace, "conversation_id": "same"}
            )
        )

    receipt = build_source_import_receipt(
        store.database_path,
        user_id="local",
        source_id="cursor",
    )

    assert receipt.messages == 2
    assert receipt.conversations == 2


def test_revocation_during_receipt_query_discards_the_old_result(tmp_path, monkeypatch):
    async def scenario():
        app = make_app(tmp_path)
        async with (
            app.router.lifespan_context(app),
            AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer receipt-test"},
            ) as client,
        ):
            (
                await client.post(
                    "/api/v1/sources/claude-code/authorize",
                    json=await authorized_request(client, "claude-code"),
                )
            ).raise_for_status()
            app.state.evidence_store.put(record(1, text="do not return after revocation"))
            original = api_module.build_source_import_receipt

            def revoke_after_query(*args, **kwargs):
                result = original(*args, **kwargs)
                app.state.source_grant_store.revoke(user_id="local", source_id="claude-code")
                return result

            monkeypatch.setattr(api_module, "build_source_import_receipt", revoke_after_query)
            result = await client.get("/api/v1/sources/claude-code/import-receipt")
            assert result.status_code == 403
            assert "do not return" not in result.text

    asyncio.run(scenario())


def test_author_time_counts_use_a_covering_index_not_large_original_bodies(tmp_path):
    store = EvidenceStore(tmp_path / "receipt.sqlite3")
    store.put(record(1, text="large original " * 1000))
    with closing(sqlite3.connect(store.database_path)) as db:
        plan = db.execute(
            "EXPLAIN QUERY PLAN SELECT count(*),count(DISTINCT conversation_id),"
            "sum(written_by='person'),sum(created_at IS NULL) "
            "FROM evidence WHERE user_id=? AND source_id=?",
            ("local", "claude-code"),
        ).fetchall()
    assert any("COVERING INDEX evidence_coverage_index" in row[3] for row in plan)


def test_busy_receipt_returns_retryable_error_without_sql_details(tmp_path, monkeypatch):
    async def scenario():
        app = make_app(tmp_path)
        async with (
            app.router.lifespan_context(app),
            AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer receipt-test"},
            ) as client,
        ):
            (
                await client.post(
                    "/api/v1/sources/claude-code/authorize",
                    json=await authorized_request(client, "claude-code"),
                )
            ).raise_for_status()

            def unavailable(*args, **kwargs):
                raise sqlite3.OperationalError("private path and database details")

            monkeypatch.setattr(api_module, "build_source_import_receipt", unavailable)
            response = await client.get("/api/v1/sources/claude-code/import-receipt")
            assert response.status_code == 503
            assert response.json()["detail"] == "source_import_receipt_unavailable"
            assert "private path" not in response.text

    asyncio.run(scenario())


def test_selected_query_can_be_verified_after_it_leaves_latest_samples(tmp_path):
    async def scenario():
        app = make_app(tmp_path)
        async with (
            app.router.lifespan_context(app),
            AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer receipt-test"},
            ) as client,
        ):
            (
                await client.post(
                    "/api/v1/sources/claude-code/authorize",
                    json=await authorized_request(client, "claude-code"),
                )
            ).raise_for_status()
            endpoint = "/api/v1/sources/claude-code/import-receipt"
            old = app.state.evidence_store.put(record(1)).record
            for index in range(2, 7):
                app.state.evidence_store.put(record(index))
            latest = (await client.get(endpoint)).json()
            assert old.evidence_id not in {item["evidence_id"] for item in latest["query_examples"]}
            response = await client.get(endpoint, params={"query_evidence_id": old.evidence_id})
            response.raise_for_status()
            selected = response.json()["query_examples"]
            assert len(selected) == 1
            assert selected[0]["evidence_id"] == old.evidence_id
            assert selected[0]["evidence_revision"] == old.revision
            assert selected[0]["excerpt"] == old.verbatim
            for excluded in (
                record(20, source="other", text="excluded-source"),
                record(21, user="other", text="excluded-user"),
                record(22, author=WrittenBy.AGENT, text="excluded-agent"),
                record(23, text="<user_query>ambiguous"),
            ):
                stored = app.state.evidence_store.put(excluded).record
                result = await client.get(
                    endpoint, params={"query_evidence_id": stored.evidence_id}
                )
                assert result.status_code == 200
                assert result.json()["query_examples"] == []
            assert (await client.get(endpoint, params={"query_evidence_id": ""})).status_code == 422
            (await client.post("/api/v1/sources/claude-code/revoke")).raise_for_status()
            assert (
                await client.get(endpoint, params={"query_evidence_id": old.evidence_id})
            ).status_code == 403

    asyncio.run(scenario())
