from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from one_memory.api import create_app
from one_memory.config import Settings
from one_memory.hy_sdk_types import HySdkReadError
from one_memory.ui_projection import (
    HOME_PROJECTION_CANDIDATES,
    project_raw_latest,
    raw_user_excerpt,
    read_home_projection,
)


def raw_row(
    raw_id: str,
    content: str,
    *,
    status: str = "active",
    memory_at: int | None = 1_700_000_000,
    created: int | None = 1_699_000_000,
) -> dict[str, object]:
    return {
        "memory_id": raw_id,
        "content": content,
        "layer": "l1_raw",
        "status": status,
        "user_id": "local",
        "agent_id": "all-apps",
        "session_id": f"session-{raw_id}",
        "source": "cursor",
        "memory_at": memory_at,
        "gmt_created": created,
        "custom": {"private": "not projected"},
    }


def chat_row(memory_id: str = "m-1") -> dict[str, object]:
    return {
        "memory_id": memory_id,
        "content": "The user ships on Friday",
        "layer": "l2_fact",
        "status": "active",
        "is_latest": True,
        "user_id": "local",
        "agent_id": "all-apps",
        "session_id": "s-1",
        "source": "cursor",
        "memory_at": 1_700_000_000,
        "gmt_created": 1_699_000_000,
        "updated_at": 1_800_000_000,
        "scene": "chat",
        "kind": "chat",
    }


