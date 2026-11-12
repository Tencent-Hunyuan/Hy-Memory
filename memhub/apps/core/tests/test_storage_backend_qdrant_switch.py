"""An embedding switch on a Qdrant vector store, end to end, against the pinned SDK.

hy-memory 2.0.16 re-embeds Qdrant collections (Tencent-Hunyuan/Hy-Memory#422).
As in `test_storage_backend_qdrant`, the child interpreter answers every
`QdrantClient(host=...)` with one shared in-process Qdrant (`qdrant_client`'s
own local engine): to the SDK, and to its re-embed module, it is one server.
Another home's collection sits on the same server under another prefix.

The rest is real: Core's routes, the setting and its API key, the SDK's
Qdrant store and lite write path, the switch worker (plan, pause, re-embed
through the SDK's own embedding service over HTTP, verify, commit), search on
the new space, the space listing and the cleanup of the retired space. The
neighbour's collection is never listed, read or dropped, and the home's Chroma
files of before the setting are never touched.
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
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from pathlib import Path

    import hy_memory
    import qdrant_client
    from qdrant_client.models import Distance, PointStruct, VectorParams

    home = Path(sys.argv[1])
    assert "site-packages" in Path(hy_memory.__file__).resolve().parts

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
    from one_memory.model_profiles import (
        ModelCapabilities,
        ModelProfileInput,
        ModelProfileStore,
        ProviderKind,
    )
    from one_memory.secrets import InMemorySecretStore

    WORD = re.compile(r"\w+")
    TARGET_WIDTH = 8


    def hashed(text, width):
        vector = [0.0] * width
        for word in WORD.findall(text.casefold()):
            digest = hashlib.sha256(word.encode()).digest()
            vector[int.from_bytes(digest[:4], "big") % width] += 1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


    class HashedEmbedder:
        dimensions = BUILTIN_EMBEDDING_DIMENSIONS

        def encode(self, texts):
            return [hashed(text, self.dimensions) for text in texts]


    LocalOnnxEmbeddingService._embedder = HashedEmbedder()
    embedded = []


    class Embeddings(BaseHTTPRequestHandler):
        # The target model: an OpenAI-compatible `/embeddings`, 8 wide.
        def do_POST(self):  # noqa: N802 - http.server's name
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            texts = body["input"] if isinstance(body["input"], list) else [body["input"]]
            embedded.extend(texts)
            payload = json.dumps(
                {
                    "object": "list",
                    "model": body["model"],
                    "data": [
                        {"object": "embedding", "index": index,
                         "embedding": hashed(text, TARGET_WIDTH)}
                        for index, text in enumerate(texts)
                    ],
                    "usage": {"prompt_tokens": 1, "total_tokens": 1},
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            return


    server = ThreadingHTTPServer(("127.0.0.1", 0), Embeddings)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/v1"


    async def lite_has_no_model(_app):
        return False


    api._recall_model_ready = lite_has_no_model
    secrets = InMemorySecretStore()
    data = home / "data"
    vector_dir = data / "hy-memory" / "governed-v2" / "vector"
    NEIGHBOUR = "mhfeedface_memhub_" + "0" * 16 + "_512"
    result = {}


    def settings():
        return Settings(
            data_dir=data,
            source_home=home,
            api_token=SecretStr("qdrant-switch"),
            imports_enabled=False,
            automatic_memory_processing=False,
            live_sync_enabled=False,
        )


    def collections():
        return sorted(item.name for item in _shared.get_collections().collections)


    def widths(name):
        points, _ = _shared.scroll(collection_name=name, limit=1000, with_vectors=True)
        out = set()
        for point in points:
            vector = point.vector.get("") if isinstance(point.vector, dict) else point.vector
            out.add(len(vector))
        return sorted(out), len(points)


    async def session(scenario):
        app = create_app(settings(), secret_store=secrets)
        async with app.router.lifespan_context(app):
            await app.state.embedding_switch.recover()
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer qdrant-switch"},
                timeout=120,
            ) as http:
                await scenario(app, http)


    async def write(app, user, text, session_id):
        async def run():
            client = await app.state.sdk_client_handle.client()
            written = client.add(
                [{"role": "user", "content": text}, {"role": "assistant", "content": "noted"}],
                user_id=user,
                source="hermes",
                session_id=session_id,
            )
            assert written.get("success", True), written

        await asyncio.create_task(run())


    async def search(http, user, query):
        response = await http.post(
            "/api/v2/context/search",
            json={"kinds": ["raw"], "limit": 5, "query": query, "user_id": user},
        )
        assert response.status_code == 200, response.text
        return [item["content"] for item in response.json()["items"]]


    def target_profile():
        profiles = ModelProfileStore(data / "one-memory.sqlite3")
        created = profiles.create(
            ModelProfileInput(
                name="eight wide",
                provider_kind=ProviderKind.OPENAI_COMPATIBLE,
                base_url=url,
                model="chat",
                embedding_provider_kind=ProviderKind.OPENAI_COMPATIBLE,
                embedding_base_url=url,
                embedding_model="stand-in-8",
                embedding_dimensions=TARGET_WIDTH,
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
        return created


    async def first(app, http):
        await http.put("/api/v1/settings/memory-mode", json={"mode": "lite"})
        await write(app, "local", "the chroma era: I keep bees on the roof", "s-chroma")
        result["chroma_files"] = sorted(path.name for path in vector_dir.iterdir())
        changed = await http.put(
            "/api/v1/settings/storage-backends",
            json={"vector": {"provider": "qdrant", "host": "qdrant.internal",
                             "api_key": "qdrant-secret"}},
        )
        assert changed.status_code == 200, changed.text
        result["prefix"] = changed.json()["vector"]["collection_prefix"]
        result["capabilities"] = {
            name: value["state"] for name, value in changed.json()["capabilities"].items()
        }
        # Another home on the same server.
        _shared.create_collection(
            NEIGHBOUR, vectors_config=VectorParams(size=512, distance=Distance.COSINE)
        )
        _shared.upsert(
            NEIGHBOUR,
            points=[PointStruct(id=1, vector=hashed("neighbour", 512),
                                payload={"user_id": "local", "content": "neighbour"})],
        )
        for number, (user, text) in enumerate(
            [
                ("local", "my cat is called Miso"),
                ("local", "I take the night train to Vienna"),
                ("alice", "alice grows tomatoes on the balcony"),
            ]
        ):
            await write(app, user, text, f"s-{number}")
        result["before"] = collections()
        result["hits_before"] = await search(http, "local", "cat called Miso")

        created = target_profile()
        planned = await http.post(
            "/api/v1/embedding-switches/plan", json={"profile_id": created.profile_id}
        )
        assert planned.status_code == 200, planned.text
        plan = planned.json()
        result["plan"] = {
            key: plan[key] for key in ("needed", "blockers", "total_vectors", "users")
        }
        started = await http.post(
            "/api/v1/embedding-switches",
            json={
                "profile_id": created.profile_id,
                "expected_revision": created.revision,
                "plan_token": plan["plan_token"],
                "requested_by": "cli",
            },
        )
        assert started.status_code == 202, started.text
        job_id = started.json()["job_id"]
        deadline = time.monotonic() + 180
        while True:
            job = (await http.get(f"/api/v1/embedding-switches/{job_id}")).json()
            if job["state"] in ("done", "failed", "cancelled"):
                break
            assert time.monotonic() < deadline, job
            await asyncio.sleep(0.2)
        result["job"] = {key: job[key] for key in ("state", "error_code")}
        result["after"] = collections()
        spaces = (await http.get("/api/v1/embedding-spaces")).json()
        result["spaces"] = {
            "scan_error": spaces["scan_error"],
            "scanned": sorted(
                [item["collection_base"], item["name_dims"], item["state"]]
                for item in spaces["scanned"]
            ),
            "states": {item["space_id"]: item["state"] for item in spaces["spaces"]},
        }
        live = next(item for item in spaces["spaces"] if item["state"] == "live")
        retired = [item for item in spaces["spaces"] if item["state"] == "retired"]
        prefix = result["prefix"]
        result["live_memory"] = widths(f"{prefix}_{live['collection_base']}_{live['name_dims']}")
        result["hits_after"] = await search(http, "local", "cat called Miso")
        result["alice_after"] = await search(http, "alice", "tomatoes balcony")
        result["embedded"] = sorted(set(embedded))
        result["neighbour_after_switch"] = widths(NEIGHBOUR)
        await write(app, "local", "after the switch: I bought a red bicycle", "s-after")
        result["hits_new_write"] = await search(http, "local", "red bicycle")

        # The person's cleanup of the retired space: only this home's collections.
        [old] = retired
        dropped = await http.delete(f"/api/v1/embedding-spaces/{old['space_id']}")
        result["dropped"] = [dropped.status_code, dropped.json().get("space", {}).get("state")]
        result["after_drop"] = collections()
        result["neighbour_after_drop"] = widths(NEIGHBOUR)
        result["chroma_files_after"] = sorted(path.name for path in vector_dir.iterdir())
        result["api_keys"] = sorted({str(call["api_key"]) for call in calls})
        result["old_base"] = old["collection_base"]
        result["new_base"] = live["collection_base"]


    async def main():
        await session(first)
        print("RESULT " + json.dumps(result, ensure_ascii=False))


    try:
        asyncio.run(main())
    finally:
        server.shutdown()
    """
)


