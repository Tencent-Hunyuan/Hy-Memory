"""Resolve extraction history limits without changing shared client config."""

from typing import Any, Optional


def resolve_history_turns(config: Any, override: Optional[int] = None) -> int:
    """Return the per-write limit; zero disables historical context reads."""
    turns = override
    if turns is None:
        turns = getattr(getattr(config, "extractor", None), "history_turns", None)
    if turns is None:
        return 5
    if isinstance(turns, bool) or not isinstance(turns, int) or turns < 0:
        raise ValueError("history_turns must be a non-negative integer")
    return turns
