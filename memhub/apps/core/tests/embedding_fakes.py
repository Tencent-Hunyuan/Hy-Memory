"""An in-memory `ReembedBackend` for embedding switch tests.

It keeps collections as plain dicts named exactly as the SDK names them, so a
test can lay out a home -- a live space, a stray 512 space left by an older
build, a retired space -- and check what a switch or merge did to each. The
vectors themselves are not computed: a record remembers the model and width
it was "embedded" with, which is what the assertions need.
"""

from __future__ import annotations

import asyncio
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from one_memory.embedding_backend import (
    EmbeddingTarget,
    EmbeddingWidthError,
    GraphCheck,
    PhysicalSpace,
    ReembedCancelledError,
    ReembedStep,
    ScannedSpace,
    VectorCheck,
)
from one_memory.embedding_spaces import VECTOR_ROLES, space_collection_names

_ROLE_SUFFIX = re.compile(
    r"^(?P<base>.+?)_(?P<role>coding_keys_cv|coding_keys|working_facts_cv|working_facts)"
    r"(?:_(?P<dims>\d+))?$"
)
_DIMS_SUFFIX = re.compile(r"^(?P<base>.+)_(?P<dims>\d+)$")


class Crash(BaseException):
    """Stands in for the process dying: nothing in the worker catches it."""


@dataclass
class Record:
    document: str
    metadata: dict[str, object]
    width: int
    model: str


@dataclass
class FakeVectorStore:
    collections: dict[str, dict[str, Record]] = field(default_factory=dict)

    def put(
        self,
        base: str,
        name_dims: int | None,
        records: dict[str, tuple[str, str]],
        *,
        role: str = "memory",
        width: int | None = None,
        model: str = "old-model",
    ) -> None:
        """`records` maps id -> (document, user_id)."""
        name = space_collection_names(base, name_dims)[role]
        target = self.collections.setdefault(name, {})
        for record_id, (document, user) in records.items():
            target[record_id] = Record(
                document=document,
                metadata={"user_id": user, "content": document},
                width=width or name_dims or 0,
                model=model,
            )

    def space(self, base: str, name_dims: int | None) -> dict[str, dict[str, Record]]:
        return {
            role: dict(self.collections.get(name, {}))
            for role, name in space_collection_names(base, name_dims).items()
            if name in self.collections
        }

    def ids(self, base: str, name_dims: int | None, role: str = "memory") -> set[str]:
        return set(self.collections.get(space_collection_names(base, name_dims)[role], {}))


def parse(name: str) -> tuple[str, int | None, str] | None:
    if name.endswith("_tag_index"):
        return None
    match = _ROLE_SUFFIX.match(name)
    if match:
        dims = match.group("dims")
        return match.group("base"), int(dims) if dims else None, match.group("role")
    role = "memory"
    if name.endswith("_entities"):
        name, role = name[: -len("_entities")], "entities"
    match = _DIMS_SUFFIX.match(name)
    if match:
        return match.group("base"), int(match.group("dims")), role
    return name, None, role


