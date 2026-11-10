"""Kuzu graph-width inspection and explicit user-directed administration.

This module is the second graph architecture exception.  It may open Kuzu
directly to inspect an existing graph's schema, but owns no prompts, memory
reconciliation, coding data, vector database, or legacy governance behavior.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
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


def effective_memory_mode(configured: str = "pro") -> str:
    """The SDK mode clients are built with: the setting, unless dev forces ultra.

    `ONE_MEMORY_GRAPH_STORE` (any value but empty or `none`) predates the
    setting and keeps working for development: it forces ultra whatever the
    configured mode. Without it the configured mode (default pro) applies.
    """
    return "ultra" if graph_store_enabled() else configured


def inspect_graph_width_mismatch(config: Any) -> GraphWidthMismatch | None:
    """Return a mismatch without renaming or deleting anything.

    Only a Kuzu graph is a file MemHub can inspect. A Neo4j/Memgraph graph
    (`storage_backends`) is a server: there is nothing to check, archive or
    export here, so it never reports a mismatch.
    """
    provider = str(getattr(config.graph_store, "provider", "kuzu") or "kuzu").lower()
    if provider != "kuzu":
        return None
    db_path = Path(config.graph_store.db_path)
    expected = getattr(getattr(config, "embedder", None), "embedding_dims", None)
    return _inspect_graph_width(db_path, expected)


def inspect_profile_graph_width(
    profile: ModelProfileSnapshot,
    *,
    api_key: str | None,
    data_dir: Path,
    mode: str | None = None,
    graph_dir: str = "graph",
) -> GraphWidthMismatch | None:
    """Inspect the graph path and width selected by the active profile.

    `graph_dir` is the live embedding space's graph (`embedding_spaces`).
    """
    del api_key
    return _inspect_graph_width(
        _profile_graph_path(data_dir, mode, graph_dir),
        _profile_embedding_width(profile),
    )


def decide_profile_graph_width(
    profile: ModelProfileSnapshot,
    *,
    api_key: str | None,
    data_dir: Path,
    decision: GraphWidthDecision,
    mode: str | None = None,
    graph_dir: str = "graph",
) -> GraphWidthOutcome:
    """Apply the user's decision to the active profile's graph."""
    del api_key
    config = _GraphAdminConfig(
        graph_store=_GraphStoreConfig(str(_profile_graph_path(data_dir, mode, graph_dir))),
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


#: The `kuzu.Database` resource bounds of the probe child: the SDK's
#: (`kuzu_database_kwargs`, hy-memory >= 2.0.15), from the same environment
#: (`MEMORY_GRAPH_BUFFER_POOL_MB`, `MEMORY_GRAPH_MAX_THREADS`) and defaults,
#: so the probe replays the WAL in the buffer pool the SDK will open it with
#: and not in Kuzu's default of most of physical memory. Kept inline: the
#: child does not import the SDK.
GRAPH_PROBE_DATABASE_KWARGS_SOURCE = """
import os
def _probe_env_int(key, default):
    try:
        return int(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default
database_kwargs = {}
_pool_mb = _probe_env_int("MEMORY_GRAPH_BUFFER_POOL_MB", 512)
if _pool_mb > 0:
    database_kwargs["buffer_pool_size"] = max(_pool_mb, 64) << 20
_threads = _probe_env_int("MEMORY_GRAPH_MAX_THREADS", 4)
if _threads > 0:
    database_kwargs["max_num_threads"] = _threads
"""

#: The body of the graph-open probe child (`graph_open_guard.GraphOpenProbe`).
#: Run as `python -I -c <this> <graph> <alarm_s>` from source; the frozen
#: binary runs the same through `--graph-open-probe` (`run_graph_open_probe`).
#: It imports nothing of MemHub, so a broken graph costs only this process.
GRAPH_OPEN_PROBE_SOURCE = (
    GRAPH_PROBE_DATABASE_KWARGS_SOURCE
    + """
import re, signal, sys
if hasattr(signal, "alarm"):
    # Default SIGALRM action ends the process in the kernel, GIL or not.
    signal.alarm(max(1, int(sys.argv[2])))
read_only = len(sys.argv) > 3 and sys.argv[3] == "ro"
try:
    import kuzu
except ImportError:
    raise SystemExit(3)
database = kuzu.Database(sys.argv[1], read_only=read_only, **database_kwargs)
connection = kuzu.Connection(database)
connection.execute("RETURN 1")
width = ""
try:
    result = connection.execute("CALL TABLE_INFO('Memory') RETURN *")
    while result.has_next():
        row = result.get_next()
        if len(row) >= 3 and row[1] == "embedding":
            match = re.search(r"\\[(\\d+)\\]", str(row[2]))
            width = match.group(1) if match else ""
            break
except Exception:
    width = ""
connection.close()
database.close()
print("width=" + width, flush=True)
"""
)


def run_graph_open_probe(arguments: list[str]) -> int:
    """`one-memory --graph-open-probe <graph> <alarm_s> [ro]`: open, then close.

    Read-write and not lazy, like the SDK's own open, so the WAL replay and
    checkpoint happen here, in a process that may be killed for it.
    """
    if len(arguments) not in (2, 3):
        return 2
    namespace: dict[str, object] = {"__name__": "__graph_open_probe__"}
    saved = sys.argv
    sys.argv = ["--graph-open-probe", *arguments]
    try:
        exec(compile(GRAPH_OPEN_PROBE_SOURCE, "<graph-open-probe>", "exec"), namespace)
    except SystemExit as stop:
        return int(stop.code or 0) if not isinstance(stop.code, str) else 1
    except Exception:  # noqa: BLE001 - the exit status is the whole answer
        return 1
    finally:
        sys.argv = saved
    return 0


_GRAPH_WIDTH_CACHE: dict[str, int | None] = {}
_GRAPH_WIDTH_LOCK = threading.Lock()


def forget_graph_widths() -> None:
    """Drop every remembered width: an embedding switch moved the graph."""
    with _GRAPH_WIDTH_LOCK:
        _GRAPH_WIDTH_CACHE.clear()


def remember_graph_width(db_path: Path, width: int | None) -> None:
    """Cache a width the startup probe already read (`graph_open_guard`)."""
    with _GRAPH_WIDTH_LOCK:
        _GRAPH_WIDTH_CACHE[str(db_path.resolve())] = width


def _graph_embedding_width(db_path: Path) -> int | None:
    """Read the Memory embedding width from Kuzu's schema, never in Core.

    A damaged graph can freeze or kill the process that opens it, and an open
    here would also be a second `kuzu.Database` beside the SDK's in this
    process. So the width is read by the same bounded child as the startup
    probe (read-only here) and cached; the startup probe primes the cache.
    """
    key = str(db_path.resolve())
    with _GRAPH_WIDTH_LOCK:
        if key in _GRAPH_WIDTH_CACHE:
            return _GRAPH_WIDTH_CACHE[key]
    from one_memory.graph_open_guard import GraphOpenProbe, default_probe_command

    result = GraphOpenProbe(
        command=lambda path, alarm_s: default_probe_command(path, alarm_s, read_only=True),
        timeout_s=30,
    )(db_path)
    width = result.width if result.ok else None
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


def live_graph_path(data_dir: Path, graph_dir: str | None) -> Path:
    """The graph `build_sdk_config` gives an ultra client of this space.

    Same root and directory as the SDK config's `graph_store.db_path`, without
    building a client config (that needs the profile's secrets).
    """
    return data_dir.resolve() / "hy-memory" / HY_MEMORY_NAMESPACE / (graph_dir or "graph")


def _profile_graph_path(data_dir: Path, mode: str | None = None, graph_dir: str = "graph") -> Path:
    ultra = mode == "ultra" if mode is not None else graph_store_enabled()
    name = graph_dir if ultra else "graph-disabled"
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
