"""The SDK retrieval and extraction features MemHub lets a person turn on.

Graph rerank (usage A), episode recall (usage B), the episode write side and
the extraction switches are `MemoryConfig` fields. MemHub hard-codes none of
them: one global `memory_features` document sets them per client in
`build_sdk_config`, an unset one keeps the SDK default (so nothing set is the
config of before, byte for byte), and a change rebuilds the handles the way a
mode change does. A feature never touches the vector collection identity.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr, ValidationError

from one_memory import hy_sdk_runtime, sdk_write_processor
from one_memory.api import create_app
from one_memory.config import Settings
from one_memory.memory_features import (
    FEATURE_NAMES,
    FEATURES,
    FEATURES_BY_NAME,
    PARAMETERS,
    SETTING_NAMES,
    MemoryFeatures,
    effective_features,
    feature_activity,
)
from one_memory.memory_server.config_state import ConfigStore
from one_memory.model_profiles import ModelProfileSnapshot, ProviderKind
from one_memory.secrets import InMemorySecretStore

TOKEN = "memory-features-test"

#: What MemHub ran with before the setting existed, on a clean environment.
TODAY = {
    "graph_rerank": False,
    "same_series_timeline": False,
    "episode_recall": False,
    "session_episodes": False,
    "episode_summary": False,
    "intention": True,
    "intention_dedup": True,
    "few_shot": False,
}
#: The same for the numeric settings.
TODAY_PARAMETERS = {"intention_dedup_threshold": 0.95}


@pytest.fixture(autouse=True)
def _clean_sdk_env(monkeypatch):
    """The SDK's own env vars feed its defaults; tests start without them."""
    for spec in (*FEATURES, *PARAMETERS):
        monkeypatch.delenv(spec.env, raising=False)
    # The threshold falls back to the SDK's shared dedup variable.
    monkeypatch.delenv("MEMORY_DEDUP_THRESHOLD", raising=False)
    monkeypatch.delenv("ONE_MEMORY_GRAPH_STORE", raising=False)


def _remote_profile() -> ModelProfileSnapshot:
    return ModelProfileSnapshot(
        profile_id="remote",
        revision=3,
        provider_kind=ProviderKind.OPENAI_COMPATIBLE,
        base_url="https://models.example.test/v1",
        model="chat-model",
        embedding_provider_kind=ProviderKind.OPENAI_COMPATIBLE,
        embedding_model="embed-model",
        embedding_dimensions=1024,
        timeout_seconds=60,
        max_concurrency=1,
        context_window=None,
        has_api_key=True,
        # Since #255 the embedding endpoint is its own field, never inherited.
        embedding_base_url="https://models.example.test/v1",
    )


PROFILES = (hy_sdk_runtime.builtin_retrieval_snapshot(), _remote_profile())


def _config(tmp_path: Path, profile: ModelProfileSnapshot, features=()):
    return hy_sdk_runtime.build_sdk_config(
        profile, api_key="secret-key", data_dir=tmp_path, features=features
    )


def _field(config, name: str) -> bool:
    spec = FEATURES_BY_NAME[name]
    return getattr(getattr(config, spec.section), spec.field)


# --- the catalog and the document ------------------------------------------


def test_the_document_names_exactly_the_catalog() -> None:
    assert tuple(MemoryFeatures.model_fields) == SETTING_NAMES
    assert set(TODAY) == set(FEATURE_NAMES)
    # Each feature is a MemoryConfig field the SDK has; no env var is written.
    defaults = hy_sdk_runtime.sdk_feature_defaults()
    assert set(defaults) == set(SETTING_NAMES)


def test_the_defaults_are_todays_behaviour() -> None:
    everything = {**TODAY, **TODAY_PARAMETERS}
    assert hy_sdk_runtime.sdk_feature_defaults() == everything
    assert MemoryFeatures().overrides() == ()
    assert effective_features((), everything) == everything


@pytest.mark.parametrize("profile", PROFILES, ids=("builtin", "remote"))
def test_intention_dedup_defaults_to_on_at_095_in_the_built_config(tmp_path, profile) -> None:
    config = _config(tmp_path, profile)
    assert config.recall.intention_dedup_enabled is True
    assert config.recall.intention_dedup_threshold == 0.95


