"""Stable answer-context formatting for normal, evolved, and capsule memories."""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Dict, Iterable, Optional


def format_memory_time(value: Any) -> str:
    """Format a memory timestamp without silently dropping time information."""
    if value in (None, ""):
        return "Unknown"
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        # 调用方存进来的是 int(naive_datetime.timestamp())（session_episode.py:284、
        # :318，_graph/rerank.py:816、:896），与 datetime 分支指向同一个瞬间。
        # 按 UTC 渲染会让同一条记忆在 capsule 的 Timeline 里显示成前一天 ——
        # UTC+8 的 2026-01-01 00:30 会渲染成 "2025-12-31 16:30:00 UTC"，而同一块
        # Memories 里那条记忆渲染成 "2026-01-01 00:30:00"。按本地时间渲染，与
        # naive datetime 分支（本仓库约定 naive = 本地，见 reconciler._as_aware）
        # 保持同一口径。
        dt = datetime.fromtimestamp(float(value))
    else:
        raw = str(value).strip()
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return raw
    rendered = dt.isoformat(sep=" ", timespec="seconds")
    return rendered.replace("+00:00", " UTC")


def _entry_time(entry: Dict[str, Any]) -> Any:
    memory_at = entry.get("memory_at")
    return memory_at if memory_at is not None else entry.get("gmt_created")


def _item_time(item: Dict[str, Any], node: Any = None) -> Any:
    """Read the logical memory time from either reader metadata or its node."""
    when = item.get("memory_at")
    if when is None:
        when = item.get("gmt_created")
    if when is not None:
        return when
    when = getattr(node, "memory_at", None) if node is not None else None
    if when is None and node is not None:
        when = getattr(node, "gmt_created", None)
    return when


def format_evolved_memory(
    chain: Iterable[Dict[str, Any]],
    *,
    fallback_content: str = "",
    fallback_time: Any = None,
) -> str:
    """Render one evolution chain as a natural capsule memory entry."""
    versions = [dict(item) for item in (chain or []) if item]
    if not versions:
        return format_plain_memory(fallback_content, fallback_time)
    latest = versions[0]
    latest_time = _entry_time(latest)
    if latest_time is None:
        latest_time = fallback_time
    lines = [
        f"- Current: {latest.get('content') or fallback_content}",
        f"  When: {format_memory_time(latest_time)}",
    ]
    previous = versions[1:]
    if previous:
        lines.append("  History:")
        for item in previous:
            lines.extend([
                f"  - {item.get('content') or ''}",
                f"    When: {format_memory_time(_entry_time(item))}",
            ])
    return "\n".join(lines)


def format_plain_memory(content: str, when: Any = None) -> str:
    return "\n".join([
        f"- Memory: {(content or '').strip()}",
        f"  When: {format_memory_time(when)}",
    ])


def _already_formatted(content: str) -> bool:
    stripped = (content or "").lstrip()
    return stripped.startswith((
        "Episode:", "Memory:", "Current:", "Related memories:",
        "Matched memories:", "- Memory:", "- Current:",
    ))


def _content_key(value: Any) -> str:
    """Exact semantic-record key, ignoring only labels and whitespace."""
    text = str(value or "").strip()
    text = re.sub(r"^(?:Episode|Memory|Current):\s*", "", text, flags=re.I)
    return " ".join(text.split()).casefold()


def _item_id(item: Dict[str, Any]) -> str:
    node = item.get("node")
    return str(
        item.get("node_id")
        or item.get("memory_id")
        or getattr(node, "node_id", "")
        or ""
    )


def _display_content(item: Dict[str, Any], node: Any = None) -> str:
    return str(
        item.get("_display_content")
        or item.get("content")
        or getattr(node, "content", "")
        or ""
    ).strip()


def _display_chain(
    item: Dict[str, Any], chain: Iterable[Dict[str, Any]], content: str,
) -> list[Dict[str, Any]]:
    versions = [dict(entry) for entry in (chain or []) if entry]
    if versions and item.get("_display_content"):
        versions[0]["content"] = content
    return versions


def _dedupe_nested_items(
    items: Iterable[Dict[str, Any]],
    *,
    seen_ids: set[str],
    seen_contents: set[str],
) -> list[Dict[str, Any]]:
    """Remove cycles/exact repeats before rendering nested graph/capsule members."""
    out = []
    for original in items or []:
        item = dict(original)
        node = item.get("node")
        item_id = _item_id(item)
        content = item.get("content") or getattr(node, "content", "") or ""
        content_key = _content_key(content)
        if ((item_id and item_id in seen_ids)
                or (content_key and content_key in seen_contents)):
            continue
        if item_id:
            seen_ids.add(item_id)
        if content_key:
            seen_contents.add(content_key)
        for field in ("_graph_merged_items", "related_memories"):
            if item.get(field):
                item[field] = _dedupe_nested_items(
                    item[field], seen_ids=seen_ids, seen_contents=seen_contents,
                )
        out.append(item)
    return out


