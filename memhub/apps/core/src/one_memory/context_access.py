"""Resolve existing read grants before invoking either context path."""

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from one_memory.context_models import ContextSearchRequest
from one_memory.external_memory_access import ExternalMemoryAccess
from one_memory.memory_server.store_binding import store_for

#: The revoked-source query, per engine.
#:
#: Identical text today because it uses no placeholders and reads by position,
#: which both engines support. Kept as a named constant so that adding a
#: parameter later forces the `?`/`%s` difference to be considered here rather
#: than being introduced silently at the call site.
REVOKED_SOURCES_SQL = (
    "SELECT source_id FROM source_grants WHERE user_id='local' AND revoked_at IS NOT NULL"
)
ACTIVE_SOURCES_SQL = (
    "SELECT source_id FROM source_grants WHERE user_id='local' AND revoked_at IS NULL"
)


@dataclass(frozen=True)
class ContextAuthority:
    raw_allowed: bool
    memory_allowed: bool
    raw_sources: frozenset[str] | None
    memory_scopes: frozenset[str] | None


def resolve_context_authority(
    request: ContextSearchRequest,
    access: ExternalMemoryAccess | None,
    database_path: Path,
    *,
    deadline: float,
) -> ContextAuthority:
    if time.monotonic() >= deadline:
        raise TimeoutError
    raw_requested = "raw" in request.kinds
    memory_requested = "memory" in request.kinds
    explicit = frozenset(request.raw_source_ids) if request.raw_source_ids is not None else None
    if access is None:
        return ContextAuthority(raw_requested, memory_requested, explicit, None)

    memory_scopes = frozenset(
        scope for scope in access.scopes if scope == "all-apps" or scope.startswith("workspace:v1:")
    )
    memory_allowed = memory_requested and "om_recall" in access.tools and bool(memory_scopes)
    declared_sources = frozenset(
        scope.removeprefix("source:") for scope in access.scopes if scope.startswith("source:")
    )
    generic_raw = access.host_id == "generic-local-mcp" and "raw-imported-all" in access.scopes
    raw_allowed = (
        raw_requested
        and "om_conversations" in access.tools
        and "all-apps" in access.scopes
        and (generic_raw if access.host_id == "generic-local-mcp" else bool(declared_sources))
    )
    if explicit is not None and (
        not raw_allowed or (not generic_raw and not explicit <= declared_sources)
    ):
        raise HTTPException(403, "external_context_scope_denied")

    raw_sources: frozenset[str] = frozenset()
    if raw_allowed:
        database: Any = store_for(database_path)
        with database.read(busy_timeout_ms=max(0, int((deadline - time.monotonic()) * 1000))) as db:
            # `set_progress_handler` is sqlite3-only; MySQL bounds the query
            # with the checkout's busy timeout instead. Skipped when absent so
            # the same code runs on both engines without a branch per engine.
            if hasattr(db, "set_progress_handler"):
                db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            sources = frozenset(
                str(row[0])
                for row in db.execute(ACTIVE_SOURCES_SQL if generic_raw else REVOKED_SOURCES_SQL)
            )
        if time.monotonic() >= deadline:
            raise TimeoutError
        raw_sources = sources if generic_raw else declared_sources - sources
        if explicit is not None:
            if not explicit <= raw_sources:
                raise HTTPException(403, "external_context_scope_denied")
            raw_sources = explicit
        raw_allowed = bool(raw_sources)
    if not raw_allowed and not memory_allowed:
        raise HTTPException(403, "external_context_scope_denied")
    return ContextAuthority(raw_allowed, memory_allowed, raw_sources, memory_scopes)