def test_the_sdk_env_var_still_moves_the_default_of_an_unset_feature(monkeypatch) -> None:
    monkeypatch.setenv("MEMORY_EPISODE_RECALL_ENABLED", "true")
    assert hy_sdk_runtime.sdk_feature_defaults()["episode_recall"] is True


def test_unknown_or_non_boolean_values_are_refused() -> None:
    with pytest.raises(ValidationError):
        MemoryFeatures.model_validate({"graph_rerank": True, "local_rerank": True})
    with pytest.raises(ValidationError):
        MemoryFeatures.model_validate({"graph_rerank": "true"})
    with pytest.raises(ValidationError):
        MemoryFeatures.model_validate({"graph_rerank": 1})
    with pytest.raises(ValueError, match="unknown_memory_feature"):
        hy_sdk_runtime.build_sdk_config(
            PROFILES[0], api_key=None, data_dir=Path("/tmp/unused"), features={"nope": True}
        )


@pytest.mark.parametrize("bad", (0.79, 1.01, -1, 2, True, "0.9", float("nan"), float("inf")))
def test_an_intention_dedup_threshold_outside_its_range_is_refused(bad) -> None:
    with pytest.raises(ValidationError):
        MemoryFeatures.model_validate({"intention_dedup_threshold": bad})


def test_the_intention_dedup_threshold_range_is_inclusive() -> None:
    for value in (0.8, 0.9, 1, 1.0):
        document = MemoryFeatures.model_validate({"intention_dedup_threshold": value})
        assert document.overrides() == (("intention_dedup_threshold", float(value)),)
    with pytest.raises(ValueError, match="invalid_memory_feature_value"):
        hy_sdk_runtime.build_sdk_config(
            PROFILES[0],
            api_key=None,
            data_dir=Path("/tmp/unused"),
            features={"intention_dedup_threshold": 0.5},
        )


def test_the_sdk_env_vars_still_move_the_threshold_default(monkeypatch) -> None:
    monkeypatch.setenv("MEMORY_DEDUP_THRESHOLD", "0.9")
    assert hy_sdk_runtime.sdk_feature_defaults()["intention_dedup_threshold"] == 0.9
    monkeypatch.setenv("MEMORY_INTENTION_DEDUP_THRESHOLD", "0.97")
    assert hy_sdk_runtime.sdk_feature_defaults()["intention_dedup_threshold"] == 0.97


def test_merge_sets_resets_and_leaves_absent_keys_alone() -> None:
    saved = MemoryFeatures(graph_rerank=True, few_shot=True)
    update = MemoryFeatures.model_validate({"few_shot": None, "episode_recall": True})
    merged = saved.merged(update)
    assert merged.overrides() == (("episode_recall", True), ("graph_rerank", True))
    assert saved.merged(MemoryFeatures()) == saved


def test_the_setting_persists_and_an_untouched_install_writes_none(tmp_path) -> None:
    store = ConfigStore(tmp_path)
    store.set_sync("cursor", True)
    assert "memory_features" not in json.loads((tmp_path / "config.json").read_text())
    store.set_memory_features(MemoryFeatures(graph_rerank=True, intention=False))
    saved = json.loads((tmp_path / "config.json").read_text())
    # Only what a person set; an absent key is the SDK default.
    assert saved["memory_features"] == {"graph_rerank": True, "intention": False}
    assert ConfigStore(tmp_path).memory_features().overrides() == (
        ("graph_rerank", True),
        ("intention", False),
    )
    (tmp_path / "config.json").write_text(json.dumps({"memory_features": {"bogus": True}}))
    with pytest.raises(Exception, match="config_invalid"):
        ConfigStore(tmp_path)


# --- how settings reach the SDK ----------------------------------------------


@pytest.mark.parametrize("profile", PROFILES, ids=("builtin", "remote"))
def test_each_feature_reaches_the_built_memory_config(tmp_path, profile) -> None:
    for spec in FEATURES:
        flipped = not TODAY[spec.name]
        config = _config(tmp_path, profile, {spec.name: flipped})
        assert _field(config, spec.name) is flipped, spec.name
        for other in FEATURES:
            if other.name != spec.name:
                assert _field(config, other.name) is TODAY[other.name], (spec.name, other.name)


