"""Public types and errors for exact memory-ID lookup."""

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


__all__ = ["MemoryKind", "MemoryVariant", "AmbiguousMemoryIdError"]