def format_final_content(item: Dict[str, Any]) -> str:
    """Make every final search item self-contained with its logical time."""
    node = item.get("node")
    content = _display_content(item, node)
    capsule_memories = item.get("_capsule_memories")
    if item.get("source") == "episode_capsule" and capsule_memories is not None:
        episode_content = (getattr(node, "content", "") or content).strip()
        root_id = _item_id(item)
        members_deduped = _dedupe_nested_items(
            capsule_memories,
            seen_ids={root_id} if root_id else set(),
            seen_contents={_content_key(episode_content)} if episode_content else set(),
        )
        members = [_format_nested_item(member) for member in members_deduped]
        members = [member for member in members if member]
        if item.get("_episode_render_mode") == "l2_visible":
            title = str(item.get("_episode_title") or "").strip()
            timeline = list(item.get("_episode_timeline") or [])
            lines = []
            if title:
                lines.append(f"Episode context: {title}")
            if timeline:
                lines.append("Timeline:")
                for entry in timeline:
                    lines.append(f"- {format_memory_time(entry.get('memory_at'))}")
            if members:
                lines.append("Memories:")
                lines.extend(members)
            return "\n".join(lines)
        rendered = format_episode_header({**item, "content": episode_content})
        if members:
            rendered += "\nMatched memories:\n" + "\n".join(members)
        return rendered
    if item.get("source") == "session_episode_rrf":
        return format_episode_header(item)
    bundle_items = item.get("_bundle_items")
    if bundle_items:
        members = [format_retrieval_item(member) for member in bundle_items]
        return "Matched memories:\n" + "\n".join(
            member for member in members if member
        )
    graph_items = item.get("_graph_merged_items") or item.get("related_memories")
    if graph_items:
        root_id = _item_id(item)
        # `_graph_merged_items` contains the canonical seed as well as admitted
        # expansions. Do not pre-mark the seed as seen: that used to drop the
        # directly retrieved evidence and return only its neighbors.
        graph_items = list(graph_items)
        if (
            root_id
            and not any(_item_id(member) == root_id for member in graph_items)
            and node is not None
        ):
            graph_items.insert(0, {
                "node_id": root_id, "node": node,
                "content": getattr(node, "content", "") or content,
            })
        graph_items = _dedupe_nested_items(
            graph_items,
            seen_ids=set(),
            seen_contents=set(),
        )
        members = [_format_nested_item(member) for member in graph_items]
        members = [member for member in members if member]
        if members:
            heading = (
                "Memory timeline:"
                if item.get("_timeline_member_ids") is not None
                else "Related memories:"
            )
            return heading + "\n" + "\n".join(members)
    if _already_formatted(content):
        return content
    chain = item.get("evolution_chain") or []
    if chain:
        return format_evolved_memory(
            chain,
            fallback_content=content,
            fallback_time=_item_time(item, node),
        )
    return "\n".join([
        f"Memory: {content}",
        f"When: {format_memory_time(_item_time(item, node))}",
    ])


def _format_nested_item(item: Dict[str, Any]) -> str:
    """Format one member inside a capsule or graph composite."""
    graph_items = item.get("_graph_merged_items") or item.get("related_memories")
    if graph_items:
        nested = [_format_nested_item(member) for member in graph_items]
        return "- Related memories:\n" + "\n".join(
            "  " + line
            for block in nested if block
            for line in block.splitlines()
        )
    return format_retrieval_item(item)


def build_rerank_text(item: Dict[str, Any]) -> str:
    """Build semantic rerank text without response labels or timestamps.

    Response rendering is deliberately excluded from this function. In
    particular, ``_episode_render_mode`` must not alter reranker input: otherwise
    comparing ``full`` with ``l2_visible`` changes both presentation and top-k,
    making the result unstable and the A/B impossible to interpret.
    """
    # A hidden clause may admit a long parent L2 into the pool. Rerank against
    # the matched clause so the same dilution that caused the miss does not
    # immediately remove it again. Final rendering still uses parent content.
    retrieval_clause = str(item.get("_retrieval_clause") or "").strip()
    if retrieval_clause:
        return retrieval_clause
    node = item.get("node")
    content = (item.get("content") or getattr(node, "content", "") or "").strip()
    capsule_memories = item.get("_capsule_memories")
    if item.get("source") == "episode_capsule" and capsule_memories is not None:
        member_texts = [build_rerank_text(member) for member in capsule_memories]
        return "\n\n".join([content, *(text for text in member_texts if text)])
    bundle_items = item.get("_bundle_items")
    if bundle_items:
        return "\n\n".join(
            text for text in (build_rerank_text(member) for member in bundle_items) if text
        )
    graph_items = item.get("_graph_merged_items") or item.get("related_memories")
    if graph_items:
        graph_items = list(graph_items)
        root_id = _item_id(item)
        if (
            root_id
            and not any(_item_id(member) == root_id for member in graph_items)
            and node is not None
        ):
            graph_items.insert(0, {
                "node_id": root_id, "node": node,
                "content": getattr(node, "content", "") or content,
            })
        texts = []
        seen_ids: set[str] = set()
        seen_contents: set[str] = set()
        for member in _dedupe_nested_items(
            graph_items, seen_ids=seen_ids, seen_contents=seen_contents,
        ):
            text = build_rerank_text(member)
            if text:
                texts.append(text)
        return "\n\n".join(texts)
    chain = item.get("evolution_chain") or []
    if chain:
        texts = [str(entry.get("content") or "").strip() for entry in chain]
        return "\n\n".join(text for text in texts if text) or content
    return content


def format_retrieval_item(item: Dict[str, Any]) -> str:
    """Format one internal reader item, preserving an evolution chain if present."""
    node = item.get("node")
    content = _display_content(item, node)
    when: Optional[Any] = _item_time(item, node)
    chain = item.get("evolution_chain") or []
    if chain:
        return format_evolved_memory(
            _display_chain(item, chain, content),
            fallback_content=content, fallback_time=when,
        )
    return format_plain_memory(content, when)


def format_episode_header(item: Dict[str, Any]) -> str:
    node = item.get("node")
    content = (item.get("content") or getattr(node, "content", "") or "").strip()
    when = _item_time(item, node)
    return "\n".join([
        f"Episode: {content}",
        f"When: {format_memory_time(when)}",
    ])