@pytest.mark.parametrize("profile", PROFILES, ids=("builtin", "remote"))
def test_intention_dedup_on_off_and_threshold_reach_the_built_memory_config(
    tmp_path, profile
) -> None:
    off = _config(tmp_path, profile, {"intention_dedup": False})
    assert off.recall.intention_dedup_enabled is False
    assert off.recall.intention_dedup_threshold == 0.95
    on = _config(tmp_path, profile, {"intention_dedup": True, "intention_dedup_threshold": 0.9})
    assert on.recall.intention_dedup_enabled is True
    assert on.recall.intention_dedup_threshold == 0.9
    # Nothing else moves.
    assert _field(on, "intention") is True and _field(on, "graph_rerank") is False


@pytest.mark.parametrize("profile", PROFILES, ids=("builtin", "remote"))
def test_nothing_set_is_the_config_of_before_byte_for_byte(tmp_path, profile) -> None:
    before = repr(dataclasses.asdict(_config(tmp_path, profile)))
    assert repr(dataclasses.asdict(_config(tmp_path, profile, ()))) == before
    assert repr(dataclasses.asdict(_config(tmp_path, profile, {}))) == before
    # Setting every feature to its default value changes nothing either.
    assert repr(dataclasses.asdict(_config(tmp_path, profile, TODAY))) == before
    everything = {**TODAY, **TODAY_PARAMETERS}
    assert repr(dataclasses.asdict(_config(tmp_path, profile, everything))) == before


@pytest.mark.parametrize("profile", PROFILES, ids=("builtin", "remote"))
def test_features_never_change_the_collection_identity(tmp_path, profile) -> None:
    def identity(config):
        return (
            dataclasses.asdict(config.vector_store),
            config.embedder.model,
            config.embedder.embedding_dims,
            config.graph_store.db_path,
            config.cache.db_path,
        )

    baseline = identity(_config(tmp_path, profile))
    for values in (
        {name: True for name in FEATURE_NAMES},
        {name: False for name in FEATURE_NAMES},
        {"graph_rerank": True, "episode_recall": True},
        {"intention_dedup": False, "intention_dedup_threshold": 0.8},
    ):
        assert identity(_config(tmp_path, profile, values)) == baseline


def test_acquire_client_builds_the_client_with_the_features(tmp_path, monkeypatch) -> None:
    built: list[tuple[str, bool, bool]] = []

    class Client:
        def __init__(self, *, config, mode, runtime, embedding_service):
            built.append(
                (
                    mode,
                    config.graph_store.graph_rerank_enabled,
                    config.recall.episode_recall_enabled,
                )
            )

        def close(self):
            return None

    monkeypatch.setattr(hy_sdk_runtime, "HyMemoryClient", Client)
    monkeypatch.setattr(hy_sdk_runtime, "_acquire_runtime", lambda config, key: object())
    monkeypatch.setattr(hy_sdk_runtime, "_release_runtime", lambda key: None)
    monkeypatch.setattr(hy_sdk_runtime, "inspect_graph_width_mismatch", lambda config: None)
    profile = hy_sdk_runtime.builtin_retrieval_snapshot()
    hy_sdk_runtime.acquire_client(profile, api_key=None, data_dir=tmp_path, mode="pro").close()
    hy_sdk_runtime.acquire_client(
        profile,
        api_key=None,
        data_dir=tmp_path,
        mode="ultra",
        features=(("episode_recall", True), ("graph_rerank", True)),
    ).close()
    assert built == [("pro", False, False), ("ultra", True, True)]


# --- active in the current mode ---------------------------------------------


def test_a_feature_needing_a_higher_mode_is_reported_inactive() -> None:
    on = effective_features((("graph_rerank", True), ("episode_recall", True)), TODAY)
    assert feature_activity("graph_rerank", on, "pro") == "requires_ultra"
    assert feature_activity("graph_rerank", on, "ultra") is None
    # Usage B needs no graph: it acts in pro, not in lite (nothing extracted).
    assert feature_activity("episode_recall", on, "pro") is None
    assert feature_activity("episode_recall", on, "lite") == "requires_pro"
    assert feature_activity("intention", TODAY, "lite") == "requires_pro"
    assert feature_activity("few_shot", TODAY, "ultra") == "disabled"
    series = effective_features((("same_series_timeline", True),), TODAY)
    assert feature_activity("same_series_timeline", series, "ultra") == "requires_graph_rerank"
    series = effective_features((("same_series_timeline", True), ("graph_rerank", True)), TODAY)
    assert feature_activity("same_series_timeline", series, "pro") == "requires_ultra"
    assert feature_activity("same_series_timeline", series, "ultra") is None
    defaults = {**TODAY, **TODAY_PARAMETERS}
    assert feature_activity("intention_dedup", defaults, "pro") is None
    assert feature_activity("intention_dedup_threshold", defaults, "pro") is None
    assert feature_activity("intention_dedup_threshold", defaults, "lite") == "requires_pro"
    off = effective_features((("intention_dedup", False),), defaults)
    assert feature_activity("intention_dedup", off, "pro") == "disabled"
    assert feature_activity("intention_dedup_threshold", off, "pro") == "requires_intention_dedup"


