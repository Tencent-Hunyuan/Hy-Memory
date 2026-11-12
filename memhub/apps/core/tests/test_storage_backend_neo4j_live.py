"""A real Neo4j/Memgraph server, when one is named: probe, then an ultra SDK client.

Skipped unless `ONE_MEMORY_TEST_NEO4J_URL` (and, if the server needs them,
`ONE_MEMORY_TEST_NEO4J_USER` / `ONE_MEMORY_TEST_NEO4J_PASSWORD`) name a server
this test may write a graph to. CI has none: `test_storage_backends` covers
the same paths with a stand-in probe.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from one_memory.hy_sdk_runtime import acquire_client, builtin_retrieval_snapshot
from one_memory.storage_backend_probe import probe_storage
from one_memory.storage_backends import ResolvedStorage

pytestmark = [
    pytest.mark.live,
    pytest.mark.serial,
    pytest.mark.skipif(
        not os.environ.get("ONE_MEMORY_TEST_NEO4J_URL"),
        reason="set ONE_MEMORY_TEST_NEO4J_URL to a Neo4j/Memgraph server",
    ),
]


def _storage() -> ResolvedStorage:
    return ResolvedStorage(
        graph_provider=os.environ.get("ONE_MEMORY_TEST_NEO4J_PROVIDER", "neo4j"),  # type: ignore[arg-type]
        vector_provider="chroma",
        graph_url=os.environ["ONE_MEMORY_TEST_NEO4J_URL"],
        graph_username=os.environ.get("ONE_MEMORY_TEST_NEO4J_USER"),
        graph_password=os.environ.get("ONE_MEMORY_TEST_NEO4J_PASSWORD"),
    )


def test_the_probe_reaches_the_server() -> None:
    probe = probe_storage(_storage())
    assert probe.ok, probe.model_dump()
    assert probe.graph.state == "ok"


def test_an_ultra_client_opens_its_graph_on_the_server(tmp_path: Path) -> None:
    lease = acquire_client(
        builtin_retrieval_snapshot(),
        api_key=None,
        data_dir=tmp_path,
        mode="ultra",
        storage=_storage(),
    )
    try:
        graph = lease.client._graph_store  # noqa: SLF001 - the graph-view exception
        assert graph is not None
        assert type(graph).__name__ == "Neo4jGraphStore"
        assert getattr(graph, "_available", True)
    finally:
        lease.close()
