"""Core boundary tests using the broker's envelope, never a host's claimed identity.

The Core token below stands in for the trusted broker. A host cannot set these
headers through RecallBroker, which rebuilds them from its credential store.
Task/output rows are deterministic lineage fixtures, not a real HY extraction.
"""

import asyncio
import json
from contextlib import asynccontextmanager
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from starlette.requests import Request

from one_memory.api import create_app
from one_memory.config import Settings
from one_memory.external_memory_access import (
    external_access,
    require_external_endpoint,
    search_external_matter_contexts,
)
from one_memory.hy_lifecycle import HyLifecycle
from one_memory.hy_memory_bridge import build_hy_memory_config
from one_memory.imports.access import opaque_workspace_id
from one_memory.imports.models import EvidenceInput, EvidenceOrigin, OriginMetadata, WrittenBy
from one_memory.imports.sources.registry import RootCandidateKind
from one_memory.matters.models import Citation, Claim, MatterDraft, MatterPatch, MemoryRef
from one_memory.memory_governance import MemoryDraft, MemoryKind
from one_memory.memory_tasks import MemoryTaskKind, MemoryTaskOutput
from one_memory.model_profiles import ModelCapabilities, ModelProfileInput, ProviderKind
from one_memory.secrets import InMemorySecretStore

TOOLS = ("om_recall", "om_ask", "om_conversations", "om_matters")
WORKSPACE = "/fixture/approved-project"
OTHER_WORKSPACE = "/fixture/other-project"
WORKSPACE_SCOPE = opaque_workspace_id("workbuddy", WORKSPACE)
OTHER_SCOPE = opaque_workspace_id("claude-code", OTHER_WORKSPACE)
ALL_SCOPES = ("all-apps", WORKSPACE_SCOPE, OTHER_SCOPE, "source:workbuddy", "source:claude-code")
ROUTES = {
    "om_recall": ("POST", "/api/v1/recall", {"query": "needle", "workspace_path": WORKSPACE}),
    "om_ask": (
        "POST",
        "/api/v1/memories/ask",
        {"question": "needle", "workspace_path": WORKSPACE},
    ),
    "om_conversations": ("GET", "/api/v1/conversations/search", {"query": "needle"}),
    "om_matters": ("POST", "/api/v1/matters/context/search", {"query": "needle"}),
}


class HealthyLifecycle:
    def lifecycle_now(self, _user_id, _memory_ids):
        return HyLifecycle(hidden={})


def envelope(*, tools=TOOLS, scopes=ALL_SCOPES):
    return {
        "x-one-memory-read-context": "external",
        "x-one-memory-installation-id": "24361b44-5a15-4f87-ad09-05571dc281c3",
        "x-one-memory-installation-revision": "2",
        "x-one-memory-allowed-tools": json.dumps(tools),
        "x-one-memory-allowed-scopes": json.dumps(scopes),
    }


def workbuddy_envelope(*, tools=("om_recall",), scopes=("all-apps",)):
    return envelope(tools=tools, scopes=scopes) | {"x-one-memory-host-id": "workbuddy"}


def test_optional_broker_host_identity_requires_complete_envelope():
    assert external_access(envelope()).host_id is None
    assert external_access(envelope() | {"x-one-memory-host-id": "codex"}).host_id == "codex"
    for headers in (
        {"x-one-memory-host-id": "codex"},
        envelope() | {"x-one-memory-host-id": "../private"},
        envelope() | {"x-one-memory-host-id": ""},
    ):
        with pytest.raises(HTTPException):
            external_access(headers)


def test_boundary_http_routes_require_workbuddy_recall_identity(tmp_path):
    async def scenario():
        async with library(tmp_path) as (_, client, *_):
            payload = {"hook_event_name": "SessionStart", "session_id": "missing"}
            allowed = await client.post(
                "/api/v1/boundary/peek", json=payload, headers=workbuddy_envelope()
            )
            assert allowed.status_code == 200
            assert allowed.json() == {}

            for headers in (
                envelope(tools=("om_recall",), scopes=("all-apps",)),
                workbuddy_envelope(tools=("om_ask",)),
                workbuddy_envelope(scopes=("source:workbuddy",)),
                workbuddy_envelope() | {"x-one-memory-host-id": "cursor"},
            ):
                denied = await client.post("/api/v1/boundary/peek", json=payload, headers=headers)
                assert denied.status_code == 403

            invalid = await client.post(
                "/api/v1/boundary/ack",
                json={"token": "not-a-valid-token"},
                headers=workbuddy_envelope(),
            )
            assert invalid.status_code == 422
            assert "not-a-valid-token" not in invalid.text

    asyncio.run(scenario())


