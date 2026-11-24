"""Bounded, ID-independent ordering of equal-score lexical search results."""
import json
import logging

logger = logging.getLogger(__name__)


def memory_content_key(item):
    """Semantic tie key for memory hits; clone UUIDs never decide order."""
    node = item.get("node")
    custom = getattr(node, "custom", None) or {}
    return (
        str(item.get("content") or getattr(node, "content", "") or ""),
        str(getattr(node, "layer", "") or item.get("layer") or ""),
        str(getattr(node, "session_id", "") or ""),
        str(getattr(node, "memory_at", "") or ""),
        json.dumps(custom.get("source_session_ids") or [], ensure_ascii=False),
        json.dumps(getattr(node, "tags", None) or [], ensure_ascii=False),
    )


def content_key(point):
    payload = point.payload or {}
    return (
        -float(point.score), str(payload.get("content") or ""),
        str(payload.get("layer") or ""), str(payload.get("session_id") or ""),
        str(payload.get("memory_at") or ""),
        json.dumps(payload.get("tags") or [], ensure_ascii=False, sort_keys=True),
    )


def bounded_keyword_ties(fetch, top_k):
    """Fetch a lookahead, expanding only when a tie crosses the cutoff.

    At most 4*k+1 points are fetched per request (three requests). If a
    larger tie still straddles the final page, omit that unresolved group:
    choosing an arbitrary subset would reintroduce ID-dependent results.
    Distinct-score ranking and every returned score remain unchanged.
    """
    if top_k <= 0:
        return []
    cap = 4 * top_k + 1
    limit = top_k + 1
    while True:
        points = list(fetch(limit))
        if len(points) < limit or len(points) <= top_k:
            return sorted(points, key=content_key)[:top_k]
        boundary = float(points[top_k - 1].score)
        if float(points[-1].score) < boundary:
            return sorted(points, key=content_key)[:top_k]
        if limit >= cap:
            higher = [p for p in points if float(p.score) > boundary]
            if not higher:
                # The whole lookahead is one tie group.  Omitting it would
                # turn a valid query into an empty result; fall back to the
                # same ID-independent semantic order used by the other paths.
                logger.warning(
                    "[keyword-ties] entirely tied at score=%s after %s points; "
                    "using semantic tie order", boundary, cap,
                )
                return sorted(points, key=content_key)[:top_k]
            logger.warning(
                "[keyword-ties] truncated tie at score=%s after %s points; "
                "returning only strictly higher scores", boundary, cap,
            )
            return sorted(higher, key=content_key)[:top_k]
        limit = min(2 * limit - 1, cap)
