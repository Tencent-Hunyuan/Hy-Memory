"""Assemble a continuation packet from already authorized context items."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from one_memory.context_models import ContextItem, MemoryContextItem

ResumeField = Literal[
    "goal", "decisions", "constraints", "reported_progress", "open_items", "resources"
]
EvidenceClass = Literal[
    "person_statement", "agent_report", "tool_output", "system_acceptance", "governed_memory"
]
Observation = Literal["current", "observed_at", "unverified"]

_FIELD_RULES: tuple[tuple[ResumeField, re.Pattern[str]], ...] = (
    ("resources", re.compile(r"https?://|(?:^|\s)/(?:[^\s/]+/)+[^\s]+|\b[A-Za-z]:\\")),
    ("open_items", re.compile(r"待办|未完成|下一步|阻塞|open item|todo|next step", re.I)),
    ("constraints", re.compile(r"必须|不得|禁止|限制|约束|must|cannot|constraint", re.I)),
    ("decisions", re.compile(r"决定|选定|确认采用|改用|decision|decided|chosen", re.I)),
    (
        "reported_progress",
        re.compile(r"已完成|已经实现|通过|提交|进展|done|passed|implemented", re.I),
    ),
    ("goal", re.compile(r"目标|目的|要做|负责|goal|objective", re.I)),
)


class ResumeEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    field: ResumeField | Literal["unclassified"]
    text: str = Field(min_length=1, max_length=20000)
    evidence_class: EvidenceClass
    reference_id: str = Field(min_length=1, max_length=512)
    revision: int = Field(ge=1)
    written_by: str = Field(min_length=1, max_length=32)
    source_id: str | None = Field(default=None, max_length=128)
    event_time: str | None = Field(default=None, max_length=128)
    observation: Observation = "observed_at"
    observed_at: str | None = Field(default=None, max_length=128)
    current: bool = False


class ResumePacket(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    goal: tuple[ResumeEvidence, ...] = ()
    decisions: tuple[ResumeEvidence, ...] = ()
    constraints: tuple[ResumeEvidence, ...] = ()
    reported_progress: tuple[ResumeEvidence, ...] = ()
    open_items: tuple[ResumeEvidence, ...] = ()
    resources: tuple[ResumeEvidence, ...] = ()
    unclassified_evidence: tuple[ResumeEvidence, ...] = ()
    missing_fields: tuple[ResumeField, ...]


def _field(text: str) -> ResumeField | None:
    for name, pattern in _FIELD_RULES:
        if pattern.search(text):
            return name
    return None


def _raw_class(role: str, evidence_id: str, accepted: frozenset[str]) -> EvidenceClass:
    if role == "user":
        return "person_statement"
    if role == "assistant":
        return "agent_report"
    if role == "tool" and evidence_id in accepted:
        return "system_acceptance"
    return "tool_output"


def assemble_resume_packet(
    items: tuple[ContextItem, ...],
    *,
    verified_system_evidence_ids: frozenset[str] = frozenset(),
    per_field_limit: int = 8,
) -> ResumePacket:
    """Classify literal evidence without summarizing, validating or writing facts."""
    if type(per_field_limit) is not int or not 1 <= per_field_limit <= 32:
        raise ValueError("invalid_resume_field_limit")
    fields: dict[ResumeField, list[ResumeEvidence]] = {
        "goal": [],
        "decisions": [],
        "constraints": [],
        "reported_progress": [],
        "open_items": [],
        "resources": [],
    }
    unclassified: list[ResumeEvidence] = []
    seen: set[tuple[str, int]] = set()
    for item in items:
        candidates: tuple[ResumeEvidence, ...]
        if isinstance(item, MemoryContextItem):
            candidates = (
                ResumeEvidence(
                    field=_field(item.content) or "unclassified",
                    text=item.content,
                    evidence_class="governed_memory",
                    reference_id=item.memory_id,
                    revision=item.revision,
                    written_by="governance",
                    event_time=item.updated_at.isoformat(),
                    observation="current",
                    observed_at=item.updated_at.isoformat(),
                    current=True,
                ),
            )
        else:
            candidates = tuple(
                ResumeEvidence(
                    field=_field(message.text) or "unclassified",
                    text=message.text,
                    evidence_class=_raw_class(
                        message.role,
                        message.evidence_id,
                        verified_system_evidence_ids,
                    ),
                    reference_id=message.evidence_id,
                    revision=message.revision,
                    written_by=message.written_by,
                    source_id=item.source_id,
                    event_time=message.created_at.isoformat() if message.created_at else None,
                    observation="observed_at" if message.created_at else "unverified",
                    observed_at=message.created_at.isoformat() if message.created_at else None,
                    current=False,
                )
                for message in item.messages
            )
        for evidence in candidates:
            identity = (evidence.reference_id, evidence.revision)
            if identity in seen:
                continue
            seen.add(identity)
            inferred = _field(evidence.text)
            if inferred is None:
                unclassified.append(evidence)
            elif len(fields[inferred]) < per_field_limit:
                fields[inferred].append(evidence.model_copy(update={"field": inferred}))
    missing = tuple(name for name, values in fields.items() if not values)
    return ResumePacket(
        **{name: tuple(values) for name, values in fields.items()},
        unclassified_evidence=tuple(unclassified[: per_field_limit * 2]),
        missing_fields=missing,
    )