async def call_tool(client, tool, headers):
    method, path, body = ROUTES[tool]
    return await client.request(
        method, path, headers=headers, **{"params" if method == "GET" else "json": body}
    )


def response_ids(response, tool):
    assert response.status_code == 200, response.text
    body = response.json()
    if tool == "om_matters":
        return {item["matter_id"] for item in body}
    items = body["memories"] if tool == "om_ask" else body["hits"]
    key = "evidence_id" if tool == "om_conversations" else "memory_id"
    return {item[key] for item in items}


def seed_matter(app, name, record, memory, profile):
    tasks = app.state.memory_task_store
    task = tasks.create(
        request_id=f"fixture-task-{name}",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref=record.evidence_id,
        input_records=(record,),
        # Only the synthetic task snapshot is active; no runtime model is enabled.
        profile=profile.model_copy(update={"is_active": True}),
    )
    _, token = tasks.claim(task.task_id)
    tasks.save_output(
        MemoryTaskOutput(
            task_id=task.task_id,
            memory_id=memory.memory_id,
            memory_revision=memory.revision,
            hy_memory_id=memory.memory_id,
            owner="user",
            layer="L2_FACT",
            engine_version="local-fixture",
        )
    )
    tasks.complete(task.task_id, runner_token=token)
    reference = MemoryRef(
        memory_id=memory.memory_id,
        revision=memory.revision,
        status=memory.status,
        origin=memory.origin,
        content_hash=sha256(memory.content.encode()).hexdigest(),
        task_id=task.task_id,
        hy_memory_id=memory.memory_id,
        engine_version="local-fixture",
        profile_id=profile.profile_id,
        profile_revision=profile.revision,
        model=profile.model,
    )
    matter = app.state.matter_store.save(
        MatterDraft(
            title=f"needle {name}",
            purpose=f"fixture purpose {name}",
            boundary=f"fixture boundary {name}",
            anchors=[record.evidence_id],
            claims=[
                Claim(
                    statement=memory.content,
                    kind="constraint",
                    citations=[Citation(evidence_id=record.evidence_id, quote=record.verbatim)],
                    memory_refs=[reference],
                )
            ],
        ),
        model="local-fixture",
        require_hy=True,
    )
    matter = app.state.matter_store.patch(
        matter.matter_id, MatterPatch(expected_version=matter.version, agent_access=True)
    )
    assert matter.availability == "ready" and matter.source_engine == "hy-memory"
    return matter


