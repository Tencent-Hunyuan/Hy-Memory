"""Public types and errors for exact memory-ID lookup."""

from dataclasses import dataclass
import math
from typing import Literal, Sequence


MemoryKind = Literal["chat", "experience", "fact"]
MemoryVariant = Literal["working", "coding"]


class AmbiguousMemoryIdError(LookupError):
    """An ID exists in more than one public memory namespace."""

    def __init__(self, memory_id: str, namespaces: Sequence[str]):
        self.memory_id = memory_id
        self.namespaces = tuple(namespaces)
        joined = ", ".join(self.namespaces)
        super().__init__(
            f"memory ID {memory_id!r} is ambiguous across namespaces: {joined}; "
            "pass kind and/or variant to disambiguate"
        )


class NativeRetrievalError(RuntimeError):
    """Native retrieval failed closed; ``reason`` is a stable machine-readable code."""

    def __init__(self, reason: str, message: str):
        self.reason = reason
        super().__init__(message)


@dataclass(frozen=True)
class NativeScoreThreshold:
    """Exclusive raw-score threshold bound to a mode and query configuration."""

    value: float
    mode: str
    score_kind: str
    config_id: str

    def __post_init__(self):
        if isinstance(self.value, bool) or not isinstance(self.value, (int, float)) or not math.isfinite(self.value):
            raise NativeRetrievalError("invalid_request", "threshold must be finite")


__all__ = ["MemoryKind", "MemoryVariant", "AmbiguousMemoryIdError",
           "NativeRetrievalError", "NativeScoreThreshold"]