# --- rebuilding handles -------------------------------------------------------


class _FakeHandle:
    def __init__(self, mode: str, features=(), profile_id: str = "p") -> None:
        self.mode = mode
        self.features = features
        self.profile_id = profile_id
        self.closed = False

    async def client(self):
        return self

    async def close(self):
        self.closed = True


def test_the_read_handle_rebuilds_its_client_when_the_features_change(
    monkeypatch, tmp_path
) -> None:
    """Reads follow the active profile (#255), the mode and the features."""
    from one_memory import sdk_read_handle

    features = [()]
    made: list[_FakeHandle] = []

    def handle_for(profile, *, api_key, data_dir, mode=None, features=()):
        handle = _FakeHandle(mode or "pro", features, profile.profile_id)
        made.append(handle)
        return handle

    monkeypatch.setattr(sdk_read_handle, "SdkClientHandle", handle_for)

    class _NoProfile:
        def active_identity(self):
            return None

    reads = sdk_read_handle.ActiveProfileReadHandle(
        profiles=_NoProfile(),  # type: ignore[arg-type]
        secrets=None,  # type: ignore[arg-type]
        data_dir=tmp_path,
        memory_mode=lambda: "pro",
        features=lambda: features[0],
    )
    hold = asyncio.Event()
    holding = asyncio.Event()

    async def read():
        return await reads.client()

    async def slow_read():
        client = await reads.client()
        holding.set()
        await hold.wait()
        return client

    async def scenario():
        in_flight = asyncio.create_task(slow_read())
        await asyncio.wait_for(holding.wait(), 5)
        old = made[0]
        assert (await asyncio.create_task(read())) is old
        features[0] = (("episode_recall", True),)
        try:
            fresh = await asyncio.create_task(read())
            assert fresh.features == (("episode_recall", True),) and fresh.mode == "pro"
            # A read that took the old client is still using it.
            assert not old.closed
        finally:
            hold.set()
            await in_flight
        for _ in range(10):
            await asyncio.sleep(0)
        assert old.closed and not fresh.closed
        await reads.close()
        assert fresh.closed

    asyncio.run(scenario())


class _Profiles:
    def active_identity(self):
        return ("profile-a", 1)

    def snapshot(self, profile_id, revision):
        return SimpleNamespace(profile_id=profile_id, has_api_key=False)

    def resolve_snapshot_secret(self, profile_id, revision, secrets):
        return None


def test_a_feature_change_keeps_a_busy_write_handle_until_its_write_ends(monkeypatch) -> None:
    features = [()]
    opened: list[_FakeHandle] = []

    def handle(profile, **kwargs):
        value = _FakeHandle(kwargs["mode"], kwargs["features"], profile.profile_id)
        opened.append(value)
        return value

    monkeypatch.setattr(sdk_write_processor, "SdkClientHandle", handle)
    coordinator = sdk_write_processor.SdkWriteCoordinator(
        profiles=_Profiles(),  # type: ignore[arg-type]
        secrets=None,  # type: ignore[arg-type]
        data_dir=None,
        transport_factory=lambda _profile: None,
        sedimentation_enabled=lambda: True,
        memory_mode=lambda: "pro",
        memory_features=lambda: features[0],
    )

    async def scenario():
        async with coordinator.use_client() as client:
            assert client.features == ()
            features[0] = (("few_shot", True),)
            await coordinator.retire_stale_handles()
            # In flight: it finishes with the features it started with (#218).
            assert not opened[0].closed
        assert opened[0].closed
        async with coordinator.use_client() as client:
            assert client.features == (("few_shot", True),)
        features[0] = ()
        await coordinator.retire_stale_handles()
        assert opened[1].closed
        await coordinator.close()

    asyncio.run(scenario())


# --- the HTTP surface -----------------------------------------------------------