@asynccontextmanager
async def library(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    app = create_app(
        Settings(
            data_dir=tmp_path / "data",
            source_home=home,
            api_token=SecretStr("endpoint-fixture"),
            imports_enabled=False,
            automatic_memory_processing=False,
            live_sync_enabled=False,
        ),
        source_catalog={},
        secret_store=InMemorySecretStore(),
    )
    async with (
        app.router.lifespan_context(app),
        AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer endpoint-fixture"},
        ) as client,
    ):
        app.state.matter_store._lifecycle_source = HealthyLifecycle()
        app.state.recall_target.semantic_search = None
        app.state.memory_answerer.semantic_search = None
        profile = app.state.model_profile_store.create(
            ModelProfileInput(
                name="Lineage fixture", provider_kind=ProviderKind.OLLAMA, model="test"
            ),
            app.state.secret_store,
        )
        for source in ("workbuddy", "claude-code"):
            root = home / source
            root.mkdir()
            app.state.source_grant_store.grant(
                user_id="local",
                source_id=source,
                root=root,
                candidate_kind=RootCandidateKind.DEFAULT,
            )
        records, memories, matters = {}, {}, {}
        for name, source, workspace, scope in (
            ("shared", "workbuddy", WORKSPACE, "all-apps"),
            ("workspace", "workbuddy", WORKSPACE, WORKSPACE_SCOPE),
            ("other", "claude-code", OTHER_WORKSPACE, OTHER_SCOPE),
            ("private", "workbuddy", WORKSPACE, "private"),
            ("survivor", "claude-code", OTHER_WORKSPACE, "all-apps"),
        ):
            record = app.state.evidence_store.put(
                EvidenceInput(
                    source_id=source,
                    conversation_id=f"fixture-{name}",
                    message_id="1",
                    workspace=workspace,
                    role="user",
                    written_by=WrittenBy.PERSON,
                    verbatim=f"needle raw-{name}",
                    origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
                )
            ).record
            memory = app.state.person_writes.create(
                MemoryDraft(
                    user_id="local",
                    kind=MemoryKind.CHAT,
                    content=f"needle governed-{name}",
                    scopes=(scope,),
                    evidence_ids=(record.evidence_id,),
                ),
                request_id=f"fixture-{name}",
            )
            records[name], memories[name] = record, memory
            if name in {"shared", "workspace", "other"}:
                matters[name] = seed_matter(app, name, record, memory, profile)
        assert app.state.evidence_store.count() == 5
        assert len(app.state.matter_store.search_contexts("needle")) == 3
        yield app, client, records, memories, matters


@pytest.mark.parametrize("tool", TOOLS)
def test_each_broker_route_has_nonempty_positive_control(tmp_path, tool):
    async def scenario():
        async with library(tmp_path) as (_, client, records, memories, matters):
            response = await call_tool(client, tool, envelope(tools=(tool,)))
            expected = (
                {item.matter_id for item in matters.values()}
                if tool == "om_matters"
                else {item.evidence_id for item in records.values()}
                if tool == "om_conversations"
                else {memories[name].memory_id for name in ("shared", "workspace", "survivor")}
            )
            assert response_ids(response, tool) == expected
            assert (
                "raw-other" in response.text if tool == "om_matters" else "needle" in response.text
            )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "tool,other", [(tool, other) for tool in TOOLS for other in TOOLS if tool != other]
)
def test_core_rejects_cross_tool_envelope(tmp_path, tool, other):
    async def scenario():
        async with library(tmp_path) as (_, client, *_):
            assert response_ids(await call_tool(client, tool, envelope(tools=(tool,))), tool)
            denied = await call_tool(client, tool, envelope(tools=(other,)))
            assert denied.status_code == 403, denied.text
            assert "governed-" not in denied.text and "raw-" not in denied.text

    asyncio.run(scenario())


@pytest.mark.parametrize("tool", TOOLS)
def test_each_broker_route_requires_complete_envelope(tmp_path, tool):
    async def scenario():
        async with library(tmp_path) as (_, client, *_):
            assert response_ids(await call_tool(client, tool, envelope()), tool)
            denied = await call_tool(client, tool, {"x-one-memory-read-context": "external"})
            assert denied.status_code == 403, denied.text
            assert "raw-" not in denied.text and "governed-" not in denied.text

    asyncio.run(scenario())


@pytest.mark.parametrize("tool", TOOLS)
def test_envelope_does_not_replace_core_authentication(tmp_path, tool):
    async def scenario():
        async with library(tmp_path) as (_, client, *_):
            assert response_ids(await call_tool(client, tool, envelope()), tool)
            denied = await call_tool(
                client,
                tool,
                {**envelope(), "Authorization": "Bearer omhg_v1_" + "A" * 43},
            )
            assert denied.status_code == 401, denied.text
            assert "raw-" not in denied.text and "governed-" not in denied.text

    asyncio.run(scenario())


