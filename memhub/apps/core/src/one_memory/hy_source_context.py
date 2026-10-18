"""Per-write original Evidence and where each HY memory came from; never a memory source."""

import json
from collections.abc import Set as AbstractSet
from importlib import import_module
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from one_memory.imports.models import EvidenceContentKind, EvidenceRecord, WrittenBy
from one_memory.input_sanitization import sanitize_model_input


class HySupportedMemory(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    hy_memory_id: str
    governed_memory_id: str
    revision: int
    content: str
    owner: Literal["user", "agent"]
    evidence_ids: tuple[str, ...]


class HySourceContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    current: tuple[EvidenceRecord, ...]
    history: tuple[EvidenceRecord, ...] = ()
    supporting: tuple[EvidenceRecord, ...] = ()
    known_memories: tuple[HySupportedMemory, ...] = ()


class HyWriteBindings:
    def __init__(self, context: HySourceContext) -> None:
        if not context.current:
            raise ValueError("hy_memory_write_verification_context_scope")
        self.context = context
        seed = context.current[0]
        records = (*context.current, *context.history, *context.supporting)
        workspaces = {record.workspace for record in records}
        # A missing workspace is a legacy identity, not a wildcard that may
        # absorb a named project. Keep NULL and named workspaces disjoint.
        if None in workspaces and len(workspaces) != 1:
            raise ValueError("hy_memory_write_verification_context_scope")
        ids = [record.evidence_id for record in records]
        if len(ids) != len(set(ids)):
            raise ValueError("hy_memory_write_verification_context_scope")
        for record in records:
            if (
                (
                    record.user_id,
                    record.source_id,
                    record.conversation_id,
                    record.workspace,
                )
                != (
                    seed.user_id,
                    seed.source_id,
                    seed.conversation_id,
                    seed.workspace,
                )
                or record.content_kind is not EvidenceContentKind.RAW_CONVERSATION
                or (record.role, record.written_by)
                not in {
                    # D98: the same change as the turn assembly. A sub agent's
                    # question is a `user` message marked written by the
                    # agent; excluding it here would make the source context
                    # disagree with what was actually sent.
                    ("user", WrittenBy.PERSON),
                    # A host that leaves the author blank still sent a turn.
                    # WorkBuddy records 1078 of them across 1002 conversations,
                    # and refusing the write over that answered every one of
                    # those tasks with `model_content_outcome_unknown` on the
                    # second attempt: a memory that may have been written and
                    # can never be confirmed. The author is missing, not the
                    # turn; scope is still checked above.
                    ("user", WrittenBy.UNKNOWN),
                    ("user", WrittenBy.AGENT),
                    ("assistant", WrittenBy.AGENT),
                    ("tool", WrittenBy.TOOL),
                }
            ):
                raise ValueError("hy_memory_write_verification_context_scope")
        self.all_sources: list[dict[str, Any]] = [
            {
                "id": record.evidence_id,
                "role": record.role,
                "content": sanitize_model_input(record.verbatim),
                "new": index < len(context.current),
                "revision": record.revision,
                "created_at": record.created_at.isoformat() if record.created_at else None,
            }
            for index, record in enumerate(records)
        ]
        self.sources = self.all_sources[: len(context.current) + len(context.history)]
        self._supports: dict[tuple[str, str], tuple[str, ...]] = {}
        self.profile_order: list[str] | None = None
        self.verified_operations: dict[str, tuple[str, str]] = {}
        self.known = {memory.hy_memory_id: memory for memory in context.known_memories}
        for memory in context.known_memories:
            if not memory.evidence_ids or not set(memory.evidence_ids) <= set(ids):
                raise ValueError("hy_memory_write_verification_context_scope")
            self._supports[(memory.content, memory.owner)] = memory.evidence_ids

    def check_content(self, content: str) -> None:
        try:
            parsed = json.loads(content)
        except ValueError as error:
            raise ValueError("hy_memory_write_verification_input_mismatch") from error
        expected = [
            {"role": record.role, "content": sanitize_model_input(record.verbatim)}
            for record in self.context.current
        ]
        # HY strips tool messages itself before chat extraction when it judges a turn chat.
        without_tools = [item for item in expected if item["role"] != "tool"]
        if parsed != expected and (without_tools == expected or parsed != without_tools):
            raise ValueError("hy_memory_write_verification_input_mismatch")

    def history_json(self) -> str:
        return json.dumps(
            [
                {
                    "evidence_id": source["id"],
                    **{k: source[k] for k in ("role", "content", "revision", "created_at")},
                }
                for source in self.sources
                if not source["new"]
            ],
            ensure_ascii=False,
        )

    def turn_support(
        self, owner: str | None, targets: AbstractSet[str] = frozenset()
    ) -> tuple[str, ...]:
        """This turn's words by the memory's author, plus what a merged memory already cited."""
        roles = {"user": {"user"}, "agent": {"assistant"}}.get(owner or "")
        current = [source for source in self.all_sources if source["new"]]
        ids = [source["id"] for source in current if roles is None or source["role"] in roles]
        if not ids:
            ids = [source["id"] for source in current]
        for target in sorted(targets):
            if target in self.known:
                ids.extend(self.known[target].evidence_ids)
        return tuple(dict.fromkeys(ids))

    def record_turn_supports(self, items: list[dict[str, Any]]) -> None:
        for item in items:
            key = (item["content"], item["owner"] or "")
            self._supports.setdefault(key, self.turn_support(item["owner"]))

    def record_operation_support(self, content: str, owner: str | None, targets: set[str]) -> None:
        key = (content, owner or "")
        merged = (*self._supports.get(key, ()), *self.turn_support(owner, targets))
        self._supports[key] = tuple(dict.fromkeys(merged))

    def support_for(self, content: str, owner: str | None) -> tuple[str, ...]:
        return self._supports.get((content, owner or ""), ())

    def profile_support(self, content: str, values: dict[str, Any]) -> tuple[str, ...]:
        render = import_module("hy_memory.agent.tools.basic_profile")._render_content

        if not values or render(values, field_order=self.profile_order) != content:
            return ()
        ids: dict[str, None] = {}
        for key, value in values.items():
            support = self.support_for(f"{key}: {value}", "user")
            if not support:
                return ()
            ids.update(dict.fromkeys(support))
        return tuple(ids)
