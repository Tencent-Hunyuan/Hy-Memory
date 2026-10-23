"""An embedding profile must not send the OpenAI `dimensions` request parameter.

Regression: MemHub fed the profile's store width into the SDK's request-side
``embedding_dims``, so every embedding call carried ``dimensions``. Models that
do not accept it (e.g. BAAI/bge-m3) replied HTTP 400, so no extraction ran.
"""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from hy_memory.core.embed_service import EmbedService

from one_memory.hy_sdk_runtime import build_sdk_config
from one_memory.model_profiles import ModelProfileSnapshot, ProviderKind

WIDTH = 1024


class _Recorder(BaseHTTPRequestHandler):
    bodies: list[dict[str, Any]] = []

    def do_POST(self) -> None:  # noqa: N802 - http.server callback name
        length = int(self.headers.get("content-length", "0"))
        self.bodies.append(json.loads(self.rfile.read(length) or b"{}"))
        payload = {
            "object": "list",
            "model": "bge-m3",
            "data": [{"object": "embedding", "index": 0, "embedding": [0.0] * WIDTH}],
            "usage": {"prompt_tokens": 1, "total_tokens": 1},
        }
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        return


def _serve() -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address[:2]
    return server, f"http://{host}:{port}/v1"


def _profile(base_url: str) -> ModelProfileSnapshot:
    return ModelProfileSnapshot(
        profile_id="dims",
        revision=1,
        provider_kind=ProviderKind.OPENAI_COMPATIBLE,
        base_url=base_url,
        model="generation-model",
        embedding_provider_kind=ProviderKind.OPENAI_COMPATIBLE,
        embedding_base_url=base_url,
        embedding_model="BAAI/bge-m3",
        embedding_dimensions=WIDTH,
        timeout_seconds=30,
        max_concurrency=1,
        context_window=8192,
        has_api_key=True,
    )


def test_config_keeps_store_width_but_not_request_dimensions(tmp_path: Path) -> None:
    server, base_url = _serve()
    try:
        config = build_sdk_config(_profile(base_url), api_key="k", data_dir=tmp_path)
    finally:
        server.shutdown()

    assert config.embedder.embedding_dims is None
    assert config.vector_store.embedding_dims == WIDTH


def test_embedding_request_omits_dimensions(tmp_path: Path) -> None:
    _Recorder.bodies.clear()
    server, base_url = _serve()
    try:
        config = build_sdk_config(_profile(base_url), api_key="k", data_dir=tmp_path)
        vector = asyncio.run(EmbedService(config).embed("hello"))
    finally:
        server.shutdown()

    assert len(vector) == WIDTH
    assert len(_Recorder.bodies) == 1
    assert "dimensions" not in _Recorder.bodies[0]
