"""Validate the identity envelope supplied by the authenticated local broker."""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from fastapi import HTTPException, Request

from one_memory.database_access import main_database
from one_memory.matters.models import Matter, MatterContext
from one_memory.matters.store import MatterStore
from one_memory.memory_governance import MemoryGovernanceStore

TOOLS = frozenset({"om_recall", "om_ask", "om_conversations", "om_matters", "om_diagnostic_trace"})
_ENVELOPE_HEADERS = frozenset(
    {
        "x-one-memory-read-context",
        "x-one-memory-installation-id",
        "x-one-memory-installation-revision",
        "x-one-memory-allowed-scopes",
        "x-one-memory-allowed-tools",
    }
)
_BROKER_ROUTES = {
    ("POST", "/api/v1/recall"): "om_recall",
    ("POST", "/api/v1/memories/ask"): "om_ask",
    ("POST", "/api/v2/memories/ask"): "om_ask",
    ("POST", "/api/v2/memories/ask/stream"): "om_ask",
    ("GET", "/api/v1/conversations/search"): "om_conversations",
    ("POST", "/api/v1/matters/context/search"): "om_matters",
    ("POST", "/api/v1/diagnostics/trace"): "om_diagnostic_trace",
    ("POST", "/api/v1/boundary/peek"): "om_recall",
    ("POST", "/api/v1/boundary/ack"): "om_recall",
}
_CONCRETE_CALL_EVENT_DELIVERY = re.compile(
    r"/api/v1/call-events/[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}/delivery",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ExternalMemoryAccess:
    installation_id: str
    revision: int
    scopes: frozenset[str]
    tools: frozenset[str]
    host_id: str | None = None

    def require(self, tool: str, scopes: frozenset[str]) -> None:
        if tool not in self.tools or not scopes or not scopes <= self.scopes:
            raise HTTPException(403, "external_recall_scope_denied")

    def readable_source_ids(self, database_path: Path) -> frozenset[str]:
        sources = frozenset(
            scope.removeprefix("source:") for scope in self.scopes if scope.startswith("source:")
        )
        if not sources:
            raise HTTPException(403, "external_source_permission_required")
        with main_database(database_path).read() as db:
            revoked = {
                str(row[0])
                for row in db.execute(
                    "SELECT source_id FROM source_grants "
                    "WHERE user_id='local' AND revoked_at IS NOT NULL"
                )
            }
        return sources - revoked

    def allows_matter(self, matter: Matter, governance: MemoryGovernanceStore) -> bool:
        if (
            not matter.agent_access
            or matter.availability != "ready"
            or matter.source_engine != "hy-memory"
        ):
            return False
        support_ids: set[str] = set()
        for claim in matter.claims:
            if not claim.memory_refs:
                return False
            for reference in claim.memory_refs:
                memory = governance.get(reference.memory_id, user_id="local")
                if (
                    memory is None
                    or memory.revision != reference.revision
                    or memory.status != reference.status
                    or self.scopes.isdisjoint(memory.scopes)
                ):
                    return False
                support_ids.update(memory.evidence_ids)
        # Titles, boundaries and uncertainties can depend on uncited anchors.
        # Referenced memories can also have support not quoted by this matter.
        with main_database(governance.database_path).read() as db:
            row = db.execute(
                "SELECT dependencies FROM matter_versions WHERE matter_id=? AND version=?",
                (matter.matter_id, matter.version),
            ).fetchone()
            if row is None:
                return False
            dependencies = json.loads(row[0])
            if not dependencies:
                return False
            for evidence_id in set(dependencies) | support_ids:
                source = db.execute(
                    "SELECT e.source_id FROM evidence e JOIN source_grants g "
                    "USING(user_id,source_id) WHERE e.user_id='local' AND e.evidence_id=? "
                    "AND g.revoked_at IS NULL",
                    (evidence_id,),
                ).fetchone()
                if source is None or f"source:{source[0]}" not in self.scopes:
                    return False
        return bool(matter.claims)


def external_access(headers: Mapping[str, str]) -> ExternalMemoryAccess | None:
    # Starlette Headers.items() preserves duplicate HTTP fields; .get() does not.
    values: dict[str, str] = {}
    for name, value in headers.items():
        key = name.lower()
        if key not in _ENVELOPE_HEADERS and key != "x-one-memory-host-id":
            continue
        if key in values:
            raise HTTPException(403, "external_installation_permission_required")
        values[key] = value
    if not values:
        return None
    host_id = values.pop("x-one-memory-host-id", None)
    if values.keys() != _ENVELOPE_HEADERS or values["x-one-memory-read-context"] != "external":
        raise HTTPException(403, "external_installation_permission_required")
    try:
        if host_id is not None and not re.fullmatch(r"[a-z0-9][a-z0-9:._-]{0,127}", host_id):
            raise ValueError
        installation = values["x-one-memory-installation-id"]
        if str(UUID(installation)) != installation:
            raise ValueError
        revision_text = values["x-one-memory-installation-revision"]
        if not re.fullmatch(r"[1-9][0-9]{0,15}", revision_text):
            raise ValueError
        revision = int(revision_text)
        raw_scopes = values["x-one-memory-allowed-scopes"]
        raw_tools = values["x-one-memory-allowed-tools"]
        if revision > 2**53 - 1 or len(raw_scopes) > 20_000 or len(raw_tools) > 1_000:
            raise ValueError
        scopes, tools = json.loads(raw_scopes), json.loads(raw_tools)
        if not isinstance(scopes, list) or not scopes or len(scopes) > 128:
            raise ValueError
        if not isinstance(tools, list) or not tools or len(tools) > len(TOOLS):
            raise ValueError
        if any(not isinstance(item, str) or item not in TOOLS for item in tools):
            raise ValueError
        if any(
            not isinstance(item, str)
            or (
                item != "all-apps"
                and not re.fullmatch(
                    r"workspace:v1:[0-9a-f]{64}|source:[a-z0-9][a-z0-9-]{0,127}", item
                )
            )
            for item in scopes
        ):
            raise ValueError
        if len(set(scopes)) != len(scopes) or len(set(tools)) != len(tools):
            raise ValueError
        return ExternalMemoryAccess(
            installation, revision, frozenset(scopes), frozenset(tools), host_id
        )
    except (ValueError, TypeError, RecursionError):
        raise HTTPException(403, "external_installation_permission_required") from None


def require_external_endpoint(request: Request) -> None:
    """Mount after local_api_guard: expose only explicitly allowed broker routes."""
    access = external_access(request.headers)
    if access is None:
        return
    if request.method == "POST" and request.url.path in {
        "/api/v1/context/search",
        "/api/v2/context/search",
    }:
        # The route resolves each requested lane against its original grant.
        # Passing this envelope gate does not authorize either lane by itself.
        if access.tools.isdisjoint({"om_recall", "om_conversations"}):
            raise HTTPException(403, "external_recall_scope_denied")
        return
    tool = (
        "om_recall"
        if request.method == "POST" and _CONCRETE_CALL_EVENT_DELIVERY.fullmatch(request.url.path)
        else _BROKER_ROUTES.get((request.method, request.url.path))
    )
    if tool is None:
        raise HTTPException(403, "external_endpoint_denied")
    if tool not in access.tools:
        raise HTTPException(403, "external_recall_scope_denied")
    if tool in {"om_conversations", "om_matters"}:
        access.require(tool, frozenset({"all-apps"}))
        if not any(scope.startswith("source:") for scope in access.scopes):
            raise HTTPException(403, "external_source_permission_required")


def search_external_matter_contexts(
    access: ExternalMemoryAccess,
    query: str,
    *,
    matters: MatterStore,
    governance: MemoryGovernanceStore,
) -> list[MatterContext]:
    """Apply installation bounds before rendering quotes or consuming the result limit."""
    access.require("om_matters", frozenset({"all-apps"}))
    access.readable_source_ids(governance.database_path)
    terms = query.casefold().split()
    found: list[MatterContext] = []
    for matter in matters.list():
        if not access.allows_matter(matter, governance):
            continue
        searchable = f"{matter.title} {matter.purpose} {' '.join(matter.tags)}".casefold()
        if not all(term in searchable for term in terms):
            continue
        context = matters.context(matter.matter_id, character_budget=20_000)
        current = matters.get(matter.matter_id)
        if (
            context.availability != "ready"
            or current.version != matter.version
            or context.version != matter.version
            or not access.allows_matter(current, governance)
        ):
            continue
        found.append(context)
        if len(found) == 3:
            break
    # Rendering a later item may race a revocation affecting an earlier one.
    released = []
    for context in found:
        current = matters.get(context.matter_id)
        if current.version == context.version and access.allows_matter(current, governance):
            released.append(context)
    return released
