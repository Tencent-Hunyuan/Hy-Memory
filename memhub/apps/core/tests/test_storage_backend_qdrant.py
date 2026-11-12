"""MemHub on a Qdrant vector store, end to end, against the pinned SDK wheel.

There is no Qdrant server in CI, so the child interpreter answers every
`QdrantClient(host=...)` with one shared in-process Qdrant (`qdrant_client`'s
own local engine, `:memory:`): to the SDK it is a server every store and every
client shares, exactly what MemHub requires (`storage_backends`). The rest is
real: Core's routes, the setting and its secret, the SDK's Qdrant store, the
lite write path and raw search, backup (with the export) and restore.

Like `test_pinned_sdk_raw_search`, the child runs with `-I` so `hy_memory` is
the installed wheel, with a deterministic local embedder (no model, no ONNX).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap

import pytest

pytestmark = pytest.mark.integration

_CHILD = textwrap.dedent(
    r"""
    import asyncio
    import hashlib
    import json
    import math
    import re
    import sys
    from pathlib import Path

    import hy_memory
    import qdrant_client

    home = Path(sys.argv[1])
    assert "site-packages" in Path(hy_memory.__file__).resolve().parts

    # One engine for every `QdrantClient(host=...)`: a server, in process.
    _Real = qdrant_client.QdrantClient
    _shared = _Real(location=":memory:")
    calls = []

    class SharedServer:
        def __init__(self, *args, **kwargs):
            calls.append({key: kwargs.get(key) for key in ("host", "port", "api_key")})
            assert kwargs.get("host"), kwargs

        def __getattr__(self, name):
            return getattr(_shared, name)

        def close(self):
            pass

    qdrant_client.QdrantClient = SharedServer

    from httpx import ASGITransport, AsyncClient
    from pydantic import SecretStr

    import one_memory.api as api
    from one_memory.api import create_app
    from one_memory.config import Settings
    from one_memory.embedding_assets import BUILTIN_EMBEDDING_DIMENSIONS
    from one_memory.hy_sdk_runtime import LocalOnnxEmbeddingService
    from one_memory.secrets import InMemorySecretStore
    from one_memory.storage_backends import VECTOR_SECRET_REF

    WORD = re.compile(r"\w+")

    class HashedEmbedder:
        dimensions = BUILTIN_EMBEDDING_DIMENSIONS

        def encode(self, texts):
            vectors = []
            for text in texts:
                vector = [0.0] * self.dimensions
                for word in WORD.findall(text.casefold()):
                    digest = hashlib.sha256(word.encode()).digest()
                    vector[int.from_bytes(digest[:4], "big") % self.dimensions] += 1.0
                norm = math.sqrt(sum(value * value for value in vector)) or 1.0
                vectors.append([value / norm for value in vector])
            return vectors

    LocalOnnxEmbeddingService._embedder = HashedEmbedder()

    async def lite_has_no_model(_app):
        return False

    api._recall_model_ready = lite_has_no_model
    secrets = InMemorySecretStore()
    data = home / "data"
    vector_dir = data / "hy-memory" / "governed-v2" / "vector"
    result = {}

    def settings():
        return Settings(
            data_dir=data,
            source_home=home,
            api_token=SecretStr("qdrant-test"),
            imports_enabled=False,
            automatic_memory_processing=False,
            live_sync_enabled=False,
        )

    def collections():
        return sorted(item.name for item in _shared.get_collections().collections)

    async def session(scenario):
        app = create_app(settings(), secret_store=secrets)
        async with app.router.lifespan_context(app):
            await app.state.embedding_switch.recover()
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer qdrant-test"},
            ) as http:
                await scenario(app, http)

    async def lite(app, http):
        await http.put("/api/v1/settings/memory-mode", json={"mode": "lite"})

    async def write(app, text, session_id):
        # From a task of its own: a read client is held until the asking
        # task ends, and a backup closes every client.
        await asyncio.create_task(_write(app, text, session_id))

    async def _write(app, text, session_id):
        client = await app.state.sdk_client_handle.client()
        written = client.add(
            [{"role": "user", "content": text}, {"role": "assistant", "content": "noted"}],
            user_id="local",
            source="hermes",
            session_id=session_id,
        )
        assert written.get("success", True), written

    async def search(http, query):
        response = await http.post(
            "/api/v2/context/search", json={"kinds": ["raw"], "limit": 5, "query": query}
        )
        assert response.status_code == 200, response.text
        return [item["content"] for item in response.json()["items"]]

    async def first(app, http):
        await lite(app, http)
        await write(app, "the chroma era: I keep bees on the roof", "s-chroma")
        result["chroma_files"] = sorted(path.name for path in vector_dir.iterdir())
        result["chroma_hits"] = await search(http, "bees on the roof")

        bad = await http.put(
            "/api/v1/settings/storage-backends", json={"vector": {"provider": "faiss"}}
        )
        result["faiss"] = [bad.status_code, bad.json()["detail"]]
        local = await http.put(
            "/api/v1/settings/storage-backends", json={"vector": {"provider": "qdrant"}}
        )
        result["qdrant_without_host"] = [local.status_code, local.json()["detail"]]

        changed = await http.put(
            "/api/v1/settings/storage-backends",
            json={
                "vector": {
                    "provider": "qdrant",
                    "host": "qdrant.internal",
                    "port": 6334,
                    "api_key": "qdrant-secret",
                }
            },
        )
        assert changed.status_code == 200, changed.text
        body = changed.json()
        result["changed"] = {
            key: body[key] for key in ("changed", "notice", "label", "previous_label")
        }
        result["prefix"] = body["vector"]["collection_prefix"]
        result["has_api_key"] = body["vector"]["has_api_key"]
        result["capabilities"] = {
            name: value["state"] for name, value in body["capabilities"].items()
        }
        result["secret"] = secrets.resolve(VECTOR_SECRET_REF)
        result["config"] = json.loads((data / "config.json").read_text())["storage_backends"]

        await write(app, "the qdrant era: my cat is called Miso", "s-qdrant")
        result["qdrant_hits"] = await search(http, "my cat is called Miso")
        result["old_hit_on_qdrant"] = await search(http, "bees on the roof")
        result["collections"] = collections()
        result["chroma_files_after"] = sorted(path.name for path in vector_dir.iterdir())
        result["connected"] = calls[-1]

        processing = (await http.get("/api/v1/memory-processing")).json()
        result["pipeline_storage"] = processing["pipeline"]["storage_backends"]
        spaces = (await http.get("/api/v1/embedding-spaces")).json()
        result["spaces_scan_error"] = spaces["scan_error"]

        backup = await http.post("/api/v1/backup", json={"target": "backups/qdrant"})
        result["backup"] = backup.status_code
        result["backup_kind"] = backup.json()["backend"]["kind"]
        restore = await http.post(
            "/api/v1/backup/restore", json={"source": "backups/qdrant.memhub-backup"}
        )
        result["restore"] = [restore.status_code, restore.json().get("detail")]
        # An old backup (the management database alone) restores as before.
        from one_memory.database_access import main_database

        main_database(data / "one-memory.sqlite3").back_up(data / "backups" / "old.sqlite3")
        old = await http.post("/api/v1/backup/restore", json={"source": "backups/old.sqlite3"})
        result["restore_old"] = [old.status_code, old.json().get("memories_included")]

    async def second(app, http):
        # A restart, after the restore: the setting and its secret held,
        # and the startup scan left the Chroma store of the first era alone.
        current = (await http.get("/api/v1/embedding-switches/current")).json()
        result["scan"] = [current["scan_state"], current["scan_error"], current["writes_paused"]]
        result["after_restart_hits"] = await search(http, "my cat is called Miso")
        reset = await http.delete("/api/v1/settings/storage-backends")
        body = reset.json()
        result["reset"] = [reset.status_code, body["label"], body["notice"], body["namespace"]]
        result["secret_after_reset"] = secrets.resolve(VECTOR_SECRET_REF)
        result["collections_after_reset"] = collections()
        result["back_on_chroma"] = await search(http, "bees on the roof")
        again = await http.put(
            "/api/v1/settings/storage-backends",
            json={"vector": {"provider": "qdrant", "host": "qdrant.internal"}},
        )
        assert again.status_code == 200, again.text
        result["prefix_again"] = again.json()["vector"]["collection_prefix"]
        result["found_again"] = await search(http, "my cat is called Miso")

    async def main():
        await session(first)
        await session(second)
        print("RESULT " + json.dumps(result, ensure_ascii=False))

    asyncio.run(main())
    """
)


@pytest.fixture(scope="module")
def qdrant(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("qdrant-backend")
    script = root / "scripts" / "child.py"
    script.parent.mkdir()
    script.write_text(_CHILD, encoding="utf-8")
    home = root / "home"
    home.mkdir()
    environment = {
        **os.environ,
        "ANONYMIZED_TELEMETRY": "False",
        "ONE_MEMORY_DATA_DIR": str(home / "data"),
        # Stray SDK variables must not move anything (`test_hy_network_egress`).
        "MEMORY_VECTOR_STORE": "faiss",
        "MEMORY_VECTOR_HOST": "ambient.example",
    }
    completed = subprocess.run(
        [sys.executable, "-I", str(script), str(home)],
        cwd=home,
        env=environment,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-6000:]
    [line] = [line for line in completed.stdout.splitlines() if line.startswith("RESULT ")]
    return json.loads(line.removeprefix("RESULT "))


def test_unusable_vector_backends_are_refused_with_stable_codes(qdrant) -> None:
    assert qdrant["faiss"] == [422, "storage_backend_faiss_unsupported"]
    assert qdrant["qdrant_without_host"] == [422, "storage_backend_vector_host_required"]


def test_the_setting_is_saved_with_its_secret_in_the_secret_store(qdrant) -> None:
    assert qdrant["changed"] == {
        "changed": True,
        "notice": "existing_memories_not_moved",
        "label": "Kuzu + Qdrant",
        "previous_label": "Kuzu + Chroma",
    }
    assert qdrant["has_api_key"] is True
    assert qdrant["secret"] == "qdrant-secret"
    config = qdrant["config"]
    assert config["vector"] == {"provider": "qdrant", "host": "qdrant.internal", "port": 6334}
    assert "qdrant-secret" not in json.dumps(config)
    assert qdrant["prefix"] == config["namespace"]
    assert qdrant["connected"] == {
        "host": "qdrant.internal",
        "port": 6334,
        "api_key": "qdrant-secret",
    }


def test_new_writes_land_in_qdrant_under_the_home_prefix(qdrant) -> None:
    assert any("Miso" in hit for hit in qdrant["qdrant_hits"])
    prefix = qdrant["prefix"]
    assert qdrant["collections"]
    assert all(name.startswith(f"{prefix}_memhub_") for name in qdrant["collections"])


def test_switching_moves_nothing_and_leaves_chroma_untouched(qdrant) -> None:
    assert any("bees" in hit for hit in qdrant["chroma_hits"])
    # The Chroma-era memory is not on Qdrant: nothing was copied.
    assert not any("bees" in hit for hit in qdrant["old_hit_on_qdrant"])
    # Not one file of the Chroma store was created, removed or renamed.
    assert qdrant["chroma_files_after"] == qdrant["chroma_files"]
    # Back on Chroma, its memories are where they were.
    assert any("bees" in hit for hit in qdrant["back_on_chroma"])


def test_embedding_maintenance_runs_on_this_homes_qdrant_collections(qdrant) -> None:
    capabilities = qdrant["capabilities"]
    # hy-memory 2.0.16 re-embeds Qdrant: the switch and the space scan work,
    # on this home's prefixed collections (`test_storage_backend_qdrant_switch`).
    assert capabilities["embedding_switch"] == "works"
    assert capabilities["embedding_space_scan"] == "works"
    assert capabilities["upgrade_migration"] == "skipped"
    assert capabilities["backup"] == "works"
    assert capabilities["restore"] == "works"
    assert qdrant["scan"] == ["done", None, False]
    assert qdrant["spaces_scan_error"] is None


def test_status_shows_the_active_backends(qdrant) -> None:
    storage = qdrant["pipeline_storage"]
    assert storage["graph"] == "kuzu" and storage["vector"] == "qdrant"
    assert storage["label"] == "Kuzu + Qdrant" and storage["is_default"] is False
    assert storage["health"] == "ok"


def test_backup_works_and_restore_keeps_the_setting(qdrant) -> None:
    assert qdrant["backup"] == 200
    assert qdrant["backup_kind"] == "remote_vector"
    # Written back onto the server through the SDK (`test_backup_includes_memories`).
    assert qdrant["restore"] == [200, None]
    assert qdrant["restore_old"] == [200, False]
    assert any("Miso" in hit for hit in qdrant["after_restart_hits"])


def test_reset_keeps_the_namespace_and_deletes_nothing(qdrant) -> None:
    status, label, notice, namespace = qdrant["reset"]
    assert (status, label, notice) == (200, "Kuzu + Chroma", "existing_memories_not_moved")
    assert namespace == qdrant["prefix"]
    assert qdrant["secret_after_reset"] is None
    assert qdrant["collections_after_reset"] == qdrant["collections"]
    # The same server again: the same prefix, the same memories.
    assert qdrant["prefix_again"] == qdrant["prefix"]
    assert any("Miso" in hit for hit in qdrant["found_again"])