@pytest.mark.parametrize("tool", ("om_recall", "om_ask"))
def test_workspace_expansion_is_intersected_with_installation_grant(tmp_path, tool):
    async def scenario():
        async with library(tmp_path) as (_, client, _, memories, _):
            broad = await call_tool(client, tool, envelope(tools=(tool,)))
            assert memories["workspace"].memory_id in response_ids(broad, tool)
            restricted = await call_tool(
                client, tool, envelope(tools=(tool,), scopes=("all-apps",))
            )
            assert response_ids(restricted, tool) == {
                memories[name].memory_id for name in ("shared", "survivor")
            }
            assert "governed-private" not in restricted.text
            assert "governed-workspace" not in restricted.text
            assert "governed-other" not in restricted.text

    asyncio.run(scenario())


@pytest.mark.parametrize("marker", (None, "internal", "External", ""))
def test_partial_envelope_cannot_downgrade_to_internal_reader(tmp_path, marker):
    async def scenario():
        async with library(tmp_path) as (_, client, _, memories, _):
            body = {"query": "needle", "scope": "*"}
            local = await client.post("/api/v1/recall", json=body)
            assert memories["private"].memory_id in response_ids(local, "om_recall")
            headers = envelope(scopes=("all-apps",), tools=("om_recall",))
            if marker is None:
                del headers["x-one-memory-read-context"]
            else:
                headers["x-one-memory-read-context"] = marker
            denied = await client.post("/api/v1/recall", json=body, headers=headers)
            assert denied.status_code == 403, denied.text
            assert "governed-private" not in denied.text

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "header,value",
    (
        ("x-one-memory-read-context", "internal"),
        ("x-one-memory-allowed-scopes", '["all-apps"]'),
        ("x-one-memory-allowed-tools", '["om_ask"]'),
        ("x-one-memory-installation-revision", "3"),
    ),
)
def test_duplicate_envelope_headers_are_rejected(tmp_path, header, value):
    async def scenario():
        async with library(tmp_path) as (_, client, *_):
            assert response_ids(await call_tool(client, "om_recall", envelope()), "om_recall")
            headers = [*envelope().items(), (header, value)]
            denied = await call_tool(client, "om_recall", headers)
            assert denied.status_code == 403, denied.text

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "field,value",
    (
        ("installation-revision", "01"),
        ("installation-revision", "+2"),
        ("installation-revision", "9007199254740992"),
        ("allowed-scopes", '["all-apps","all-apps"]'),
        ("allowed-scopes", '["*"]'),
        ("allowed-scopes", "[]"),
        ("allowed-scopes", "[" * 1200 + "]" * 1200),
        ("allowed-tools", '["om_recall","om_recall"]'),
        ("allowed-tools", '["unrecognized"]'),
        ("allowed-tools", "[]"),
    ),
)
def test_non_broker_envelope_values_fail_closed(tmp_path, field, value):
    async def scenario():
        async with library(tmp_path) as (_, client, *_):
            assert response_ids(await call_tool(client, "om_recall", envelope()), "om_recall")
            denied = await call_tool(
                client, "om_recall", {**envelope(), f"x-one-memory-{field}": value}
            )
            assert denied.status_code == 403, denied.text

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "scopes,expected",
    (
        (("all-apps", "source:workbuddy"), {"shared"}),
        (("all-apps", WORKSPACE_SCOPE, "source:workbuddy"), {"shared", "workspace"}),
        (("all-apps", WORKSPACE_SCOPE, OTHER_SCOPE, "source:workbuddy"), {"shared", "workspace"}),
    ),
)
def test_matter_context_intersects_lineage_scopes_and_raw_quote_sources(tmp_path, scopes, expected):
    async def scenario():
        async with library(tmp_path) as (_, client, _, _, matters):
            broad = await call_tool(client, "om_matters", envelope())
            assert len(response_ids(broad, "om_matters")) == 3
            result = await call_tool(client, "om_matters", envelope(scopes=scopes))
            assert response_ids(result, "om_matters") == {
                matters[name].matter_id for name in expected
            }
            assert "raw-other" not in result.text and "governed-other" not in result.text

    asyncio.run(scenario())


