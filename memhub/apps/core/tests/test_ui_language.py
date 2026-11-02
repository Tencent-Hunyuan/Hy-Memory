"""The App's display language, and the SDK prompt language it implies.

The owner's rule (#243): every display language, zh-Hans and en included, is
passed as `MemoryConfig.language` (hy-memory 2.0.11); only an unset language
passes none, so the SDK keeps detecting zh/en exactly as before. A change rebuilds the
handles the way a mode or feature change does, and the language never touches
the vector collection identity.
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

from one_memory import hy_sdk_runtime, sdk_read_handle, sdk_write_processor
from one_memory.api import create_app
from one_memory.config import Settings
from one_memory.memory_server.config_state import ConfigDocument, ConfigStore
from one_memory.secrets import InMemorySecretStore
from one_memory.ui_language import UI_LANGUAGES, normalize_ui_language, sdk_language

TOKEN = "ui-language-test"
#: Every code is passed to the SDK, zh-Hans included.
PASSED = ("en", "zh-Hans", "zh-Hant", "ja", "es", "de", "pt", "fr", "ko", "ru", "id")


@pytest.fixture(autouse=True)
def _clean_sdk_env(monkeypatch):
    """The SDK's own `MEMORY_LANGUAGE` feeds its default; tests start without it."""
    monkeypatch.delenv("MEMORY_LANGUAGE", raising=False)


def _config(tmp_path: Path, language: str | None):
    return hy_sdk_runtime.build_sdk_config(
        hy_sdk_runtime.builtin_retrieval_snapshot(),
        api_key=None,
        data_dir=tmp_path,
        language=language,
    )


# --- the rule -------------------------------------------------------------------


def test_eleven_codes_and_the_legacy_chinese_code() -> None:
    assert PASSED == UI_LANGUAGES
    assert len(UI_LANGUAGES) == 11
    # Earlier App builds stored zh-CN; it is zh-Hans now.
    assert normalize_ui_language("zh-CN") == "zh-Hans"
    assert normalize_ui_language("system") is None
    assert normalize_ui_language("xx") is None
    assert normalize_ui_language(None) is None


def test_only_an_unset_language_leaves_the_sdk_to_detect() -> None:
    assert sdk_language(None) is None
    assert sdk_language("system") is None
    assert sdk_language("xx") is None
    # zh-Hans is passed like every other code; the legacy zh-CN maps to it.
    assert sdk_language("zh-Hans") == "zh-Hans"
    assert sdk_language("zh-CN") == "zh-Hans"
    for code in PASSED:
        assert sdk_language(code) == code


def test_no_language_builds_the_config_of_before(tmp_path) -> None:
    assert _config(tmp_path, None).language is None
    assert _config(tmp_path, sdk_language("zh-Hans")).language == "zh-Hans"


@pytest.mark.parametrize("code", PASSED)
def test_a_passed_code_reaches_the_memory_config(tmp_path, code) -> None:
    config = _config(tmp_path, sdk_language(code))
    assert config.language == code


def test_the_language_never_changes_the_collection_identity(tmp_path) -> None:
    def identity(config):
        return (
            dataclasses.asdict(config.vector_store),
            config.embedder.model,
            config.embedder.embedding_dims,
            config.graph_store.db_path,
            config.cache.db_path,
        )

    baseline = identity(_config(tmp_path, None))
    for code in PASSED:
        assert identity(_config(tmp_path, code)) == baseline


