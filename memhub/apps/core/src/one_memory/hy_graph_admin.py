"""Kuzu graph-width inspection and explicit user-directed administration.

This module is the second graph architecture exception.  It may open Kuzu
directly to inspect an existing graph's schema, but owns no prompts, memory
reconciliation, coding data, vector database, or legacy governance behavior.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from importlib import import_module
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from one_memory.hy_runtime_config import HY_MEMORY_NAMESPACE
from one_memory.model_profiles import ModelProfileSnapshot, ProviderKind


class GraphWidthError(RuntimeError):
    """Base error for graph-width inspection and administration."""


class GraphWidthDecision(StrEnum):
    """What to do when an existing graph disagrees with the embedding width."""

    KEEP_EXISTING = "keep_existing"
    REBUILD_WITH_NEW = "rebuild_with_new"
    EXPORT_FIRST = "export_first"


class GraphWidthMismatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    stored_width: int
    expected_width: int
    graph_path: str


class GraphWidthOutcome(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    archived_path: str | None = None
    exported_path: str | None = None


class GraphWidthMismatchError(GraphWidthError):
    """Raised instead of silently archiving a live graph."""

    def __init__(self, mismatch: GraphWidthMismatch) -> None:
        super().__init__(
            f"graph_width_mismatch:stored={mismatch.stored_width}"
            f":expected={mismatch.expected_width}"
        )
        self.mismatch = mismatch


def graph_store_enabled() -> bool:
    """Whether HY should open the Kuzu graph store."""
    raw = os.environ.get("ONE_MEMORY_GRAPH_STORE", "").strip().lower()
    if not raw:
        return False
    return raw != "none"


def inspect_graph_width_mismatch(config: Any) -> GraphWidthMismatch | None:
    """Return a mismatch without renaming or deleting anything."""
    db_path = Path(config.graph_store.db_path)
    expected = getattr(getattr(config, "embedder", None), "embedding_dims", None)
    return _inspect_graph_width(db_path, expected)


def inspect_profile_graph_width(
    profile: ModelProfileSnapshot,
    *,
    api_key: str | None,
    data_dir: Path,
) -> GraphWidthMismatch | None:
    """Inspect the graph path and width selected by the active profile."""
    del api_key
    return _inspect_graph_width(
        _profile_graph_path(data_dir),
        _profile_embedding_width(profile),
    )


def decide_profile_graph_width(
    profile: ModelProfileSnapshot,
    *,
    api_key: str | None,
    data_dir: Path,
    decision: GraphWidthDecision,
) -> GraphWidthOutcome:
    """Apply the user's decision to the active profile's graph."""
    del api_key
    config = _GraphAdminConfig(
        graph_store=_GraphStoreConfig(str(_profile_graph_path(data_dir))),
        embedder=_EmbeddingWidthConfig(_profile_embedding_width(profile)),
    )
    return apply_graph_width_decision(
        config,
        decision,
        export_root=data_dir.parent,
    )


def apply_graph_width_decision(
    config: Any,
    decision: GraphWidthDecision,
    *,
    export_root: Path | None = None,
) -> GraphWidthOutcome:
    """Act only on a current mismatch and only as explicitly requested."""
    mismatch = inspect_graph_width_mismatch(config)
    if mismatch is None:
        return GraphWidthOutcome()
    if decision is GraphWidthDecision.REBUILD_WITH_NEW:
        archive = retire_mismatched_graph_store(config)
        return GraphWidthOutcome(archived_path=str(archive) if archive is not None else None)
    if decision is GraphWidthDecision.EXPORT_FIRST:
        if export_root is None:
            raise GraphWidthError("export_root_required")
        return GraphWidthOutcome(
            exported_path=str(export_graph_store(config, export_root=export_root))
        )
    return GraphWidthOutcome()


def export_graph_store(config: Any, *, export_root: Path) -> Path:
    """Copy the graph outside its data directory, atomically."""
    db_path = Path(config.graph_store.db_path)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    destination = export_root / f"one-memory-graph-{stamp}"
    staging = destination.with_name(f"{destination.name}.partial")
    sources = [db_path] + [
        sibling
        for suffix in (".wal", ".shadow", ".tmp")
        if (sibling := db_path.with_name(f"{db_path.name}{suffix}")).exists()
    ]
    try:
        staging.mkdir(parents=True)
        for source in sources:
            shutil.copy2(source, staging / source.name)
        staging.rename(destination)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    logging.getLogger(__name__).info(
        "exported hy graph store to %s (%d files)",
        destination,
        len(sources),
    )
    return destination


def retire_mismatched_graph_store(config: Any) -> Path | None:
    """Archive a mismatched Kuzu graph only after the user chooses rebuild."""
    db_path = Path(config.graph_store.db_path)
    if not db_path.exists():
        return None
    expected = getattr(getattr(config, "embedder", None), "embedding_dims", None)
    if not expected:
        return None
    stored = _graph_embedding_width(db_path)
    if stored is None or stored == int(expected):
        return None
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    archive = db_path.with_name(f"{db_path.name}.dim{stored}.{stamp}.bak")
    db_path.rename(archive)
    for suffix in (".wal", ".shadow", ".tmp"):
        sibling = db_path.with_name(f"{db_path.name}{suffix}")
        if sibling.exists():
            sibling.rename(archive.with_name(f"{archive.name}{suffix}"))
    logging.getLogger(__name__).warning(
        "retired hy graph store with embedding width %s (model expects %s): %s",
        stored,
        expected,
        archive.name,
    )
    return archive


_GRAPH_WIDTH_CACHE: dict[str, int | None] = {}
_GRAPH_WIDTH_LOCK = threading.Lock()


def _graph_embedding_width(db_path: Path) -> int | None:
    """Read the Memory embedding width directly from Kuzu's schema."""
    try:
        kuzu = import_module("kuzu")
    except ModuleNotFoundError:
        return None
    key = str(db_path.resolve())
    with _GRAPH_WIDTH_LOCK:
        if key in _GRAPH_WIDTH_CACHE:
            return _GRAPH_WIDTH_CACHE[key]
    try:
        database = kuzu.Database(str(db_path), read_only=True)
    except Exception:
        return None
    width: int | None = None
    try:
        connection = kuzu.Connection(database)
        result = connection.execute("CALL TABLE_INFO('Memory') RETURN *")
        while result.has_next():
            row = result.get_next()
            if len(row) < 3 or row[1] != "embedding":
                continue
            match = re.search(r"\[(\d+)\]", str(row[2]))
            width = int(match.group(1)) if match else None
            break
    except Exception:
        width = None
    with _GRAPH_WIDTH_LOCK:
        _GRAPH_WIDTH_CACHE[key] = width
    return width