class FakeReembedBackend:
    """The backend contract, over `FakeVectorStore`."""

    def __init__(self, store: FakeVectorStore | None = None) -> None:
        self.store = store or FakeVectorStore()
        self.unavailable: str | None = None
        #: Called before every batch; a test raises `Crash` or flips a cancel here.
        self.before_batch: Callable[[str, int], None] = lambda _role, _done: None
        #: Width the "embedding service" returns; None returns the target's.
        self.returned_width: int | None = None
        #: Ids the re-embed silently loses, to prove verification catches it.
        self.lose_ids: set[str] = set()
        self.batch_size = 2
        self.reembed_calls: list[tuple[str, str]] = []
        self.dropped: list[str] = []
        self.graphs_built: list[Path] = []
        #: When set, every batch waits for it: a switch a test holds mid-way.
        self.pause: asyncio.Event | None = None

    def unavailable_reason(self) -> str | None:
        return self.unavailable

    async def scan(self, *, per_user: bool = False) -> tuple[ScannedSpace, ...]:
        grouped: dict[tuple[str, int | None], dict[str, str]] = {}
        for name in self.store.collections:
            parsed = parse(name)
            if parsed is None:
                continue
            base, dims, role = parsed
            grouped.setdefault((base, dims), {})[role] = name
        out = []
        for (base, dims), roles in sorted(
            grouped.items(), key=lambda kv: (kv[0][0], kv[0][1] or 0)
        ):
            counts = {role: len(self.store.collections[name]) for role, name in roles.items()}
            widths = {
                record.width
                for name in roles.values()
                for record in self.store.collections[name].values()
            }
            users: dict[str, int] = {}
            if per_user:
                for name in roles.values():
                    for record in self.store.collections[name].values():
                        user = str(record.metadata.get("user_id") or "")
                        users[user] = users.get(user, 0) + 1
            out.append(
                ScannedSpace(
                    collection_base=base,
                    name_dims=dims,
                    width=widths.pop() if len(widths) == 1 else None,
                    counts=counts,
                    collections=roles,
                    per_user=users,
                )
            )
        return tuple(out)

    async def diff(self, source: PhysicalSpace, target: PhysicalSpace) -> dict[str, int]:
        target_names = target.names()
        out = {}
        for role, name in source.names().items():
            have = set(self.store.collections.get(target_names.get(role, ""), {}))
            out[role] = len(set(self.store.collections.get(name, {})) - have)
        return out

    async def reembed(
        self,
        source: PhysicalSpace,
        target: PhysicalSpace,
        *,
        embedding: EmbeddingTarget,
        cancel: Callable[[], bool],
        on_step: Callable[[ReembedStep], None],
        batch_size: int = 64,
    ) -> None:
        del batch_size
        if target.dims is None:
            raise ValueError("the target space needs an explicit width")
        self.reembed_calls.append((source.collection_base, target.collection_base))
        target_names = target.names()
        model = embedding.space.model or "?"
        for role in VECTOR_ROLES:
            name = source.names().get(role)
            if name is None or name not in self.store.collections:
                continue
            records = sorted(self.store.collections[name].items())
            destination = self.store.collections.setdefault(target_names[role], {})
            done = embedded = kept = 0
            for start in range(0, len(records), self.batch_size):
                if cancel():
                    raise ReembedCancelledError("embedding_switch_cancelled")
                self.before_batch(role, done)
                if self.pause is not None:
                    await self.pause.wait()
                batch = records[start : start + self.batch_size]
                width = self.returned_width or target.dims
                if width != target.dims:
                    raise EmbeddingWidthError("embedding_dimensions_mismatch")
                for record_id, record in batch:
                    if record_id in destination:
                        kept += 1
                        continue
                    if record_id in self.lose_ids:
                        continue
                    destination[record_id] = Record(
                        document=record.document,
                        metadata=dict(record.metadata),
                        width=width,
                        model=model,
                    )
                    embedded += 1
                done += len(batch)
                on_step(
                    ReembedStep(
                        phase="vectors",
                        role=role,
                        done=done,
                        total=len(records),
                        embedded=embedded,
                        kept=kept,
                    )
                )

    async def verify(
        self, source: PhysicalSpace, target: PhysicalSpace, *, embedding: EmbeddingTarget
    ) -> VectorCheck:
        del embedding
        problems: list[str] = []
        per_role: dict[str, dict[str, int]] = {}
        users_source: dict[str, int] = {}
        users_target: dict[str, int] = {}
        target_names = target.names()
        for role, name in source.names().items():
            src = self.store.collections.get(name, {})
            tgt = self.store.collections.get(target_names.get(role, ""), {})
            missing = set(src) - set(tgt)
            extra = set(tgt) - set(src)
            mismatched = {
                key for key in set(src) & set(tgt) if src[key].document != tgt[key].document
            }
            bad = sum(1 for record in tgt.values() if record.width != target.dims)
            per_role[role] = {
                "source": len(src),
                "target": len(tgt),
                "missing": len(missing),
                "extra": len(extra),
                "mismatched": len(mismatched),
                "bad_dims": bad,
            }
            for label, value in (
                ("missing", missing),
                ("extra", extra),
                ("mismatched", mismatched),
            ):
                if value:
                    problems.append(f"{role}: {len(value)} {label}")
            if bad:
                problems.append(f"{role}: {bad} bad dims")
            for record in src.values():
                user = str(record.metadata.get("user_id") or "")
                users_source[user] = users_source.get(user, 0) + 1
            for record in tgt.values():
                user = str(record.metadata.get("user_id") or "")
                users_target[user] = users_target.get(user, 0) + 1
        for user in sorted(set(users_source) | set(users_target)):
            if users_source.get(user, 0) != users_target.get(user, 0):
                problems.append(f"user {user!r}: counts differ")
        return VectorCheck(per_role=per_role, per_user=users_target, problems=tuple(problems))

    async def graph_vectors(self, path: Path) -> int | None:
        return 3 if path.exists() else None

    async def reembed_graph(
        self,
        source: Path,
        target: Path,
        *,
        embedding: EmbeddingTarget,
        target_dims: int,
        cancel: Callable[[], bool],
        on_step: Callable[[ReembedStep], None],
    ) -> None:
        del embedding, cancel
        target.mkdir(parents=True, exist_ok=True)
        (target / "width").write_text(str(target_dims))
        self.graphs_built.append(target)
        on_step(ReembedStep(phase="graph", role="Memory", done=3, total=3, embedded=3))

    async def verify_graph(self, source: Path, target: Path, *, target_dims: int) -> GraphCheck:
        ok = (target / "width").is_file() and (target / "width").read_text() == str(target_dims)
        return GraphCheck(ok=ok, problems=() if ok else ("graph width",))

    async def drop(self, space: PhysicalSpace) -> None:
        for name in space.names().values():
            if self.store.collections.pop(name, None) is not None:
                self.dropped.append(name)

    async def drop_graph(self, path: Path) -> None:
        shutil.rmtree(path, ignore_errors=True)
