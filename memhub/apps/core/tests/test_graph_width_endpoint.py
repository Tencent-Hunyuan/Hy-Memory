"""The width mismatch has to reach the interface, not just the log.

A-33 was written as "the UI is missing". It is worse than that on the machine
this ships to: the live library is 768, the built-in model is 512, so on a
fresh install extraction fails with `graph_width_mismatch:...` forever while
the app looks fine. Nothing outside `apps/core` catches that error today, so
the endpoint below is what makes the choice reachable at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from one_memory.api import create_app
from one_memory.config import Settings
from one_memory.hy_sdk_runtime import build_sdk_config
from one_memory.model_profiles import (
    ModelCapabilities,
    ModelProfileInput,
    ProviderKind,
)
from tests.graph_fixtures import kuzu_graph_with_embedding_width


@pytest.fixture(autouse=True)
def _enable_graph_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ONE_MEMORY_GRAPH_STORE", "kuzu")


def _app(tmp_path: Path):
    return create_app(
        Settings(
            data_dir=tmp_path / "data",
            source_home=tmp_path,
            instance_id="test-instance",
            api_token=SecretStr("local-token"),
        )
    )


def _client(app):
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={
            "Authorization": "Bearer local-token",
            "Origin": "http://127.0.0.1:5173",
        },
    )


def _activate_builtin_profile(app):
    profiles = app.state.model_profile_store
    secrets = app.state.secret_store
    profile = profiles.create(
        ModelProfileInput(
            name="Local",
            provider_kind=ProviderKind.OLLAMA,
            base_url="http://127.0.0.1:11434",
            model="hy-bot-phi4",
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
            endpoint_reachable=True,
            model_available=True,
            structured_output=True,
            embedding_available=True,
        ),
        secrets=secrets,
    )
    active = profiles.activate(profile.profile_id, secrets)
    return profiles.snapshot(active.profile_id, active.revision)


def _plant_wide_graph(app, data_dir: Path) -> Path:
    snapshot = _activate_builtin_profile(app)
    config = build_sdk_config(snapshot, api_key=None, data_dir=data_dir)
    db_path = Path(config.graph_store.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    kuzu_graph_with_embedding_width(db_path, 768)
    return db_path


@pytest.mark.anyio
async def test_the_app_can_read_the_width_mismatch(tmp_path: Path) -> None:
    app = _app(tmp_path)
    async with app.router.lifespan_context(app), _client(app) as client:
        db_path = _plant_wide_graph(app, tmp_path / "data")

        response = await client.get("/api/v1/graph-width")

        assert response.status_code == 200
        body = response.json()
        assert body["mismatch"] is not None
        assert body["mismatch"]["stored_width"] == 768
        assert body["mismatch"]["expected_width"] == 512
        # Reading must never be a write: this endpoint is polled.
        assert db_path.exists()
        assert list(db_path.parent.glob("*.bak")) == []


@pytest.mark.anyio
async def test_keeping_the_existing_graph_archives_nothing(tmp_path: Path) -> None:
    app = _app(tmp_path)
    async with app.router.lifespan_context(app), _client(app) as client:
        db_path = _plant_wide_graph(app, tmp_path / "data")

        response = await client.post(
            "/api/v1/graph-width",
            json={"decision": "keep_existing"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["archived_path"] is None
        assert body["exported_path"] is None
        assert db_path.exists()
        assert list(db_path.parent.glob("*.bak")) == []


@pytest.mark.anyio
async def test_rebuilding_archives_only_when_asked_for_by_name(tmp_path: Path) -> None:
    app = _app(tmp_path)
    async with app.router.lifespan_context(app), _client(app) as client:
        db_path = _plant_wide_graph(app, tmp_path / "data")

        response = await client.post(
            "/api/v1/graph-width",
            json={"decision": "rebuild_with_new"},
        )

        assert response.status_code == 200
        archived = response.json()["archived_path"]
        assert archived is not None
        assert "dim768" in archived
        assert not db_path.exists()


@pytest.mark.anyio
async def test_export_first_hands_back_a_real_path_and_keeps_asking(
    tmp_path: Path,
) -> None:
    """The app has to be able to show where the copy went, and ask again.

    Exporting settles nothing about the graph, so a receipt that reported no
    mismatch would close the prompt and strand the user one step short of the
    decision they were preparing for.
    """
    app = _app(tmp_path)
    async with app.router.lifespan_context(app), _client(app) as client:
        db_path = _plant_wide_graph(app, tmp_path / "data")

        response = await client.post(
            "/api/v1/graph-width",
            json={"decision": "export_first"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["archived_path"] is None
        exported = Path(body["exported_path"])
        assert (exported / db_path.name).is_file()
        # Beside the data directory, not inside it: a rebuild rewrites inside.
        assert (tmp_path / "data") not in exported.parents
        assert body["mismatch"] is not None
        assert db_path.exists()


@pytest.mark.anyio
async def test_a_matching_install_reports_no_mismatch(tmp_path: Path) -> None:
    """Proves the probe above is measuring something, not always saying yes."""
    app = _app(tmp_path)
    async with app.router.lifespan_context(app), _client(app) as client:
        _activate_builtin_profile(app)

        response = await client.get("/api/v1/graph-width")

        assert response.status_code == 200
        assert response.json()["mismatch"] is None