def test_matter_cannot_republish_a_private_governed_memory(tmp_path):
    async def scenario():
        async with library(tmp_path) as (app, client, records, memories, _):
            profile = next(
                item
                for item in app.state.model_profile_store.list(app.state.secret_store)
                if item.name == "Lineage fixture"
            )
            private = seed_matter(app, "private", records["private"], memories["private"], profile)
            body = {"query": "needle private"}
            local = await client.post("/api/v1/matters/context/search", json=body)
            assert response_ids(local, "om_matters") == {private.matter_id}
            denied = await client.post(
                "/api/v1/matters/context/search", json=body, headers=envelope()
            )
            assert response_ids(denied, "om_matters") == set()
            assert "governed-private" not in denied.text

    asyncio.run(scenario())


@pytest.mark.parametrize("tool", ("om_conversations", "om_matters"))
def test_raw_quotes_require_source_grant_separate_from_tool_and_all_apps(tmp_path, tool):
    async def scenario():
        async with library(tmp_path) as (_, client, *_):
            assert response_ids(await call_tool(client, tool, envelope()), tool)
            denied = await call_tool(client, tool, envelope(scopes=("all-apps",)))
            assert denied.status_code == 403, denied.text
            assert "raw-" not in denied.text

    asyncio.run(scenario())


def test_raw_search_filters_sources_before_limit_and_body_return(tmp_path):
    async def scenario():
        async with library(tmp_path) as (_, client, records, _, _):
            all_hits = response_ids(
                await call_tool(client, "om_conversations", envelope()), "om_conversations"
            )
            assert all_hits == {record.evidence_id for record in records.values()}
            scoped = envelope(tools=("om_conversations",), scopes=("all-apps", "source:workbuddy"))
            found = await client.get(
                "/api/v1/conversations/search",
                params={"query": "needle", "limit": 1},
                headers=scoped,
            )
            assert len(found.json()["hits"]) == 1
            assert found.json()["hits"][0]["source_id"] == "workbuddy"
            assert found.json()["imported_messages"] == 3
            assert "raw-other" not in found.text and "raw-survivor" not in found.text

    asyncio.run(scenario())


@pytest.mark.parametrize("route", ("conversation", "memory", "memory-list", "matter-context"))
def test_core_only_detail_and_library_reads_reject_external_envelopes(tmp_path, route):
    async def scenario():
        async with library(tmp_path) as (_, client, records, memories, matters):
            paths = {
                "conversation": f"/api/v1/conversations/{records['other'].evidence_id}",
                "memory": f"/api/v1/memories/{memories['other'].memory_id}",
                "memory-list": "/api/v1/memories",
                "matter-context": f"/api/v1/matters/{matters['other'].matter_id}/context",
            }
            local = await client.get(paths[route])
            assert local.status_code == 200 and "other" in local.text
            external = await client.get(
                paths[route], headers=envelope(scopes=("all-apps", "source:workbuddy"))
            )
            assert external.status_code == 403, external.text
            assert "raw-other" not in external.text and "governed-other" not in external.text

    asyncio.run(scenario())


def test_delivery_endpoint_accepts_only_a_recall_grant_and_returns_no_read_data(tmp_path):
    async def scenario():
        async with library(tmp_path) as (app, client, *_):
            event_response = await client.post(
                "/api/v1/context/search",
                json={"query": "needle", "kinds": ["raw", "memory"]},
            )
            assert event_response.status_code == 200, event_response.text
            event_id = event_response.json()["request_id"]
            body = {
                "delivery": "delivered",
                "host_session_id": "session-1",
                "injection_elapsed_ms": 42,
            }
            allowed = await client.post(
                f"/api/v1/call-events/{event_id}/delivery",
                json=body,
                headers=envelope(tools=("om_recall",)),
            )
            assert allowed.status_code == 200, allowed.text
            assert allowed.json() == {"delivery": "delivered", "adoption": "unknown"}

            denied = await client.post(
                f"/api/v1/call-events/{uuid4()}/delivery",
                json=body,
                headers=envelope(tools=("om_ask",)),
            )
            assert denied.status_code == 403, denied.text
            assert denied.json() == {"detail": "external_recall_scope_denied"}

    asyncio.run(scenario())