def _inspect_graph_width(
    db_path: Path,
    expected_width: object,
) -> GraphWidthMismatch | None:
    if not db_path.exists() or not expected_width:
        return None
    stored = _graph_embedding_width(db_path)
    if stored is None or stored == int(expected_width):
        return None
    return GraphWidthMismatch(
        stored_width=int(stored),
        expected_width=int(expected_width),
        graph_path=str(db_path),
    )


def _profile_graph_path(data_dir: Path) -> Path:
    name = "graph" if graph_store_enabled() else "graph-disabled"
    return data_dir / "hy-memory" / HY_MEMORY_NAMESPACE / name


def _profile_embedding_width(profile: ModelProfileSnapshot) -> int | None:
    if profile.embedding_dimensions is not None:
        return profile.embedding_dimensions
    if (profile.embedding_provider_kind or profile.provider_kind) is ProviderKind.LOCAL_ONNX:
        from one_memory.embedding_assets import BUILTIN_EMBEDDING_DIMENSIONS

        return BUILTIN_EMBEDDING_DIMENSIONS
    return None


@dataclass(frozen=True)
class _GraphStoreConfig:
    db_path: str


@dataclass(frozen=True)
class _EmbeddingWidthConfig:
    embedding_dims: int | None


@dataclass(frozen=True)
class _GraphAdminConfig:
    graph_store: _GraphStoreConfig
    embedder: _EmbeddingWidthConfig
