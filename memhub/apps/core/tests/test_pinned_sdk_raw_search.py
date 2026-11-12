"""MemHub's raw search against the hy-memory wheel it pins, not the repo source.

The suite's pythonpath puts the repository root first, so `import hy_memory`
in a test reads the repo's `hy_memory/`, which can be ahead of the pinned
release. Search kwargs the release does not accept then pass every test and
fail every raw search in a packaged build (a `statuses` argument did exactly
that against 2.0.13). These tests run in a child interpreter started with
`-I`, so `hy_memory` resolves to the wheel installed in the Core venv, and
drive the real routes over a real lite SDK client with a deterministic local
embedder (no model, no network, no ONNX assets).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap

import pytest

from tests.test_sdk_api_surface import SDK_SEARCH_PARAMETERS

pytestmark = pytest.mark.integration

_CHILD = textwrap.dedent(
    r'''
    import asyncio
    import hashlib
    import inspect
    import json
    import math
    import re
    import sys
    from importlib.metadata import version
    from pathlib import Path

    import hy_memory
    from hy_memory import HyMemoryClient

    from one_memory.hy_runtime_config import HY_MEMORY_VERSION

    home = Path(sys.argv[1])
    wheel = Path(hy_memory.__file__).resolve()
    assert "site-packages" in wheel.parts, f"not the installed wheel: {wheel}"
    assert version("hy-memory") == HY_MEMORY_VERSION, version("hy-memory")
    parameters = sorted(
        name
        for name, parameter in inspect.signature(HyMemoryClient.search).parameters.items()
        if name not in {"self", "query"} and parameter.kind is not inspect.Parameter.VAR_KEYWORD
    )
    var_keyword = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in inspect.signature(HyMemoryClient.search).parameters.values()
    )

    from httpx import ASGITransport, AsyncClient
    from pydantic import SecretStr

    import one_memory.api as api
    from one_memory.api import create_app
    from one_memory.config import Settings
    from one_memory.embedding_assets import BUILTIN_EMBEDDING_DIMENSIONS
    from one_memory.hy_sdk_runtime import (
        LocalOnnxEmbeddingService,
        SdkClientHandle,
        builtin_retrieval_snapshot,
    )
    from one_memory.secrets import InMemorySecretStore

    WORD = re.compile(r"\w+")

    class HashedEmbedder:
        """Bag of words and CJK bigrams, hashed: texts sharing words are near."""

        dimensions = BUILTIN_EMBEDDING_DIMENSIONS

        def encode(self, texts):
            vectors = []
            for text in texts:
                vector = [0.0] * self.dimensions
                for word in WORD.findall(text.casefold()):
                    grams = [word]
                    if not word.isascii():
                        grams += [word[i : i + 2] for i in range(len(word) - 1)]
                    for gram in grams:
                        digest = hashlib.sha256(gram.encode()).digest()
                        vector[int.from_bytes(digest[:4], "big") % self.dimensions] += 1.0
                norm = math.sqrt(sum(value * value for value in vector)) or 1.0
                vectors.append([value / norm for value in vector])
            return vectors

    LocalOnnxEmbeddingService._embedder = HashedEmbedder()

    async def lite_has_no_model(_app):
        return False

    api._recall_model_ready = lite_has_no_model

    async def main():
        settings = Settings(
            data_dir=home / "data",
            source_home=home,
            api_token=SecretStr("pinned-sdk"),
            imports_enabled=False,
            automatic_memory_processing=False,
            live_sync_enabled=False,
        )
        app = create_app(settings, secret_store=InMemorySecretStore())
        app.state.memory_mode = lambda: "lite"
        handle = SdkClientHandle(
            builtin_retrieval_snapshot(),
            api_key=None,
            data_dir=home / "data",
            mode="lite",
        )
        result = {"parameters": parameters, "var_keyword": var_keyword}
        async with app.router.lifespan_context(app):
            app.state.sdk_client_handle = handle
            client = await handle.client()
            for session, messages in (
                (
                    "s-hike",
                    [
                        ("user", "我叫小林"),
                        ("assistant", "你好小林"),
                        ("user", "I love hiking mountain trails at dawn"),
                        ("assistant", "Noted, early hikes."),
                    ],
                ),
                (
                    "s-tea",
                    [
                        ("user", "my favourite drink is green tea"),
                        ("assistant", "Green tea, noted."),
                    ],
                ),
            ):
                written = client.add(
                    [{"role": role, "content": content} for role, content in messages],
                    user_id="local",
                    source="hermes",
                    session_id=session,
                )
                assert written.get("success", True), written
            listed = client.list_memories(user_id="local", layers=["l1_raw"], limit=10)
            rows = listed.get("vdb", listed)["memories"]
            tea = next(row for row in rows if "green tea" in row["content"])
            # What pro leaves behind: the raw it already processed is shadow.
            client._loop_thread.run(
                client._vector_store.update_payload(tea["memory_id"], {"status": "shadow"})
            )
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer pinned-sdk"},
            ) as http:
                for name, body in (
                    ("hike_turn", {"query": "hiking trails mountain", "raw_unit": "turn"}),
                    ("tea_turn", {"query": "favourite green tea drink", "raw_unit": "turn"}),
                    ("tea_raw", {"query": "favourite green tea drink"}),
                ):
                    response = await http.post(
                        "/api/v2/context/search", json={"kinds": ["raw"], "limit": 5, **body}
                    )
                    result[name] = {"status": response.status_code, "body": response.json()}
                response = await http.post(
                    "/api/v1/recall", json={"query": "favourite green tea drink"}
                )
                result["recall"] = {"status": response.status_code, "body": response.json()}
        await handle.close()
        print("RESULT " + json.dumps(result, ensure_ascii=False))

    asyncio.run(main())
    '''
)


@pytest.fixture(scope="module")
def pinned(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("pinned-sdk")
    script = root / "scripts" / "child.py"
    script.parent.mkdir()
    script.write_text(_CHILD, encoding="utf-8")
    home = root / "home"
    home.mkdir()
    environment = {
        **os.environ,
        "ANONYMIZED_TELEMETRY": "False",
        "ONE_MEMORY_DATA_DIR": str(home / "data"),
    }
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


def test_the_fake_sdk_accepts_exactly_the_pinned_search_parameters(pinned) -> None:
    # The route tests' stand-in rejects what the pinned SDK rejects.
    assert pinned["var_keyword"] is False
    assert sorted(SDK_SEARCH_PARAMETERS) == pinned["parameters"]


def test_lite_turn_search_returns_semantic_hits_without_the_literal_query(pinned) -> None:
    hike = pinned["hike_turn"]
    assert hike["status"] == 200, hike
    items = hike["body"]["items"]
    assert items, hike["body"]
    top = items[0]
    # No turn contains "hiking trails mountain"; the hit still comes back,
    # shown by its closest turn and carrying the hit's score.
    assert "hiking mountain trails" in top["content"]
    assert "小林" not in top["content"]
    assert top["turn_index"] == 1 and top["turn_count"] == 2
    assert [item["score"] for item in items] == sorted(
        (item["score"] for item in items), reverse=True
    )


def test_lite_search_and_recall_include_shadow_raws(pinned) -> None:
    for name in ("tea_turn", "tea_raw"):
        result = pinned[name]
        assert result["status"] == 200, result
        assert any("green tea" in item["content"] for item in result["body"]["items"]), result
    recall = pinned["recall"]
    assert recall["status"] == 200, recall
    assert recall["body"]["mode"] == "raw"
    assert any("green tea" in hit["content"] for hit in recall["body"]["hits"]), recall