def test_sdk_context_v2_accepts_the_same_composed_external_grants(tmp_path):
    async def scenario():
        async with library(tmp_path) as (_, client, *_):
            response = await client.post(
                "/api/v2/context/search",
                json={"query": "needle", "kinds": ["raw", "memory"]},
                headers=envelope(
                    tools=("om_recall", "om_conversations"),
                    scopes=("all-apps", "source:workbuddy"),
                ),
            )
            assert response.status_code == 503, response.text
            assert response.json()["detail"] == "sdk_context_unavailable"

    asyncio.run(scenario())


def test_sdk_ask_v2_is_an_explicit_external_om_ask_route(tmp_path):
    async def scenario():
        async with library(tmp_path) as (_, client, *_):
            response = await client.post(
                "/api/v2/memories/ask",
                json={"question": "needle"},
                headers=envelope(
                    tools=("om_ask",),
                    scopes=("all-apps",),
                ),
            )
            assert response.status_code == 409, response.text
            assert response.json()["detail"] == "legacy_memory_authority_required"

    asyncio.run(scenario())


@pytest.mark.parametrize("tool", TOOLS)
def test_source_revocation_keeps_surviving_data_and_blocks_revoked_bodies(tmp_path, tool):
    async def scenario():
        async with library(tmp_path) as (app, client, _, _, _):
            before = await call_tool(client, tool, envelope())
            assert response_ids(before, tool) and "shared" in before.text
            app.state.source_grant_store.revoke(user_id="local", source_id="workbuddy")
            after = await call_tool(client, tool, envelope())
            assert response_ids(after, tool)
            assert "governed-shared" not in after.text and "raw-shared" not in after.text
            assert "governed-workspace" not in after.text and "raw-workspace" not in after.text
            assert app.state.evidence_store.count() == 5

    asyncio.run(scenario())


def test_raw_search_revocation_hides_counts_as_well_as_bodies(tmp_path):
    async def scenario():
        async with library(tmp_path) as (app, client, *_):
            headers = envelope(tools=("om_conversations",))
            before = await call_tool(client, "om_conversations", headers)
            assert len(response_ids(before, "om_conversations")) == 5
            assert before.json()["imported_messages"] == 5
            app.state.source_grant_store.revoke(user_id="local", source_id="workbuddy")
            after = await call_tool(client, "om_conversations", headers)
            assert len(response_ids(after, "om_conversations")) == 2
            assert after.json()["imported_messages"] == 2

    asyncio.run(scenario())


def test_all_revoked_raw_grants_do_not_query_or_fall_back_to_all_sources(tmp_path):
    async def scenario():
        async with library(tmp_path) as (app, client, *_):
            headers = envelope(tools=("om_conversations",), scopes=("all-apps", "source:workbuddy"))
            before = await call_tool(client, "om_conversations", headers)
            assert len(response_ids(before, "om_conversations")) == 3
            app.state.source_grant_store.revoke(user_id="local", source_id="workbuddy")
            with patch.object(
                app.state.evidence_store,
                "search_conversations",
                side_effect=AssertionError("all revoked must not load raw content"),
            ):
                after = await call_tool(client, "om_conversations", headers)
            assert response_ids(after, "om_conversations") == set()
            assert after.json()["imported_messages"] == 0
            assert app.state.evidence_store.count() == 5

    asyncio.run(scenario())


@pytest.mark.parametrize("revoked", (False, True))
def test_matter_checks_all_support_of_a_memory_not_only_its_visible_quote(tmp_path, revoked):
    async def scenario():
        async with library(tmp_path) as (app, client, records, _, _):
            mixed = app.state.person_writes.create(
                MemoryDraft(
                    user_id="local",
                    kind=MemoryKind.CHAT,
                    content="needle mixed allowed and restricted conclusions",
                    scopes=("all-apps",),
                    evidence_ids=(records["shared"].evidence_id, records["other"].evidence_id),
                ),
                request_id="mixed-memory",
            )
            profile = next(
                item
                for item in app.state.model_profile_store.list(app.state.secret_store)
                if item.name == "Lineage fixture"
            )
            matter = seed_matter(app, "mixed", records["shared"], mixed, profile)
            body = {"query": "needle mixed"}
            before = await client.post(
                "/api/v1/matters/context/search", json=body, headers=envelope()
            )
            assert response_ids(before, "om_matters") == {matter.matter_id}
            assert "restricted conclusions" in before.text
            if revoked:
                app.state.source_grant_store.revoke(user_id="local", source_id="claude-code")
                headers = envelope()
            else:
                headers = envelope(scopes=("all-apps", "source:workbuddy"))
            after = await client.post("/api/v1/matters/context/search", json=body, headers=headers)
            assert response_ids(after, "om_matters") == set()
            assert "restricted conclusions" not in after.text

    asyncio.run(scenario())


