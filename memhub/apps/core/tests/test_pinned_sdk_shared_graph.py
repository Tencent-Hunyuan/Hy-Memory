"""Two ultra clients MemHub builds in one Core share one Kuzu database.

Core holds the read handle's client and the write coordinator's client at the
same time, both on the live space's graph. Up to hy-memory 2.0.14 each opened
its own `kuzu.Database` on that file: Kuzu's file lock is per process, so the
second open was not refused, and the two databases' checkpoints overwrote
each other (nodes lost on reopen). Since 2.0.15 the SDK keeps one
`kuzu.Database` per file per process, shared by reference count, and bounds
its buffer pool and threads; this runs against the pinned release (2.0.16).
This drives the real wheel in a child interpreter started with `-I` (the
suite's pythonpath would otherwise import the repo's `hy_memory/`), over a
temp data dir and a local OpenAI-compatible embedding endpoint.
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
    import json
    import os
    import sys
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from importlib.metadata import version
    from pathlib import Path

    import hy_memory

    from one_memory.hy_runtime_config import HY_MEMORY_VERSION

    home = Path(sys.argv[1])
    wheel = Path(hy_memory.__file__).resolve()
    assert "site-packages" in wheel.parts, f"not the installed wheel: {wheel}"
    assert version("hy-memory") == HY_MEMORY_VERSION, version("hy-memory")

    import kuzu
    from hy_memory.data import graph_store_kuzu
    from hy_memory.models.memory import MemoryLayer, MemoryNode
    from pydantic import SecretStr

    from one_memory.embedding_spaces import EmbeddingSpaceStore
    from one_memory.hy_graph_admin import live_graph_path
    from one_memory.model_profiles import (
        ModelCapabilities,
        ModelProfileInput,
        ModelProfileStore,
        ProviderKind,
    )
    from one_memory.sdk_read_handle import ActiveProfileReadHandle
    from one_memory.sdk_write_processor import SdkWriteCoordinator
    from one_memory.secrets import InMemorySecretStore

    WIDTH = 8

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 - http.server's name
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            texts = body["input"] if isinstance(body["input"], list) else [body["input"]]
            payload = json.dumps(
                {
                    "object": "list",
                    "model": body["model"],
                    "data": [
                        {"object": "embedding", "index": i, "embedding": [0.5] * WIDTH}
                        for i, _ in enumerate(texts)
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

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/v1"

    data_dir = home / "data"
    data_dir.mkdir(parents=True)
    database = data_dir / "one-memory.sqlite3"
    secrets = InMemorySecretStore()
    profiles = ModelProfileStore(database)
    spaces = EmbeddingSpaceStore(database)
    created = profiles.create(
        ModelProfileInput(
            name="ultra",
            provider_kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url=url,
            model="chat",
            embedding_provider_kind=ProviderKind.OPENAI_COMPATIBLE,
            embedding_base_url=url,
            embedding_model="stand-in",
            embedding_dimensions=WIDTH,
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

    def node(content):
        memory = MemoryNode(
            user_id="local",
            agent_id="a",
            session_id="s",
            layer=MemoryLayer.L6_SCHEMA,
            content=content,
        )
        memory._graph_embedding = [0.1] * WIDTH
        return memory

    def memory_count(store):
        result = store._conn.execute("MATCH (m:Memory) RETURN count(m)")
        return int(result.get_next()[0])

    async def main():
        ultra = lambda: "ultra"  # noqa: E731
        read = ActiveProfileReadHandle(
            profiles=profiles,
            secrets=secrets,
            data_dir=data_dir,
            memory_mode=ultra,
            spaces=spaces,
        )
        write = SdkWriteCoordinator(
            profiles=profiles,
            secrets=secrets,
            data_dir=data_dir,
            transport_factory=lambda _profile: None,
            sedimentation_enabled=lambda: False,
            memory_mode=ultra,
            spaces=spaces,
        )
        result = {}
        read_client = await (await read.current_handle()).client()
        async with write.use_client() as write_client:
            # The read handle bootstrapped the live space from the active profile.
            graph = live_graph_path(data_dir, spaces.require_live().graph_dir)
            real = os.path.realpath(graph)
            stores = [read_client._graph_store, write_client._graph_store]
            assert read_client is not write_client
            assert stores[0] is not stores[1]
            result["mode"] = [read_client.mode, write_client.mode]
            result["available"] = [store._available for store in stores]
            result["db_paths"] = [os.path.realpath(store._db_path) for store in stores]
            result["live_graph"] = real
            result["same_database"] = stores[0]._db is stores[1]._db
            result["refs"] = graph_store_kuzu._SHARED_DBS[real].refs
            result["open_kwargs"] = stores[0]._open_kwargs
            result["open_graph_paths"] = graph_store_kuzu.open_graph_paths()
            # Each writes through its own client; each reads the other's.
            write_client._loop_thread.run(stores[1].upsert_memory_node(node("from the writer")))
            read_client._loop_thread.run(stores[0].upsert_memory_node(node("from the reader")))
            result["counts_while_open"] = [
                read_client._loop_thread.run(graph_store_kuzu._run_in_kuzu_pool(memory_count, s))
                for s in stores
            ]
            await read.close()
            result["refs_after_one_close"] = graph_store_kuzu._SHARED_DBS[real].refs
            result["writer_still_available"] = stores[1]._available
        await write.close()
        result["shared_after_both_close"] = real in graph_store_kuzu._SHARED_DBS
        reopened = kuzu.Database(str(graph), read_only=True)
        connection = kuzu.Connection(reopened)
        rows = connection.execute("MATCH (m:Memory) RETURN m.content")
        contents = []
        while rows.has_next():
            contents.append(rows.get_next()[0])
        connection.close()
        reopened.close()
        result["contents_after_reopen"] = sorted(contents)
        print("RESULT " + json.dumps(result, ensure_ascii=False))

    try:
        asyncio.run(main())
    finally:
        server.shutdown()
    """
)


@pytest.fixture(scope="module")
def shared(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("pinned-sdk-graph")
    script = root / "scripts" / "child.py"
    script.parent.mkdir()
    script.write_text(_CHILD, encoding="utf-8")
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
        timeout=240,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-4000:]
    [line] = [line for line in completed.stdout.splitlines() if line.startswith("RESULT ")]
    return json.loads(line.removeprefix("RESULT "))


def test_the_read_and_write_clients_open_the_live_graph_once(shared) -> None:
    assert shared["mode"] == ["ultra", "ultra"]
    assert shared["available"] == [True, True]
    assert shared["db_paths"] == [shared["live_graph"]] * 2
    assert shared["same_database"] is True
    assert shared["refs"] == 2
    # One entry per live store, both on the one file.
    assert shared["open_graph_paths"] == [shared["live_graph"]] * 2


def test_the_shared_database_is_bounded_by_the_sdk_defaults(shared) -> None:
    assert shared["open_kwargs"] == {"buffer_pool_size": 512 << 20, "max_num_threads": 4}


def test_writes_of_both_clients_survive_closing_them(shared) -> None:
    assert shared["counts_while_open"] == [2, 2]
    # Closing the read client leaves the writer on the same database.
    assert shared["refs_after_one_close"] == 1
    assert shared["writer_still_available"] is True
    assert shared["shared_after_both_close"] is False
    assert shared["contents_after_reopen"] == ["from the reader", "from the writer"]
