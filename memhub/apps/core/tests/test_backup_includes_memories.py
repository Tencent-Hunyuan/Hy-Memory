"""A backup holds the memories: back up, wipe, restore, and everything is back.

Real stores end to end, against the pinned SDK wheel in a child interpreter
(`-I`, so `hy_memory` is the installed wheel, not the repo's source tree):
Chroma, Kuzu and the SDK's SQLite files under a temp home, Core's routes and
its write drain. A local OpenAI-compatible stand-in answers generation (with
an error: no extraction runs) and the built-in embedding is a deterministic
hash, so no model or ONNX file is needed.

Raw turns go through the real write queue in lite; facts are put into the
SDK's vector store in pro and ultra for two users; the ultra graph is built
by the real `MemoryGraphBackfill`. The local child then:

- backs up while 20 writes are queued, and again after everything is written;
- wipes `hy-memory/governed-v2`, restores, and compares every memory, raw
  turn and graph node, and searches;
- refuses a backup whose counts do not match, and fails in the middle of a
  swap, and both times the previous data is intact;
- restores an old (management database only) backup as before;
- restores a backup whose graph is corrupt: the graph is quarantined and
  rebuilt on the next start.

The remote child uses `qdrant_client`'s local engine as the Qdrant server
(as `test_storage_backend_qdrant` does) for the export and the refusals.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_COMMON = textwrap.dedent(
    r"""
    import asyncio
    import hashlib
    import json
    import math
    import re
    import shutil
    import sys
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from pathlib import Path

    import hy_memory

    home = Path(sys.argv[1])
    assert "site-packages" in Path(hy_memory.__file__).resolve().parts

    from one_memory.embedding_assets import BUILTIN_EMBEDDING_DIMENSIONS, BUILTIN_EMBEDDING_MODEL

    WIDTH = BUILTIN_EMBEDDING_DIMENSIONS
    WORD = re.compile(r"\w+")


    def vector(text):
        values = [0.0] * WIDTH
        for word in WORD.findall(text.casefold()):
            digest = hashlib.sha256(word.encode()).digest()
            values[int.from_bytes(digest[:4], "big") % WIDTH] += 1.0
        norm = math.sqrt(sum(value * value for value in values)) or 1.0
        return [value / norm for value in values]


    class Generation(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 - http.server's name
            self.rfile.read(int(self.headers["Content-Length"]))
            payload = json.dumps({"error": {"message": "no generation here"}}).encode()
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            return


    server = ThreadingHTTPServer(("127.0.0.1", 0), Generation)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/v1"

    from httpx import ASGITransport, AsyncClient
    from pydantic import SecretStr

    from one_memory.api import create_app
    from one_memory.config import Settings
    from one_memory.hy_sdk_runtime import LocalOnnxEmbeddingService
    from one_memory.model_profiles import (
        ModelCapabilities,
        ModelProfileInput,
        ModelProfileStore,
        ProviderKind,
    )
    from one_memory.secrets import InMemorySecretStore


    class HashedEmbedder:
        dimensions = WIDTH

        def encode(self, texts):
            return [vector(text) for text in texts]


    LocalOnnxEmbeddingService._embedder = HashedEmbedder()
    data = home / "data"
    data.mkdir(parents=True, exist_ok=True)
    secrets = InMemorySecretStore()
    LAYERS = ("l0_basic_info", "l1_raw", "l2_fact", "l3_summary", "l4_identity")
    STATUSES = ["active", "superseded", "negated", "conflicted", "shadow", "archived"]
    USERS = ("local", "alice")
    result = {}


    def make_profile():
        # Generation through the stand-in, embedding with the built-in model:
        # the space the first start registered stays the live one.
        profiles = ModelProfileStore(data / "one-memory.sqlite3")
        created = profiles.create(
            ModelProfileInput(
                name="stand-in",
                provider_kind=ProviderKind.OPENAI_COMPATIBLE,
                base_url=url,
                model="chat",
                embedding_provider_kind=ProviderKind.LOCAL_ONNX,
                embedding_model=BUILTIN_EMBEDDING_MODEL,
                api_key=SecretStr("sk-test"),
            ),
            secrets,
        )
        profiles.record_capabilities(
            created.profile_id,
            expected_revision=created.revision,
            capabilities=ModelCapabilities(
                endpoint_reachable=True,
                model_available=True,
                structured_output=True,
                embedding_available=True,
            ),
            secrets=secrets,
        )
        profiles.activate(created.profile_id, secrets)


    def settings():
        return Settings(
            data_dir=data,
            source_home=home,
            api_token=SecretStr("backup-test"),
            imports_enabled=False,
            automatic_memory_processing=False,
            live_sync_enabled=False,
        )


    async def session(scenario):
        app = create_app(settings(), secret_store=secrets)
        async with app.router.lifespan_context(app):
            await app.state.embedding_switch.recover()
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer backup-test"},
                timeout=120,
            ) as http:
                await scenario(app, http)


    async def nothing(app, http):
        return None


    async def mode(http, name):
        response = await http.put("/api/v1/settings/memory-mode", json={"mode": name})
        assert response.status_code == 200, response.text


    async def raw_write(http, user, text, number):
        request_id = f"w-{user}-{number}"
        response = await http.post(
            "/api/v2/memory-writes",
            json={
                "request_id": request_id,
                "session_id": f"s-{user}",
                "source_id": "one-memory-manual",
                "user_id": user,
                "messages": [
                    {"role": "user", "content": text},
                    {"role": "assistant", "content": "noted"},
                ],
            },
        )
        assert response.status_code == 202, response.text
        return (request_id, user)


    async def wait_writes(http, pending):
        pending = set(pending)
        deadline = time.monotonic() + 120
        while pending:
            for request_id, user in list(pending):
                response = await http.get(
                    f"/api/v2/memory-writes/{request_id}", params={"user_id": user}
                )
                state = response.json().get("state")
                if state in ("completed", "failed"):
                    assert state == "completed", response.text
                    pending.discard((request_id, user))
            assert time.monotonic() < deadline, pending
            await asyncio.sleep(0.2)


    async def snapshot(app):
        # A read client is held until the asking task ends: ask from a task.
        async def read():
            client = await app.state.sdk_client_handle.client()
            out = {}
            for user in USERS:
                for layer in LAYERS:
                    listed = client.list_memories(
                        user_id=user, layers=[layer], statuses=STATUSES, limit=5000
                    )
                    out[f"{user}/{layer}"] = sorted(
                        [item.get("memory_id"), item.get("content"), item.get("status")]
                        for item in listed["vdb"]["memories"]
                    )
            return out

        return await asyncio.create_task(read())


    def manifest(name):
        return json.loads((data / "backups" / name / "manifest.json").read_text())
    """
)

_LOCAL = _COMMON + textwrap.dedent(
    r"""
    from hy_memory.models.memory import MemoryLayer, MemoryNode, MemoryStatus

    from one_memory.database_access import MainDatabase, main_database
    from one_memory.embedding_spaces import read_live_space
    from one_memory.graph_open_guard import count_graph_memories
    from one_memory.hy_graph_admin import live_graph_path
    from one_memory.hy_sdk_migration import release_chroma_systems


    def facts(user, texts, status=MemoryStatus.ACTIVE, layer=MemoryLayer.L2_FACT):
        return [
            MemoryNode(
                user_id=user,
                agent_id="default_agent",
                session_id=f"s-{user}",
                layer=layer,
                content=text,
                embedding=vector(text),
                status=status,
            )
            for text in texts
        ]


    async def put_nodes(app, nodes):
        # The SDK's own store: what a pro/ultra extraction leaves behind.
        async with app.state.sdk_write_coordinator.use_client() as client:
            client._loop_thread.run(client._vector_store.upsert_batch(nodes))


    async def search_raw(http, user, query):
        response = await http.post(
            "/api/v2/context/search",
            json={"kinds": ["raw"], "limit": 5, "query": query, "user_id": user},
        )
        assert response.status_code == 200, response.text
        return [item["content"] for item in response.json()["items"]]


    async def search_facts(app, user, query):
        async def run():
            client = await app.state.sdk_client_handle.client()
            found = client.search(query=query, user_ids=[user], limit=5, min_score=0.0)
            return [item["content"] for item in found["memories"]["normal"]]

        return await asyncio.create_task(run())


    def graph_counts():
        live = read_live_space(data / "one-memory.sqlite3")
        return count_graph_memories(live_graph_path(data, live.graph_dir))


    def mode_bits(path):
        return oct(path.stat().st_mode & 0o777)


    async def first(app, http):
        await mode(http, "lite")
        written = []
        for user in USERS:
            for number in range(3):
                written.append(
                    await raw_write(http, user, f"{user} raw turn {number} about gardening", number)
                )
        await wait_writes(http, written)
        queued = [
            await raw_write(http, "local", f"local queued turn {number} about bees", number)
            for number in range(10, 30)
        ]
        during = await http.post("/api/v1/backup", json={"target": "backups/during-writes"})
        result["during"] = [during.status_code, during.json()]
        result["write_gate_after_backup"] = app.state.backup_pause
        await wait_writes(http, queued)
        result["queued_completed"] = len(queued)

        await mode(http, "pro")
        await put_nodes(
            app,
            facts("local", ["local likes green tea", "local lives in Lyon"])
            + facts("alice", ["alice plays the cello"])
            + facts("local", ["local lived in Paris"], status=MemoryStatus.SUPERSEDED)
            + facts("alice", ["alice is called Alice"], layer=MemoryLayer.L0_BASIC_INFO),
        )
        await mode(http, "ultra")
        await put_nodes(
            app,
            facts("alice", ["alice studies marine biology"])
            + facts("local", ["local maybe owns a boat"], status=MemoryStatus.SHADOW),
        )
        await app.state.memory_graph_backfill.wait()
        result["before"] = await snapshot(app)
        estimate = await http.get("/api/v1/backup/estimate")
        result["estimate"] = [estimate.status_code, estimate.json()]
        outside = await http.post("/api/v1/backup", json={"target": "/tmp/elsewhere"})
        result["outside"] = [outside.status_code, outside.json()]
        backup = await http.post("/api/v1/backup", json={"target": "backups/full.sqlite3"})
        result["backup"] = [backup.status_code, backup.json()]
        again = await http.post("/api/v1/backup", json={"target": "backups/full"})
        result["again"] = [again.status_code, again.json()]
        target = data / "backups" / "full.memhub-backup"
        result["modes"] = sorted(
            {mode_bits(path) for path in [target, *target.rglob("*")] if path.is_dir()}
        ) + sorted({mode_bits(path) for path in target.rglob("*") if path.is_file()})
        result["files"] = sorted(
            path.relative_to(target).as_posix() for path in target.rglob("*") if path.is_file()
        )
        result["manifest"] = manifest("full.memhub-backup")
        # The v1 shape: the management database alone, as builds before
        # 1.0.7 wrote it.
        main_database(data / "one-memory.sqlite3").back_up(data / "backups" / "old.sqlite3")


    def wipe():
        # What a lost disk leaves: no SDK store. (One process runs every
        # session here, so Chroma's process cache is dropped as a restart would.)
        release_chroma_systems()
        shutil.rmtree(data / "hy-memory" / "governed-v2")
        (data / "evolve-state.json").unlink(missing_ok=True)


    async def restore_full(app, http):
        result["wiped"] = await snapshot(app)
        response = await http.post(
            "/api/v1/backup/restore", json={"source": "backups/full.memhub-backup"}
        )
        result["restore_full"] = [response.status_code, response.json()]


    def copy_backup(name, change):
        source = data / "backups" / "full.memhub-backup"
        target = data / "backups" / f"{name}.memhub-backup"
        shutil.copytree(source, target)
        document = json.loads((target / "manifest.json").read_text())
        change(target, document)
        (target / "manifest.json").write_text(json.dumps(document))


    def wrong_count(target, document):
        document["counts"]["layers"]["local"]["l2_fact"] += 1


    def corrupt_graph(target, document):
        graph = target / "sdk" / "governed-v2" / "graph"
        graph.write_bytes(b"this is not a kuzu database" * 4096)
        for item in document["files"]:
            if item["path"] == "sdk/governed-v2/graph":
                item["bytes"] = graph.stat().st_size
                item["sha256"] = hashlib.sha256(graph.read_bytes()).hexdigest()


    def damaged_file(target, document):
        (target / "sdk" / "governed-v2" / "history.sqlite3").write_bytes(b"x")


    async def after_restore(app, http):
        result["after"] = await snapshot(app)
        result["raw_hit"] = await search_raw(http, "alice", "raw turn 2 about gardening")
        result["fact_hit"] = await search_facts(app, "local", "green tea")

        copy_backup("wrong-count", wrong_count)
        response = await http.post(
            "/api/v1/backup/restore", json={"source": "backups/wrong-count.memhub-backup"}
        )
        result["restore_mismatch"] = [response.status_code, response.json()]
        result["after_mismatch"] = await snapshot(app)

        copy_backup("damaged", damaged_file)
        response = await http.post(
            "/api/v1/backup/restore", json={"source": "backups/damaged.memhub-backup"}
        )
        result["restore_damaged"] = [response.status_code, response.json()]

        original = MainDatabase.restore_from

        def failing(self, source, **kwargs):
            if ".management.backup-restore-" in str(source):
                raise RuntimeError("disk pulled out")
            return original(self, source, **kwargs)

        MainDatabase.restore_from = failing
        try:
            response = await http.post(
                "/api/v1/backup/restore", json={"source": "backups/full.memhub-backup"}
            )
        finally:
            MainDatabase.restore_from = original
        result["restore_failure"] = [response.status_code, response.json()]
        result["after_failure"] = await snapshot(app)
        result["journal_after_failure"] = (data / ".memhub-restore.json").exists()

        response = await http.post("/api/v1/backup/restore", json={"source": "backups/old.sqlite3"})
        result["restore_old"] = [response.status_code, response.json()]
        result["after_old"] = await snapshot(app)

        response = await http.post(
            "/api/v1/backup/restore", json={"source": "backups/during-writes.memhub-backup"}
        )
        result["restore_during"] = [response.status_code, response.json()]
        result["after_during"] = await snapshot(app)

        copy_backup("corrupt-graph", corrupt_graph)
        response = await http.post(
            "/api/v1/backup/restore", json={"source": "backups/corrupt-graph.memhub-backup"}
        )
        result["restore_corrupt"] = [response.status_code, response.json()]
        result["after_corrupt"] = await snapshot(app)
        result["quarantined"] = sorted(
            path.name
            for path in (data / "hy-memory" / "governed-v2").iterdir()
            if ".quarantined-" in path.name
        )
        result["graph_build_scheduled"] = json.loads((data / "evolve-state.json").read_text())[
            "graph_build"
        ]


    async def rebuilt(app, http):
        await asyncio.sleep(0.5)
        await app.state.memory_graph_backfill.wait()
        result["graph_build_after"] = app.state.evolve_state_store.graph_build().model_dump(
            mode="json"
        )


    async def main():
        await session(nothing)
        make_profile()
        await session(first)
        result["graph_before"] = graph_counts()
        wipe()
        await session(restore_full)
        result["graph_restored"] = graph_counts()
        await session(after_restore)
        await session(rebuilt)
        result["graph_rebuilt"] = graph_counts()
        print("RESULT " + json.dumps(result, ensure_ascii=False))


    try:
        asyncio.run(main())
    finally:
        server.shutdown()
    """
)

_REMOTE = _COMMON + textwrap.dedent(
    r"""
    import qdrant_client

    # One engine for every `QdrantClient(host=...)`: a server, in process.
    _Real = qdrant_client.QdrantClient
    _shared = _Real(location=":memory:")


    class SharedServer:
        def __init__(self, *args, **kwargs):
            assert kwargs.get("host"), kwargs

        def __getattr__(self, name):
            return getattr(_shared, name)

        def close(self):
            pass


    qdrant_client.QdrantClient = SharedServer
    QDRANT = {"vector": {"provider": "qdrant", "host": "qdrant.internal", "port": 6334}}


    async def chroma_era(app, http):
        await mode(http, "lite")
        await wait_writes(http, [await raw_write(http, "local", "chroma era bees", 0)])
        response = await http.post("/api/v1/backup", json={"target": "backups/chroma-era"})
        result["chroma_backup"] = response.status_code
        changed = await http.put("/api/v1/settings/storage-backends", json=QDRANT)
        assert changed.status_code == 200, changed.text
        result["capabilities"] = {
            name: value
            for name, value in changed.json()["capabilities"].items()
            if name in ("backup", "restore")
        }


    async def qdrant_era(app, http):
        written = []
        for user in USERS:
            for number in range(1, 4):
                written.append(
                    await raw_write(http, user, f"{user} qdrant turn {number} about cats", number)
                )
        await wait_writes(http, written)
        result["before"] = await snapshot(app)
        estimate = await http.get("/api/v1/backup/estimate")
        result["estimate"] = [estimate.status_code, estimate.json()]
        response = await http.post("/api/v1/backup", json={"target": "backups/qdrant"})
        result["backup"] = [response.status_code, response.json()]
        backup = data / "backups" / "qdrant.memhub-backup"
        result["manifest"] = manifest("qdrant.memhub-backup")
        lines = (backup / "export" / "memories.jsonl").read_text().splitlines()
        result["export_lines"] = [json.loads(line) for line in lines]
        response = await http.post(
            "/api/v1/backup/restore", json={"source": "backups/qdrant.memhub-backup"}
        )
        result["restore_qdrant"] = [response.status_code, response.json()]
        response = await http.post(
            "/api/v1/backup/restore", json={"source": "backups/chroma-era.memhub-backup"}
        )
        result["restore_local_on_qdrant"] = [response.status_code, response.json()]
        result["after_refusals"] = await snapshot(app)
        reset = await http.delete("/api/v1/settings/storage-backends")
        assert reset.status_code == 200, reset.text


    async def back_on_chroma(app, http):
        response = await http.post(
            "/api/v1/backup/restore", json={"source": "backups/qdrant.memhub-backup"}
        )
        result["restore_qdrant_on_chroma"] = [response.status_code, response.json()]


    async def main():
        await session(nothing)
        make_profile()
        await session(chroma_era)
        await session(qdrant_era)
        await session(back_on_chroma)
        print("RESULT " + json.dumps(result, ensure_ascii=False))


    try:
        asyncio.run(main())
    finally:
        server.shutdown()
    """
)


def _run(tmp_path_factory, name: str, source: str) -> dict:
    root = tmp_path_factory.mktemp(name)
    script = root / "scripts" / "child.py"
    script.parent.mkdir()
    script.write_text(source, encoding="utf-8")
    home = root / "home"
    home.mkdir()
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("MEMORY_GRAPH_")
    }
    environment.update(
        {
            "ANONYMIZED_TELEMETRY": "False",
            "ONE_MEMORY_DATA_DIR": str(home / "data"),
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
        }
    )
    completed = subprocess.run(
        [sys.executable, "-I", str(script), str(home)],
        cwd=home,
        env=environment,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-6000:]
    [line] = [line for line in completed.stdout.splitlines() if line.startswith("RESULT ")]
    document = json.loads(line.removeprefix("RESULT "))
    document["_home"] = str(home)
    return document


@pytest.fixture(scope="module")
def local(tmp_path_factory) -> dict:
    return _run(tmp_path_factory, "backup-local", _LOCAL)


@pytest.fixture(scope="module")
def remote(tmp_path_factory) -> dict:
    return _run(tmp_path_factory, "backup-remote", _REMOTE)


def _layer_counts(snapshot: dict) -> dict:
    return {key: len(value) for key, value in snapshot.items()}


def test_the_backup_holds_every_memory_raw_turn_and_graph_node(local) -> None:
    status, body = local["backup"]
    assert status == 200, body
    # `backups/full.sqlite3` (the v1 habit) names the v2 directory.
    assert body["target"].endswith("/backups/full.memhub-backup")
    assert body["schema_version"] == 2 and body["memories_included"] is True
    assert body["users"] == ["local", "alice"]
    layers = body["counts"]["layers"]
    before = _layer_counts(local["before"])
    for user in ("local", "alice"):
        for layer in ("l0_basic_info", "l1_raw", "l2_fact", "l3_summary", "l4_identity"):
            assert layers[user][layer] == before[f"{user}/{layer}"]
    assert layers["local"]["l1_raw"] == 23 and layers["alice"]["l1_raw"] == 3
    # Every status is in the count: active, superseded and shadow facts.
    assert layers["local"]["l2_fact"] == 4 and layers["alice"]["l0_basic_info"] == 1
    assert body["counts"]["graph"] == local["graph_before"]
    assert local["graph_before"] == {"alice": {"l2_fact": 2}, "local": {"l2_fact": 2}}
    files = local["files"]
    assert "manifest.json" in files and "management/one-memory.sqlite3" in files
    assert {"sdk/governed-v2/graph", "sdk/governed-v2/vector/chroma.sqlite3"} <= set(files)
    assert {"sdk/governed-v2/cache.sqlite3", "sdk/governed-v2/history.sqlite3"} <= set(files)
    # SQLite sidecars are folded into their database by the backup API.
    assert not [name for name in files if name.endswith(("-wal", "-shm"))]


def test_the_manifest_records_backend_space_sdk_users_and_counts(local) -> None:
    manifest = local["manifest"]
    assert manifest["format"] == "memhub-backup" and manifest["schema_version"] == 2
    assert manifest["backend"] == {
        "kind": "local",
        "vector": "chroma",
        "graph": "kuzu",
        "vector_prefix": None,
    }
    assert manifest["sdk_version"] == "2.0.15"
    assert manifest["embedding_space"]["state"] == "live"
    assert manifest["embedding_space"]["collection_base"].startswith("memhub_")
    assert manifest["users"] == ["local", "alice"]
    assert manifest["memory_mode"] == "ultra"
    assert manifest["file_count"] == len(manifest["files"])
    assert set(manifest["counts"]) == {"layers", "vector_store", "graph", "export"}


def test_the_backup_is_owner_only_and_stays_inside_the_home(local) -> None:
    assert local["modes"] == ["0o700", "0o600"]
    assert local["outside"] == [422, {"detail": "backup_target_outside_data_dir"}]
    assert local["again"] == [409, {"detail": "backup_target_exists"}]


def test_the_size_is_shown_before_the_backup(local) -> None:
    status, estimate = local["estimate"]
    assert status == 200
    assert estimate["memories_included"] is True
    assert estimate["bytes"] >= local["backup"][1]["bytes"] * 0.5
    assert estimate["free_bytes"] > 0


def test_writes_are_paused_briefly_and_resume(local) -> None:
    status, body = local["during"]
    assert status == 200, body
    assert body["pause_ms"] < 30_000
    assert local["write_gate_after_backup"] is False
    assert local["queued_completed"] == 20


def test_a_backup_taken_while_writes_are_queued_is_consistent(local) -> None:
    status, body = local["restore_during"]
    assert status == 200, body
    verification = body["verification"]
    assert verification["vector_store"] == verification["layers"] == "verified"
    assert verification["mismatches"] == []
    counts = local["during"][1]["counts"]["layers"]
    assert _layer_counts(local["after_during"])["local/l1_raw"] == counts["local"]["l1_raw"]


def test_wipe_and_restore_bring_every_memory_back(local) -> None:
    assert all(not value for value in local["wiped"].values())
    status, body = local["restore_full"]
    assert status == 200, body
    assert body["backup_schema_version"] == 2 and body["memories_included"] is True
    assert body["verification"] == {
        "vector_store": "verified",
        "layers": "verified",
        "layers_reason": None,
        "graph": "verified",
        "mismatches": [],
    }
    assert local["after"] == local["before"]
    assert local["graph_restored"] == local["graph_before"]
    assert ".memhub-previous" in body["previous_backup_path"]


def test_search_works_after_the_restore(local) -> None:
    assert any("raw turn 2 about gardening" in hit for hit in local["raw_hit"])
    assert any("green tea" in hit for hit in local["fact_hit"])


def test_a_count_mismatch_rolls_back_to_the_previous_data(local) -> None:
    assert local["restore_mismatch"] == [422, {"detail": "backup_restore_counts_mismatch"}]
    assert local["after_mismatch"] == local["before"]


def test_a_damaged_backup_is_refused_before_anything_changes(local) -> None:
    assert local["restore_damaged"] == [422, {"detail": "backup_restore_verify_failed"}]


def test_a_failure_in_the_middle_of_a_restore_leaves_the_previous_data(local) -> None:
    assert local["restore_failure"] == [422, {"detail": "backup_restore_replace_failed"}]
    assert local["after_failure"] == local["before"]
    assert local["journal_after_failure"] is False


def test_an_old_backup_restores_as_before_and_says_it_holds_no_memories(local) -> None:
    status, body = local["restore_old"]
    assert status == 200, body
    assert body["backup_schema_version"] == 1
    assert body["memories_included"] is False
    assert body["note"] == "management_database_only_no_memories"
    assert body["previous_backup_path"].endswith(".sqlite3")
    assert local["after_old"] == local["before"]


def test_a_corrupt_graph_in_a_backup_is_quarantined_and_rebuilt(local) -> None:
    status, body = local["restore_corrupt"]
    assert status == 200, body
    assert body["verification"]["graph"] == "quarantined"
    assert body["verification"]["vector_store"] == body["verification"]["layers"] == "verified"
    assert body["graph_rebuild_scheduled"] is True
    assert local["after_corrupt"] == local["before"]
    assert len(local["quarantined"]) == 1
    assert local["graph_build_scheduled"]["state"] == "running"
    assert set(local["graph_build_scheduled"]["users"]) == {"local", "alice"}
    after = local["graph_build_after"]
    assert after["state"] == "succeeded"
    assert local["graph_rebuilt"] == local["graph_before"]


def test_a_qdrant_backup_exports_the_memories_through_the_sdk(remote) -> None:
    status, body = remote["backup"]
    assert status == 200, body
    assert body["backend"]["kind"] == "remote_vector"
    manifest = remote["manifest"]
    assert manifest["contents"]["export"] == "export/memories.jsonl"
    assert manifest["export"]["embeddings_included"] is False
    assert manifest["counts"]["vector_store"] is None
    exported = manifest["counts"]["export"]
    before = _layer_counts(remote["before"])
    for user in ("local", "alice"):
        assert exported[user]["l1_raw"] == before[f"{user}/l1_raw"] == 3
    lines = remote["export_lines"]
    assert len(lines) == 6
    assert {line["user_id"] for line in lines} == {"local", "alice"}
    assert all(line["layer"] == "l1_raw" for line in lines)
    assert all("qdrant turn" in line["memory"]["content"] for line in lines)
    assert remote["estimate"][1]["export_bytes"] > 0


def test_restoring_memories_onto_qdrant_is_refused_until_the_sdk_can(remote) -> None:
    assert remote["restore_qdrant"] == [422, {"detail": "backup_restore_remote_sdk_unsupported"}]
    assert remote["capabilities"]["restore"] == {
        "state": "refused",
        "code": "backup_restore_remote_sdk_unsupported",
    }
    assert remote["capabilities"]["backup"]["state"] == "works"


def test_a_backup_never_restores_across_backends(remote) -> None:
    assert remote["chroma_backup"] == 200
    assert remote["restore_local_on_qdrant"] == [
        422,
        {"detail": "backup_restore_backend_mismatch"},
    ]
    assert remote["restore_qdrant_on_chroma"] == [
        422,
        {"detail": "backup_restore_backend_mismatch"},
    ]
    assert remote["after_refusals"] == remote["before"]


def test_the_files_on_disk_are_owner_only(local) -> None:
    backup = Path(local["_home"]) / "data" / "backups" / "full.memhub-backup"
    for path in [backup, *backup.rglob("*")]:
        bits = stat.S_IMODE(path.stat().st_mode)
        assert bits == (0o700 if path.is_dir() else 0o600), (path, oct(bits))
