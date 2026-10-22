"""Passive lifecycle projection used only by retired Governance readers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType


@dataclass(frozen=True, eq=False)
class HyLifecycle:
    hidden: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "hidden", MappingProxyType(dict(self.hidden)))

    def __eq__(self, other: object) -> bool:
        return isinstance(other, HyLifecycle) and dict(self.hidden) == dict(
            other.hidden
        )

    def __hash__(self) -> int:
        return hash(tuple(sorted(self.hidden.items())))

    def replaced_by(self, memory_id: str) -> tuple[str, ...]:
        return self.hidden.get(memory_id, ())

    def replacing(self, memory_id: str) -> tuple[str, ...]:
        return tuple(
            sorted(
                old
                for old, replacements in self.hidden.items()
                if memory_id in replacements
            )
        )