def application(tmp_path: Path):
    settings = Settings(
        data_dir=tmp_path / "data",
        source_home=tmp_path,
        api_token=SecretStr(TOKEN),
        imports_enabled=False,
        automatic_memory_processing=False,
        live_sync_enabled=False,
    )
    return create_app(settings, secret_store=InMemorySecretStore())


def run_app(tmp_path, monkeypatch, scenario) -> None:
    async def main() -> None:
        app = application(tmp_path)

        @asynccontextmanager
        async def write_client():
            yield SimpleNamespace()

        async with app.router.lifespan_context(app):
            monkeypatch.setattr(app.state.sdk_write_coordinator, "use_client", write_client)
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": f"Bearer {TOKEN}"},
            ) as client:
                await scenario(app, client)

    asyncio.run(main())


def _by_name(body) -> dict[str, dict]:
    return {item["name"]: item for item in body["features"]}


@pytest.mark.fresh_sdk
def test_features_api_get_put_reset_and_status(tmp_path, monkeypatch) -> None:
    async def scenario(app, client):
        current = (await client.get("/api/v1/settings/memory-features")).json()
        assert current["mode"] == "pro" and current["non_default"] == {}
        listed = _by_name(current)
        assert list(listed) == list(FEATURE_NAMES)
        assert {name: item["enabled"] for name, item in listed.items()} == TODAY
        assert all(item["configured"] is None for item in listed.values())
        rerank = listed["graph_rerank"]
        assert rerank["default"] is False and rerank["active"] is False
        assert rerank["reason"] == "disabled" and rerank["requires_mode"] == "ultra"
        assert rerank["sdk_field"] == "graph_store.graph_rerank_enabled"
        assert rerank["env"] == "MEMORY_GRAPH_RERANK_ENABLED"
        assert listed["intention"]["active"] is True
        assert listed["episode_summary"]["cost"] == "llm_call"
        reads = app.state.sdk_client_handle
        # The read handle is keyed by (profile, mode, features): what it
        # builds next is what these return.
        assert reads.current_features() == ()

        changed = await client.put(
            "/api/v1/settings/memory-features",
            json={"graph_rerank": True, "episode_recall": True},
        )
        assert changed.status_code == 200, changed.text
        body = changed.json()
        assert body["changed"] == ["graph_rerank", "episode_recall"]
        listed = _by_name(body)
        # Enabled but the graph store is ultra only: reported, not hidden.
        assert listed["graph_rerank"]["enabled"] is True
        assert listed["graph_rerank"]["active"] is False
        assert listed["graph_rerank"]["reason"] == "requires_ultra"
        assert listed["episode_recall"]["active"] is True
        assert body["non_default"] == {"graph_rerank": True, "episode_recall": True}
        # The handles were rebuilt with the new features.
        expected = (("episode_recall", True), ("graph_rerank", True))
        assert app.state.sdk_write_coordinator.memory_features() == expected
        assert reads.current_features() == expected
        assert reads.memory_mode() == "pro"

        processing = (await client.get("/api/v1/memory-processing")).json()
        assert processing["pipeline"]["features"] == {
            "graph_rerank": True,
            "episode_recall": True,
        }

        monkeypatch.setattr(
            app.state.model_profile_store,
            "active_identity",
            lambda: ("active-profile", 1),
        )
        await client.put("/api/v1/settings/memory-mode", json={"mode": "ultra"})
        await app.state.memory_graph_backfill.wait()
        ultra = _by_name((await client.get("/api/v1/settings/memory-features")).json())
        assert ultra["graph_rerank"]["active"] is True
        assert ultra["graph_rerank"]["reason"] is None

        again = (
            await client.put("/api/v1/settings/memory-features", json={"graph_rerank": True})
        ).json()
        assert again["changed"] == []
        assert reads.current_features() == expected

        reset = (
            await client.put(
                "/api/v1/settings/memory-features",
                json={"graph_rerank": None, "episode_recall": None},
            )
        ).json()
        assert reset["changed"] == ["graph_rerank", "episode_recall"]
        assert reset["non_default"] == {}
        assert reads.current_features() == ()
        saved = json.loads((tmp_path / "data" / "config.json").read_text())
        assert saved["memory_features"] == {}

        for bad in (
            {"local_rerank": True},
            {"graph_rerank": "true"},
            {"graph_rerank": 1},
            {"features": {"graph_rerank": True}},
        ):
            response = await client.put("/api/v1/settings/memory-features", json=bad)
            assert response.status_code == 422, bad
        assert (
            await client.get("/api/v1/settings/memory-features", params={"user_id": "x"})
        ).status_code == 422
        assert (await client.get("/api/v1/settings/memory-features")).json()["non_default"] == {}

    run_app(tmp_path, monkeypatch, scenario)