@pytest.fixture(scope="module")
def switched(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("qdrant-switch")
    script = root / "scripts" / "child.py"
    script.parent.mkdir()
    script.write_text(_CHILD, encoding="utf-8")
    home = root / "home"
    home.mkdir()
    environment = {
        **os.environ,
        "ANONYMIZED_TELEMETRY": "False",
        "ONE_MEMORY_DATA_DIR": str(home / "data"),
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
    }
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
    return json.loads(line.removeprefix("RESULT "))


def test_the_switch_is_offered_and_planned_on_qdrant(switched) -> None:
    assert switched["capabilities"]["embedding_switch"] == "works"
    assert switched["capabilities"]["embedding_space_scan"] == "works"
    plan = switched["plan"]
    assert plan["needed"] is True and plan["blockers"] == []
    # Three raw turns of two users on Qdrant; the Chroma-era turn is not there.
    assert plan["total_vectors"] == 3 and plan["users"] == 2


def test_the_switch_re_embeds_this_homes_collections_and_commits(switched) -> None:
    assert switched["job"] == {"state": "done", "error_code": None}
    prefix = switched["prefix"]
    new = [name for name in switched["after"] if switched["new_base"] in name]
    assert new and all(name.startswith(f"{prefix}_") for name in new)
    assert f"{prefix}_{switched['new_base']}_8" in new
    # Every raw turn in the new space, 8 wide, embedded by the target model.
    assert switched["live_memory"] == [[8], 3]
    assert any("Miso" in text for text in switched["embedded"])
    assert any("tomatoes" in text for text in switched["embedded"])
    # Search reads the new space; a write after the commit lands there too.
    assert any("Miso" in hit for hit in switched["hits_before"])
    assert any("Miso" in hit for hit in switched["hits_after"])
    assert any("tomatoes" in hit for hit in switched["alice_after"])
    assert any("bicycle" in hit for hit in switched["hits_new_write"])
    # The API key configured for Qdrant is the only one the re-embed used.
    assert switched["api_keys"] == ["qdrant-secret"]


def test_the_space_listing_names_only_this_homes_spaces(switched) -> None:
    spaces = switched["spaces"]
    assert spaces["scan_error"] is None
    bases = {base for base, _dims, _state in spaces["scanned"]}
    assert bases == {switched["old_base"], switched["new_base"]}
    assert all(not base.startswith("mh") for base in bases)
    assert sorted(spaces["states"].values()) == ["live", "retired"]


def test_cleanup_drops_only_the_retired_space_of_this_home(switched) -> None:
    assert switched["dropped"] == [200, "dropped"]
    prefix = switched["prefix"]
    assert not any(switched["old_base"] in name for name in switched["after_drop"])
    assert any(switched["new_base"] in name for name in switched["after_drop"])
    assert all(
        name.startswith(f"{prefix}_") or name == "mhfeedface_memhub_" + "0" * 16 + "_512"
        for name in switched["after_drop"]
    )


def test_a_neighbour_on_the_same_server_and_the_old_chroma_files_are_untouched(
    switched,
) -> None:
    assert switched["neighbour_after_switch"] == [[512], 1]
    assert switched["neighbour_after_drop"] == [[512], 1]
    assert switched["chroma_files_after"] == switched["chroma_files"]