@pytest.mark.parametrize("tool", TOOLS)
def test_source_revocation_survives_core_restart(tmp_path, tool):
    async def scenario():
        async with library(tmp_path) as (app, client, *_):
            before = await call_tool(client, tool, envelope())
            assert response_ids(before, tool) and "shared" in before.text
            app.state.source_grant_store.revoke(user_id="local", source_id="workbuddy")
            settings = app.state.settings
        reopened = create_app(settings, source_catalog={}, secret_store=InMemorySecretStore())
        async with (
            reopened.router.lifespan_context(reopened),
            AsyncClient(
                transport=ASGITransport(app=reopened),
                base_url="http://test",
                headers={"Authorization": "Bearer endpoint-fixture"},
            ) as client,
        ):
            reopened.state.matter_store._lifecycle_source = HealthyLifecycle()
            reopened.state.recall_target.semantic_search = None
            reopened.state.memory_answerer.semantic_search = None
            after = await call_tool(client, tool, envelope())
            assert response_ids(after, tool)
            assert "raw-shared" not in after.text and "governed-shared" not in after.text
            assert reopened.state.evidence_store.count() == 5

    asyncio.run(scenario())


def test_graph_export_is_internal_only_and_positive_control_copies_real_data(tmp_path):
    async def scenario():
        async with library(tmp_path) as (app, client, *_):
            kuzu = pytest.importorskip("kuzu")
            profiles, secrets = app.state.model_profile_store, app.state.secret_store
            profile = profiles.create(
                ModelProfileInput(
                    name="Offline graph fixture",
                    provider_kind=ProviderKind.OLLAMA,
                    model="test",
                    embedding_provider_kind=ProviderKind.LOCAL_ONNX,
                    embedding_model="bge-small-zh-v1.5",
                    embedding_dimensions=512,
                ),
                secrets,
            )
            profiles.record_capabilities(
                profile.profile_id,
                expected_revision=profile.revision,
                capabilities=ModelCapabilities(
                    endpoint_reachable=True, model_available=True, embedding_available=True
                ),
                secrets=secrets,
            )
            profile = profiles.activate(profile.profile_id, secrets)
            config = build_hy_memory_config(
                profiles.snapshot(profile.profile_id, profile.revision),
                api_key=None,
                data_dir=tmp_path / "data",
            )
            graph_path = Path(config.graph_store.db_path)
            graph_path.parent.mkdir(parents=True, exist_ok=True)
            database = kuzu.Database(str(graph_path))
            connection = kuzu.Connection(database)
            connection.execute(
                "CREATE NODE TABLE Memory(node_id STRING, embedding FLOAT[768], "
                "PRIMARY KEY(node_id))"
            )
            connection.execute("CREATE (:Memory {node_id: 'fixture-private-export'})")
            connection.close()
            database.close()
            local = await client.post("/api/v1/graph-width", json={"decision": "export_first"})
            assert local.status_code == 200, local.text
            exported = Path(local.json()["exported_path"]) / graph_path.name
            assert exported.stat().st_size > 0
            assert exported.read_bytes() == graph_path.read_bytes()
            before = set(tmp_path.glob("one-memory-graph-*"))
            assert len(before) == 1
            denied = await client.post(
                "/api/v1/graph-width",
                json={"decision": "export_first"},
                headers=envelope(),
            )
            assert denied.status_code == 403, denied.text
            assert set(tmp_path.glob("one-memory-graph-*")) == before

    asyncio.run(scenario())


