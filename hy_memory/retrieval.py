"""Public types and errors for exact memory-ID lookup."""

from dataclasses import dataclass
import math
from typing import Literal, Optional, Sequence


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

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(message)


@dataclass(frozen=True)
class NativeScoreThreshold:
    """Exclusive raw-score threshold bound to a mode and query configuration."""

    value: float
    mode: str
    score_kind: str
    config_id: str

    def __post_init__(self) -> None:
        if (
            isinstance(self.value, bool)
            or not isinstance(self.value, (int, float))
            or not math.isfinite(self.value)
        ):
            raise NativeRetrievalError("invalid_request", "threshold must be finite")


NATIVE_CONFIG_ID = "qdrant-native-v1-multilingual-lowercase-rrf2"


def native_score_kind(mode: str) -> str:
    return "cosine" if mode == "dense" else "rrf"


def validate_native_threshold(
    threshold: Optional[NativeScoreThreshold], mode: str
) -> None:
    if threshold is None:
        return
    if not isinstance(threshold, NativeScoreThreshold) or (
        threshold.mode,
        threshold.score_kind,
        threshold.config_id,
    ) != (mode, native_score_kind(mode), NATIVE_CONFIG_ID):
        raise NativeRetrievalError(
            "invalid_request", "native threshold mode, score kind or config mismatch"
        )


__all__ = [
    "MemoryKind",
    "MemoryVariant",
    "AmbiguousMemoryIdError",
    "NativeRetrievalError",
    "NativeScoreThreshold",
]