class ProjectionSdk:
    def __init__(self, rows: list[dict[str, object]], *, total: int | None = None) -> None:
        self.rows = rows
        self.total = len(rows) if total is None else total
        self.calls: list[dict[str, object]] = []

    def list_memories(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("layers") == ["l1_raw"]:
            return {"vdb": {"memories": self.rows, "total": self.total}}
        return {
            "scene": "all",
            "memories": self.rows,
            "total": self.total,
            "totals": {"chat": len(self.rows), "working": 0, "coding": 0},
            "errors": {},
        }

    def memory_stats(self, **kwargs):
        raise AssertionError("home projection must use one scene=all snapshot")


def test_raw_excerpt_keeps_only_user_query_and_truncates_by_codepoint() -> None:
    text = (
        "[user]: <system_reminder>ignore this</system_reminder>\n"
        "<user_info>private host data</user_info>\n"
        "<user_query>短句 https://example.com/a /tmp/x " + "🙂" * 220 + "</user_query>\n"
        "[assistant]: secret answer"
    )
    excerpt, truncated = raw_user_excerpt(text)
    assert excerpt.startswith("短句 https://example.com/a /tmp/x ")
    assert "ignore" not in excerpt and "private" not in excerpt and "secret" not in excerpt
    assert len(excerpt) == 200
    assert truncated is True


def test_raw_excerpt_fails_closed_on_nested_host_wrappers() -> None:
    assert raw_user_excerpt(
        "[user]: <system_reminder><user_query>keep me</user_query></system_reminder>"
    ) == ("", False)


@pytest.mark.parametrize(
    "wrapper",
    [
        "system_reminder",
        "system-reminder",
        "user_info",
        "attached_files",
        "image_local_path",
        "agent_transcripts",
        "rules",
        "additional_data",
        "working_memory_reminder",
    ],
)
def test_raw_excerpt_uses_shared_envelope_parser_for_host_context(wrapper) -> None:
    excerpt, truncated = raw_user_excerpt(
        f"[user]: <{wrapper}>private</{wrapper}><user_query>keep me</user_query>"
    )
    assert (excerpt, truncated) == ("keep me", False)


@pytest.mark.parametrize(
    "text",
    [
        "[user]: <system_reminder>unclosed <user_query>do not guess</user_query>",
        "[user]: <user_query>unclosed",
        "[user]: <rules><rules>nested</rules><user_query>ambiguous</user_query></rules>",
    ],
)
def test_raw_excerpt_fails_closed_on_ambiguous_envelopes(text) -> None:
    assert raw_user_excerpt(text) == ("", False)


def test_raw_projection_uses_shadow_rows_and_k10_without_leaking_payload() -> None:
    sdk = ProjectionSdk(
        [
            raw_row("r-1", "[assistant]: no user here", status="shadow"),
            raw_row("r-2", "[user]: deploy Friday", status="active"),
        ],
        total=42,
    )
    result = asyncio.run(read_home_projection(sdk, user_id="local", mode="lite"))
    assert result.visible_count == 42
    assert result.latest is not None and result.latest.type == "raw"
    assert result.latest.raw_id == "r-2"
    assert result.latest.excerpt == "deploy Friday"
    assert result.latest.time_basis == "conversation"
    assert result.display_state == "excerpt"
    assert sdk.calls == [
        {
            "user_id": "local",
            "layers": ["l1_raw"],
            "statuses": ["active", "shadow"],
            "limit": HOME_PROJECTION_CANDIDATES,
            "offset": 0,
            "order": "desc",
        }
    ]
    assert set(result.latest.model_dump()) == {
        "type",
        "raw_id",
        "session_id",
        "display_state",
        "time",
        "time_basis",
        "excerpt",
        "truncated",
        "source_id",
    }
    serialized = result.model_dump(mode="json")
    assert serialized["projection_version"] == 1
    assert serialized["latest"]["time"].endswith("Z")
    assert "content" not in serialized["latest"]


def test_raw_projection_reports_no_user_excerpt_when_k_has_none() -> None:
    sdk = ProjectionSdk([raw_row("r-1", "[assistant]: answer")])
    result = asyncio.run(read_home_projection(sdk, user_id="local", mode="lite"))
    assert result.latest is not None and result.latest.type == "raw"
    assert result.latest.raw_id == "r-1"
    assert result.latest.display_state == "no_user_excerpt"
    assert result.latest.excerpt == ""


@pytest.mark.parametrize(
    ("mode", "graph", "evolve"),
    [("pro", False, False), ("ultra", True, True)],
)
def test_memory_projection_covers_pro_and_ultra(mode, graph, evolve) -> None:
    sdk = ProjectionSdk([chat_row()], total=9)
    result = asyncio.run(read_home_projection(sdk, user_id="local", mode=mode))
    assert result.visible_count == 9
    assert result.display_state == "excerpt"
    assert result.capabilities.content_plane == "memory"
    assert result.capabilities.graph_enabled is graph
    assert result.capabilities.evolve_available is evolve
    assert result.latest is not None and result.latest.type == "memory"
    assert result.latest.memory_id == "m-1"
    assert result.latest.time_basis == "update"
    assert result.latest.time.isoformat() == "2027-01-15T08:00:00+00:00"


def test_chat_projection_prefers_gmt_modified_when_the_sdk_supplies_it() -> None:
    row = {**chat_row(), "gmt_modified": 1_810_000_000}
    result = asyncio.run(read_home_projection(ProjectionSdk([row]), user_id="local", mode="pro"))
    assert result.latest is not None
    assert result.latest.time_basis == "update"
    assert result.latest.time.timestamp() == 1_810_000_000


def test_memory_projection_uses_productivity_updated_at() -> None:
    row = {
        "kind": "experience",
        "experience_id": "e-1",
        "task": "Release",
        "solution": "Run tests",
        "scene": "working",
        "status": "active",
        "user_id": "local",
        "agent_id": "all-apps",
        "source": "manual",
        "memory_at": 1_600_000_000,
        "created_at": 1_700_000_000,
        "updated_at": 1_710_000_000,
    }
    result = asyncio.run(read_home_projection(ProjectionSdk([row]), user_id="local", mode="pro"))
    assert result.latest is not None and result.latest.type == "memory"
    assert (result.latest.scene, result.latest.kind, result.latest.variant) == (
        "working",
        "experience",
        "working",
    )
    assert result.latest.time_basis == "update"
    assert result.latest.time.isoformat() == "2024-03-09T16:00:00+00:00"


@pytest.mark.parametrize(
    "row",
    [
        {**chat_row(), "updated_at": None},
        {
            "kind": "experience",
            "experience_id": "e-1",
            "task": "Release",
            "solution": "Run tests",
            "scene": "coding",
            "status": "active",
            "user_id": "local",
            "agent_id": "all-apps",
            "source": "manual",
            "created_at": 1_700_000_000,
            "updated_at": None,
        },
    ],
)
def test_memory_projection_sort_time_falls_back_to_scene_creation(row) -> None:
    result = asyncio.run(read_home_projection(ProjectionSdk([row]), user_id="local", mode="pro"))
    assert result.latest is not None
    assert result.latest.time_basis == "write"
    expected = row.get("gmt_created") or row.get("created_at")
    assert result.latest.time.timestamp() == expected


def test_fact_is_not_a_home_scene_all_item() -> None:
    row = {
        "kind": "fact",
        "fact_id": "f-1",
        "statement": "Uses pytest",
        "scene": "coding",
        "status": "active",
        "user_id": "local",
        "agent_id": "all-apps",
        "source": "manual",
        "created_at": 1_700_000_000,
    }
    result = asyncio.run(read_home_projection(ProjectionSdk([row]), user_id="local", mode="pro"))
    assert result.visible_count == 1
    assert result.latest is None
    assert result.display_state == "no_displayable_memory"


def test_invalid_latest_uses_next_parseable_candidate_from_same_snapshot() -> None:
    sdk = ProjectionSdk(
        [
            {"memory_id": "broken", "content": "", "scene": "chat", "kind": "chat"},
            chat_row("m-2"),
        ],
        total=2,
    )
    result = asyncio.run(read_home_projection(sdk, user_id="local", mode="pro"))
    assert result.visible_count == 2
    assert result.latest is not None and result.latest.memory_id == "m-2"
    assert len(sdk.calls) == 1


def test_scene_all_rejects_boolean_total() -> None:
    sdk = ProjectionSdk([chat_row()])
    sdk.total = True  # type: ignore[assignment]
    with pytest.raises(HySdkReadError):
        asyncio.run(read_home_projection(sdk, user_id="local", mode="pro"))


def test_projection_read_failure_is_not_converted_to_an_empty_projection() -> None:
    class Broken:
        def list_memories(self, **kwargs):
            raise OSError("down")

    with pytest.raises(OSError, match="down"):
        asyncio.run(read_home_projection(Broken(), user_id="local", mode="lite"))


def test_projection_refuses_a_client_built_for_another_mode() -> None:
    sdk = ProjectionSdk([chat_row()])
    sdk.mode = "lite"
    with pytest.raises(RuntimeError, match="home_projection_mode_mismatch"):
        asyncio.run(read_home_projection(sdk, user_id="local", mode="pro"))


def test_home_projection_route_maps_whole_read_failure_to_503(tmp_path, monkeypatch) -> None:
    class Broken:
        def list_memories(self, **kwargs):
            raise OSError("down")

    class Handle:
        async def client(self):
            return Broken()

    app = create_app(
        Settings(
            _env_file=None,
            data_dir=tmp_path / "data",
            source_home=tmp_path,
            api_token=SecretStr("test"),
            imports_enabled=False,
            automatic_memory_processing=False,
            live_sync_enabled=False,
        ),
        source_catalog={},
    )
    monkeypatch.setattr("one_memory.api._sdk_client_handle", lambda _app: Handle())
    with TestClient(app, headers={"Authorization": "Bearer test"}) as client:
        response = client.get("/api/v2/home-projection")
    assert response.status_code == 503
    assert response.json() == {"detail": "home_projection_read_failed"}


def test_home_projection_reads_mode_once_and_matches_the_client(tmp_path, monkeypatch) -> None:
    sdk = ProjectionSdk([chat_row()])
    sdk.mode = "pro"

    class Handle:
        async def client(self):
            return sdk

    app = create_app(
        Settings(
            _env_file=None,
            data_dir=tmp_path / "data",
            source_home=tmp_path,
            api_token=SecretStr("test"),
            imports_enabled=False,
            automatic_memory_processing=False,
            live_sync_enabled=False,
        ),
        source_catalog={},
    )
    reads = 0

    def mode(_app):
        nonlocal reads
        reads += 1
        return "pro"

    with TestClient(app, headers={"Authorization": "Bearer test"}) as client:
        monkeypatch.setattr("one_memory.api._memory_mode", mode)
        monkeypatch.setattr("one_memory.api._sdk_client_handle", lambda _app: Handle())
        response = client.get("/api/v2/home-projection")
    assert response.status_code == 200, response.text
    assert response.json()["effective_mode"] == "pro"
    assert reads == 1


def test_project_raw_latest_uses_import_time_without_conversation_time() -> None:
    from one_memory.hy_sdk_types import HySdkRawRecord

    record = HySdkRawRecord.from_sdk(
        {
            **raw_row("r-1", "[user]: hello", memory_at=None),
            "raw_id": "r-1",
        }
    )
    latest = project_raw_latest((record,))
    assert latest is not None
    assert latest.time_basis == "import"
