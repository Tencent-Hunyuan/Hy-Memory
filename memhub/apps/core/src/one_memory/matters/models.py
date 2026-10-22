from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Citation(Contract):
    evidence_id: str | None = Field(default=None, min_length=1, max_length=512)
    raw_id: str | None = Field(default=None, min_length=1, max_length=512)
    item_index: int | None = Field(default=None, ge=0)
    quote: str = Field(min_length=1, max_length=1600)
    revision: int = 0
    source_id: str = ""
    written_by: str = ""
    created_at: str | None = None


class MemoryRef(Contract):
    memory_id: str
    revision: int | None = Field(default=None, ge=1)
    status: Literal["confirmed", "active"] = "confirmed"
    content_hash: str
    task_id: str | None = None
    hy_memory_id: str | None = None
    engine: Literal["hy-memory"] = "hy-memory"
    engine_version: str | None = None
    profile_id: str | None = None
    profile_revision: int | None = Field(default=None, ge=1)
    model: str | None = None
    origin: str = "extracted"
    authority: Literal["legacy", "sdk"] = "legacy"

    @field_validator("status", mode="before")
    @classmethod
    def _pending_is_confirmed(cls, value: object) -> object:
        # Matters saved before the pending state was dropped still carry it;
        # the memory it names was migrated to confirmed at the same revision.
        return "confirmed" if value == "candidate" else value


class Claim(Contract):
    statement: str = Field(min_length=1, max_length=1600)
    kind: Literal[
        "goal", "decision", "constraint", "proposal", "progress", "open_question", "experience"
    ]
    scope: str = Field(default="", max_length=400)
    # One synthesis batch contains at most twelve memories with eight quotes each.
    citations: list[Citation] = Field(min_length=1, max_length=96)
    basis: Literal["human_statement", "agent_report", "mixed", "unknown"] = "unknown"
    verification: Literal["citation_checked", "model_reviewed"] = "citation_checked"
    state: Literal["current", "historical", "superseded", "unresolved"] = "current"
    change_note: str = Field(default="", max_length=800)
    memory_refs: list[MemoryRef] = Field(default_factory=list, max_length=32)


class MatterDraft(Contract):
    title: str = Field(min_length=2, max_length=100)
    purpose: str = Field(min_length=1, max_length=800)
    boundary: str = Field(min_length=1, max_length=800)
    anchors: list[str] = Field(min_length=1, max_length=16)
    claims: list[Claim] = Field(min_length=1)
    tags: list[str] = Field(default_factory=list, max_length=8)
    uncertainties: list[str] = Field(default_factory=list, max_length=12)


class Matter(MatterDraft):
    # Unavailable versions contain no derived user text.
    title: str = "来源不可用"
    purpose: str = ""
    boundary: str = ""
    anchors: list[str] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    matter_id: str
    version: int
    scope_confirmed: bool = False
    agent_access: bool = False
    availability: Literal[
        "ready",
        "needs_update",
        "source_unavailable",
        "evidence_changed",
        "memory_changed",
        "archived",
    ] = "ready"
    model: str
    updated_at: str
    source_count: int = 0
    evidence_count: int = 0
    content_review: Literal["needs_review"] = "needs_review"
    source_engine: Literal["hy-memory", "legacy_direct"] = "legacy_direct"


class MatterPatch(Contract):
    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=2, max_length=100)
    boundary: str | None = Field(default=None, min_length=1, max_length=800)
    scope_confirmed: bool | None = None
    agent_access: bool | None = None


class MatterNote(Contract):
    text: str = Field(min_length=1, max_length=20_000)
    expected_version: int = Field(ge=1)


class MatterContext(Contract):
    matter_id: str
    version: int
    availability: str
    text: str
    instruction_policy: Literal["reference_only"] = "reference_only"
    truncated: bool = False


class MatterQuery(Contract):
    query: str = Field(min_length=1, max_length=512)


class SourceCoverage(Contract):
    source_id: str
    records: int
    conversations: int
    unknown_time_records: int
    selected_records: int = 0


class SampleRecord(Contract):
    evidence_id: str
    source_id: str
    conversation_id: str
    role: str
    written_by: str
    created_at: str | None
    observed_at: str
    revision: int
    content_hash: str
    project: str | None
    workspace: str | None
    text: str


class DeferredConversation(Contract):
    source_id: str
    conversation_id: str
    records: int
    characters: int
    reason: Literal["character_budget", "conversation_limit"]


class EvidenceSample(Contract):
    records: list[SampleRecord]
    coverage: list[SourceCoverage]
    selection_basis: str = "observation_order_not_message_time"
    truncated_records: int = 0
    omitted_records: int = 0
    excluded_probe_records: int = 0
    duplicate_records: int = 0
    coverage_complete: bool = True
    deferred_conversations: list[DeferredConversation] = Field(default_factory=list)


class DiscoveryStatus(Contract):
    run_id: str
    status: Literal["running", "completed", "failed", "interrupted"]
    phase: str
    completed_batches: int = 0
    total_batches: int = 0
    selected_records: int = 0
    omitted_records: int = 0
    rejected_claims: int = 0
    matter_ids: list[str] = Field(default_factory=list)
    error: str | None = None
    failed_phase: str | None = None
    validation_issues: list[str] = Field(default_factory=list)
    elapsed_seconds: float = 0
    observations: list[str] = Field(default_factory=list)
    extraction_engine: Literal["hy-memory", "legacy_direct"] = "legacy_direct"
    extraction_task_ids: list[str] = Field(default_factory=list)
    synthesis_profile_id: str | None = None
    synthesis_profile_revision: int | None = None
    import_job_ids: list[str] = Field(default_factory=list)
    collection_mode: Literal["source_import", "existing_evidence"] = "existing_evidence"
    coverage_complete: bool = False
    deferred_conversations: list[DeferredConversation] = Field(default_factory=list)
    hy_input_records: int = 0
    hy_excluded_records: int = 0
    governed_memories: int = 0
    reviewed_memories: int = 0
    reference_only_memories: int = 0
    recovered_evidence_links: int = 0
    required_memory_coverage_complete: bool = False


class DiscoveryInput(Contract):
    per_source: int | None = Field(default=None, ge=1, le=1000)
    max_chars: int | None = Field(default=None, ge=10_000, le=400_000)
    extraction_profile_id: str | None = None
    synthesis_profile_id: str | None = None
