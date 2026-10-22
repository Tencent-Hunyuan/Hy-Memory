from __future__ import annotations

import gc
from pathlib import Path

import pytest

from one_memory.model_profiles import ModelProfileSnapshot, ProviderKind


def model_snapshot(
    provider: ProviderKind,
    *,
    base_url: str | None,
    has_api_key: bool,
    embedding_provider: ProviderKind | None = None,
    embedding_base_url: str | None = None,
) -> ModelProfileSnapshot:
    return ModelProfileSnapshot(
        profile_id="profile",
        revision=3,
        provider_kind=provider,
        base_url=base_url,
        model="memory-model",
        embedding_model="embedding-model",
        embedding_provider_kind=embedding_provider,
        embedding_base_url=embedding_base_url,
        embedding_dimensions=768,
        timeout_seconds=30,
        max_concurrency=1,
        context_window=8192,
        has_api_key=has_api_key,
    )


def kuzu_graph_with_embedding_width(db_path: Path, width: int) -> None:
    kuzu = pytest.importorskip("kuzu")
    database = kuzu.Database(str(db_path))
    connection = kuzu.Connection(database)
    connection.execute(
        f"CREATE NODE TABLE Memory("
        f"node_id STRING, embedding FLOAT[{width}], PRIMARY KEY(node_id))"
    )
    del connection
    del database
    gc.collect()
