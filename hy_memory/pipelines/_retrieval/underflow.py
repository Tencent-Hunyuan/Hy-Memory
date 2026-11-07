"""Fill unused normal-result slots without displacing existing evidence.

``limit`` bounds the **normal** channel only (L2/L3/L4/L5). Side layers
(``SIDE_LAYERS``: L0 profile, L6 schema, L7 intention) are separate channels
with their own quotas and are never counted against ``limit``; the returned
list may therefore be longer than ``limit``. This mirrors the main read path,
which assembles ``normal_results + schema_results + intention_results``.
"""
import math
from typing import Any, Dict, List


SIDE_LAYERS = frozenset({"l0_basic_info", "l6_schema", "l7_intention"})


def normal_count(memories: List[Dict[str, Any]]) -> int:
    return sum(m.get("layer") not in SIDE_LAYERS for m in memories)


def fill_normal_slots(original, candidates, *, limit, min_score):
    """Preserve original entries verbatim; side channels never consume slots.

    At most ``limit - normal_count(original)`` candidates are appended, so the
    normal channel ends at ``limit`` entries. ``original`` is kept as-is, which
    means the result length is ``normal_count(original) + len(extra) +
    len(original side entries)`` and can exceed ``limit`` when side layers are
    present. Do not truncate the result to ``limit``: that would drop evidence
    the caller already selected.

    Candidate scores have already passed the complete retrieval pipeline. This
    function enforces the output gate once more and never invents a score.
    """
    remaining = max(0, limit - normal_count(original))
    if not remaining:
        return list(original)
    seen = {m.get("memory_id") for m in original}
    extra = []
    for memory in candidates:
        if len(extra) >= remaining:
            break
        mid = memory.get("memory_id")
        if not mid or mid in seen or memory.get("layer") in SIDE_LAYERS:
            continue
        score = memory.get("score")
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            continue
        if not math.isfinite(score) or score < min_score:
            continue
        seen.add(mid)
        extra.append(memory)
    out = list(original)
    first_side = next((i for i, m in enumerate(out) if m.get("layer") in SIDE_LAYERS), len(out))
    out[first_side:first_side] = extra
    return out