def test_matter_helper_filters_before_render_and_limit(tmp_path):
    async def scenario():
        async with library(tmp_path) as (app, _, _, _, matters):
            access = external_access(envelope(scopes=("all-apps", "source:workbuddy")))
            original = app.state.matter_store.context
            with patch.object(app.state.matter_store, "context", wraps=original) as render:
                contexts = search_external_matter_contexts(
                    access,
                    "needle",
                    matters=app.state.matter_store,
                    governance=app.state.memory_governance_store,
                )
            assert [context.matter_id for context in contexts] == [matters["shared"].matter_id]
            assert render.call_count == 1
            assert render.call_args.args == (matters["shared"].matter_id,)
            assert "raw-shared" in contexts[0].text

    asyncio.run(scenario())


def test_matter_helper_rejects_uncited_dependency_outside_source_grant(tmp_path):
    async def scenario():
        async with library(tmp_path) as (app, _, records, _, matters):
            store = app.state.matter_store
            current = matters["shared"]
            revised = store.save(
                MatterDraft(
                    title=current.title,
                    purpose=current.purpose,
                    boundary=current.boundary,
                    anchors=[*current.anchors, records["other"].evidence_id],
                    claims=current.claims,
                ),
                model=current.model,
                matter_id=current.matter_id,
                expected_version=current.version,
                require_hy=True,
            )
            broad = external_access(envelope())
            scoped = external_access(envelope(scopes=("all-apps", "source:workbuddy")))
            assert broad.allows_matter(revised, app.state.memory_governance_store)
            assert not scoped.allows_matter(revised, app.state.memory_governance_store)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "revoke_after,source,survivors",
    (
        ("shared", "workbuddy", {"other"}),
        ("shared", "claude-code", {"shared", "workspace"}),
    ),
)
def test_matter_helper_rechecks_source_revocation_after_render(
    tmp_path, revoke_after, source, survivors
):
    async def scenario():
        async with library(tmp_path) as (app, _, _, _, matters):
            store = app.state.matter_store
            access = external_access(envelope())
            before = search_external_matter_contexts(
                access, "needle", matters=store, governance=app.state.memory_governance_store
            )
            assert {item.matter_id for item in before} == {
                item.matter_id for item in matters.values()
            }
            original = store.context

            def revoke_after_render(*args, **kwargs):
                result = original(*args, **kwargs)
                if args[0] == matters[revoke_after].matter_id:
                    app.state.source_grant_store.revoke(user_id="local", source_id=source)
                return result

            with patch.object(store, "context", side_effect=revoke_after_render):
                after = search_external_matter_contexts(
                    access, "needle", matters=store, governance=app.state.memory_governance_store
                )
            assert {item.matter_id for item in after} == {
                matters[name].matter_id for name in survivors
            }

    asyncio.run(scenario())


def test_source_helper_rechecks_revocation_without_caching_the_envelope(tmp_path):
    async def scenario():
        async with library(tmp_path) as (app, *_):
            access = external_access(envelope())
            database = app.state.evidence_store.database_path
            assert access.readable_source_ids(database) == {"workbuddy", "claude-code"}
            app.state.source_grant_store.revoke(user_id="local", source_id="workbuddy")
            assert access.readable_source_ids(database) == {"claude-code"}
            app.state.source_grant_store.revoke(user_id="local", source_id="claude-code")
            assert access.readable_source_ids(database) == set()

    asyncio.run(scenario())


@pytest.mark.parametrize("tool", TOOLS)
def test_route_guard_helper_matches_broker_and_rejects_cross_tool(tool):
    method, path, _ = ROUTES[tool]

    def request(headers, *, route=path):
        return Request(
            {
                "type": "http",
                "method": method,
                "path": route,
                "headers": [(key.encode(), value.encode()) for key, value in headers.items()],
            }
        )

    require_external_endpoint(request({}))
    require_external_endpoint(request(envelope(tools=(tool,))))
    for other in TOOLS:
        if other != tool:
            with pytest.raises(HTTPException) as denied:
                require_external_endpoint(request(envelope(tools=(other,))))
            assert denied.value.status_code == 403
    with pytest.raises(HTTPException) as denied:
        require_external_endpoint(request(envelope(), route="/api/v1/graph-width"))
    assert denied.value.status_code == 403