@pytest.mark.fresh_sdk
def test_intention_dedup_api_on_off_threshold_and_422(tmp_path, monkeypatch) -> None:
    built: list[tuple[bool, float]] = []
    original = hy_sdk_runtime.build_sdk_config

    def recording(*args, **kwargs):
        config = original(*args, **kwargs)
        built.append(
            (config.recall.intention_dedup_enabled, config.recall.intention_dedup_threshold)
        )
        return config

    monkeypatch.setattr(hy_sdk_runtime, "build_sdk_config", recording)

    def built_now(app) -> tuple[bool, float]:
        """What a client built with the read handle's current key carries."""
        recording(
            hy_sdk_runtime.builtin_retrieval_snapshot(),
            api_key=None,
            data_dir=tmp_path,
            features=app.state.sdk_client_handle.current_features(),
        )
        return built[-1]

    async def scenario(app, client):
        path = "/api/v1/settings/memory-features"
        current = (await client.get(path)).json()
        dedup = _by_name(current)["intention_dedup"]
        assert dedup["enabled"] is True and dedup["default"] is True
        assert dedup["configured"] is None and dedup["active"] is True
        assert dedup["sdk_field"] == "recall.intention_dedup_enabled"
        assert dedup["env"] == "MEMORY_INTENTION_DEDUP_ENABLED"
        (threshold,) = current["parameters"]
        assert threshold == {
            "name": "intention_dedup_threshold",
            "value": 0.95,
            "configured": None,
            "default": 0.95,
            "minimum": 0.8,
            "maximum": 1.0,
            "active": True,
            "reason": None,
            "requires_feature": "intention_dedup",
            "sdk_field": "recall.intention_dedup_threshold",
            "env": "MEMORY_INTENTION_DEDUP_THRESHOLD",
            "description": threshold["description"],
        }
        assert built_now(app) == (True, 0.95)

        off = await client.put(path, json={"intention_dedup": False})
        assert off.status_code == 200, off.text
        body = off.json()
        assert body["changed"] == ["intention_dedup"]
        assert body["non_default"] == {"intention_dedup": False}
        assert body["parameters"][0]["active"] is False
        assert body["parameters"][0]["reason"] == "requires_intention_dedup"
        assert app.state.sdk_write_coordinator.memory_features() == (("intention_dedup", False),)
        assert built_now(app) == (False, 0.95)

        on = (
            await client.put(path, json={"intention_dedup": True, "intention_dedup_threshold": 0.9})
        ).json()
        assert on["changed"] == ["intention_dedup", "intention_dedup_threshold"]
        assert on["non_default"] == {"intention_dedup_threshold": 0.9}
        assert on["parameters"][0]["value"] == 0.9 and on["parameters"][0]["active"] is True
        expected = (("intention_dedup", True), ("intention_dedup_threshold", 0.9))
        assert app.state.sdk_client_handle.current_features() == expected
        assert built_now(app) == (True, 0.9)
        processing = (await client.get("/api/v1/memory-processing")).json()
        assert processing["pipeline"]["features"] == {"intention_dedup_threshold": 0.9}
        saved = json.loads((tmp_path / "data" / "config.json").read_text())
        assert saved["memory_features"] == {
            "intention_dedup": True,
            "intention_dedup_threshold": 0.9,
        }

        for bad in (0.5, 0.79, 1.5, -0.1, "0.9", True):
            response = await client.put(path, json={"intention_dedup_threshold": bad})
            assert response.status_code == 422, bad
        assert (await client.put(path, json={"intention_dedup": "false"})).status_code == 422
        # A refused body changes nothing.
        assert app.state.sdk_client_handle.current_features() == expected

        reset = (
            await client.put(
                path, json={"intention_dedup": None, "intention_dedup_threshold": None}
            )
        ).json()
        assert reset["changed"] == ["intention_dedup_threshold"]
        assert reset["non_default"] == {}
        assert app.state.sdk_client_handle.current_features() == ()
        assert built_now(app) == (True, 0.95)

    run_app(tmp_path, monkeypatch, scenario)