def test_acquire_client_passes_the_language_only_when_there_is_one(tmp_path, monkeypatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(
        hy_sdk_runtime, "_acquire_client", lambda profile, **kwargs: calls.append(kwargs)
    )
    profile = hy_sdk_runtime.builtin_retrieval_snapshot()
    hy_sdk_runtime.acquire_client(profile, api_key=None, data_dir=tmp_path, mode="pro")
    hy_sdk_runtime.acquire_client(
        profile, api_key=None, data_dir=tmp_path, mode="pro", language="ja"
    )
    assert "language" not in calls[0]
    assert calls[1]["language"] == "ja"


# --- the stored setting -----------------------------------------------------------


def test_the_setting_persists_and_an_untouched_install_writes_none(tmp_path) -> None:
    store = ConfigStore(tmp_path)
    store.set_sync("cursor", True)
    assert "ui_language" not in json.loads((tmp_path / "config.json").read_text())
    assert store.ui_language() is None
    store.set_ui_language("ja")
    assert json.loads((tmp_path / "config.json").read_text())["ui_language"] == "ja"
    assert ConfigStore(tmp_path).ui_language() == "ja"
    store.set_ui_language(None)
    assert ConfigStore(tmp_path).ui_language() is None
    with pytest.raises(ValueError, match="invalid_ui_language"):
        store.set_ui_language("zh-CN")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        ConfigDocument.model_validate({"ui_language": "klingon"})


# --- rebuilding handles -------------------------------------------------------------


class _FakeHandle:
    def __init__(self, language: str | None) -> None:
        self.language = language
        self.closed = False

    async def client(self):
        return self

    async def close(self):
        self.closed = True


def test_the_read_handle_rebuilds_its_client_when_the_language_changes(
    monkeypatch, tmp_path
) -> None:
    language: list[str | None] = [None]
    made: list[_FakeHandle] = []

    def handle_for(profile, *, api_key, data_dir, mode=None, features=(), language=None):
        made.append(_FakeHandle(language))
        return made[-1]

    monkeypatch.setattr(sdk_read_handle, "SdkClientHandle", handle_for)

    class _NoProfile:
        def active_identity(self):
            return None

    reads = sdk_read_handle.ActiveProfileReadHandle(
        profiles=_NoProfile(),  # type: ignore[arg-type]
        secrets=None,  # type: ignore[arg-type]
        data_dir=tmp_path,
        memory_mode=lambda: "pro",
        language=lambda: language[0],
    )

    async def scenario():
        # A use lasts as long as the task that asked, so each read is a task.
        old = await asyncio.create_task(reads.client())
        assert old.language is None
        assert await asyncio.create_task(reads.client()) is old
        language[0] = "ja"
        fresh = await asyncio.create_task(reads.client())
        assert fresh is not old and fresh.language == "ja"
        for _ in range(10):
            await asyncio.sleep(0)
        assert old.closed and not fresh.closed
        await reads.close()

    asyncio.run(scenario())


class _Profiles:
    def active_identity(self):
        return ("profile-a", 1)

    def snapshot(self, profile_id, revision):
        return SimpleNamespace(profile_id=profile_id, has_api_key=False)

    def resolve_snapshot_secret(self, profile_id, revision, secrets):
        return None


def test_a_language_change_keeps_a_busy_write_handle_until_its_write_ends(monkeypatch) -> None:
    language: list[str | None] = [None]
    opened: list[_FakeHandle] = []

    def handle(profile, **kwargs):
        opened.append(_FakeHandle(kwargs.get("language")))
        return opened[-1]

    monkeypatch.setattr(sdk_write_processor, "SdkClientHandle", handle)
    coordinator = sdk_write_processor.SdkWriteCoordinator(
        profiles=_Profiles(),  # type: ignore[arg-type]
        secrets=None,  # type: ignore[arg-type]
        data_dir=None,
        transport_factory=lambda _profile: None,
        sedimentation_enabled=lambda: True,
        memory_mode=lambda: "pro",
        sdk_language=lambda: language[0],
    )

    async def scenario():
        async with coordinator.use_client() as client:
            assert client.language is None
            language[0] = "de"
            await coordinator.retire_stale_handles()
            # In flight: it finishes in the language it started with (#218).
            assert not opened[0].closed
        assert opened[0].closed
        async with coordinator.use_client() as client:
            assert client.language == "de"
        language[0] = None
        await coordinator.retire_stale_handles()
        assert opened[1].closed
        await coordinator.close()

    asyncio.run(scenario())


# --- the HTTP surface -----------------------------------------------------------------


def run_app(tmp_path, monkeypatch, scenario) -> None:
    async def main() -> None:
        settings = Settings(
            data_dir=tmp_path / "data",
            source_home=tmp_path,
            api_token=SecretStr(TOKEN),
            imports_enabled=False,
            automatic_memory_processing=False,
            live_sync_enabled=False,
        )
        app = create_app(settings, secret_store=InMemorySecretStore())

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


@pytest.mark.fresh_sdk
def test_ui_language_api_get_put_and_status(tmp_path, monkeypatch) -> None:
    async def scenario(app, client):
        reads = app.state.sdk_client_handle
        writes = app.state.sdk_write_coordinator
        retired: list[str | None] = []
        retire = writes.retire_stale_handles

        async def counting():
            retired.append(writes.sdk_language())
            await retire()

        monkeypatch.setattr(writes, "retire_stale_handles", counting)

        current = (await client.get("/api/v1/settings/ui-language")).json()
        assert current["ui_language"] is None and current["sdk_language"] == "auto"
        assert current["languages"] == list(UI_LANGUAGES)
        assert reads.current_language() is None and writes.sdk_language() is None

        # zh-Hans is passed explicitly: Chinese prompts, handles rebuilt (#243).
        body = (
            await client.put("/api/v1/settings/ui-language", json={"ui_language": "zh-Hans"})
        ).json()
        assert body == {
            **current,
            "ui_language": "zh-Hans",
            "sdk_language": "zh-Hans",
            "rebuilt": True,
        }
        assert reads.current_language() == "zh-Hans" and writes.sdk_language() == "zh-Hans"
        assert retired == ["zh-Hans"]
        processing = (await client.get("/api/v1/memory-processing")).json()
        assert processing["pipeline"]["sdk_language"] == "zh-Hans"

        # The same value again changes nothing.
        body = (
            await client.put("/api/v1/settings/ui-language", json={"ui_language": "zh-Hans"})
        ).json()
        assert body["rebuilt"] is False and retired == ["zh-Hans"]

        body = (await client.put("/api/v1/settings/ui-language", json={"ui_language": "ja"})).json()
        assert body["sdk_language"] == "ja" and body["rebuilt"] is True
        assert reads.current_language() == "ja" and writes.sdk_language() == "ja"
        assert retired == ["zh-Hans", "ja"]

        # An explicit English choice forces English prompts.
        body = (await client.put("/api/v1/settings/ui-language", json={"ui_language": "en"})).json()
        assert body["sdk_language"] == "en" and body["rebuilt"] is True

        processing = (await client.get("/api/v1/memory-processing")).json()
        assert processing["pipeline"]["ui_language"] == "en"
        assert processing["pipeline"]["sdk_language"] == "en"

        # Unset (CLI `settings language system`): auto again.
        body = (await client.put("/api/v1/settings/ui-language", json={"ui_language": None})).json()
        assert body["sdk_language"] == "auto" and body["rebuilt"] is True
        assert reads.current_language() is None
        saved = json.loads((tmp_path / "data" / "config.json").read_text())
        assert saved["ui_language"] is None

        for bad in ({"ui_language": "zh-CN"}, {"ui_language": "xx"}, {"language": "ja"}, {}):
            response = await client.put("/api/v1/settings/ui-language", json=bad)
            assert response.status_code == 422, bad

    run_app(tmp_path, monkeypatch, scenario)
