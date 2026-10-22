"""Read-only projection of HY's Kuzu graph for MemHub's existing graph UI.

This module is one of the two explicit graph architecture exceptions.  The
public SDK does not expose graph projection yet, so it may use the client's
private graph handle and loop thread.  It must remain read-only and must not
grow memory, vector, coding, prompt, or governance responsibilities.
"""

from __future__ import annotations

from typing import Any, cast

from pydantic import BaseModel, ConfigDict


class HyGraphNode(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    node_id: str
    content: str
    layer: str | None = None
    confidence: float | None = None


class HyGraphEdge(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_node_id: str
    target_node_id: str
    relation: str
    weight: float | None = None


class HyGraphSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    nodes: tuple[HyGraphNode, ...] = ()
    edges: tuple[HyGraphEdge, ...] = ()
    # False when HY was never consulted, which is not the same as HY having
    # induced no relations.
    consulted: bool = True


def read_graph_snapshot(
    client: object,
    user_id: str,
    agent_id: str,
    limit: int,
) -> HyGraphSnapshot:
    raw_nodes, raw_edges = _read_graph_snapshot(client, user_id, agent_id, limit)
    return HyGraphSnapshot(
        nodes=tuple(
            HyGraphNode(
                node_id=str(row[0]),
                content=str(row[1]),
                layer=str(row[2]) if row[2] is not None else None,
                confidence=_safe_float(row[3]),
            )
            for row in raw_nodes
            if len(row) >= 4 and row[0] and row[1]
        ),
        edges=tuple(
            HyGraphEdge(
                source_node_id=str(row[0]),
                target_node_id=str(row[1]),
                relation=str(row[2]),
                weight=_safe_float(row[3]),
            )
            for row in raw_edges
            if len(row) >= 4 and row[0] and row[1] and row[2]
        ),
    )


def _read_graph_snapshot(
    client: object,
    user_id: str,
    agent_id: str,
    limit: int,
) -> tuple[list[list[object]], list[list[object]]]:
    """Read the graph through the SDK handles until a public API exists."""
    concrete = cast(Any, client)
    # Keep the existing architecture gate unchanged; this module is the named
    # graph-only exception while the rest of MemHub remains on public handles.
    graph_store = getattr(concrete, "_graph_store")  # noqa: B009
    if graph_store is None:
        return ([], [])
    loop_thread = getattr(concrete, "_loop_thread")  # noqa: B009
    # Kuzu rejects a prepared statement that is handed a parameter its text
    # never references, so a wildcard read must not bind agent_id at all.
    params: dict[str, object] = {"user_id": user_id}
    if agent_id != "*":
        params["agent_id"] = agent_id
    safe_limit = max(1, min(int(limit), 1_000))
    memory_agent = "" if agent_id == "*" else " AND m.agent_id = $agent_id"
    related_agent = (
        "" if agent_id == "*" else " AND a.agent_id = $agent_id AND b.agent_id = $agent_id"
    )
    source_agent = "" if agent_id == "*" else " AND a.agent_id = $agent_id"
    nodes = (
        loop_thread.run(
            graph_store._run(
                f"""
            MATCH (m:Memory)
            WHERE m.user_id = $user_id{memory_agent}
              AND m.status = 'active'
            RETURN m.node_id, m.content, m.layer, m.confidence
            LIMIT {safe_limit}
            """,
                params,
            )
        )
        or []
    )
    related_edges = (
        loop_thread.run(
            graph_store._run(
                f"""
            MATCH (a:Memory)-[r:RELATED_TO]->(b:Memory)
            WHERE a.user_id = $user_id{related_agent}
              AND b.user_id = $user_id
              AND a.status = 'active' AND b.status = 'active'
            RETURN a.node_id, b.node_id, r.relation_type, r.weight
            LIMIT {safe_limit * 4}
            """,
                params,
            )
        )
        or []
    )
    derived_edges = (
        loop_thread.run(
            graph_store._run(
                f"""
            MATCH (a:Memory)-[:DERIVED_FROM]->(v:VdbRef)
            WHERE a.user_id = $user_id{source_agent}
              AND a.status = 'active'
            RETURN a.node_id, v.node_id, 'derived_from', 1.0
            LIMIT {safe_limit * 4}
            """,
                params,
            )
        )
        or []
    )
    topic_nodes = (
        loop_thread.run(
            graph_store._run(
                f"""
            MATCH (m:Memory)-[:TAGGED_WITH]->(t:Topic)
            WHERE m.user_id = $user_id{memory_agent}
              AND m.status = 'active'
            RETURN DISTINCT t.topic_id, t.name, 'TOPIC', 1.0
            LIMIT {safe_limit}
            """,
                params,
            )
        )
        or []
    )
    tagged_edges = (
        loop_thread.run(
            graph_store._run(
                f"""
            MATCH (m:Memory)-[:TAGGED_WITH]->(t:Topic)
            WHERE m.user_id = $user_id{memory_agent}
              AND m.status = 'active'
            RETURN m.node_id, t.topic_id, 'tagged_with', 1.0
            LIMIT {safe_limit * 4}
            """,
                params,
            )
        )
        or []
    )
    return (
        [*nodes, *topic_nodes],
        [*related_edges, *derived_edges, *tagged_edges],
    )


def _safe_float(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None
