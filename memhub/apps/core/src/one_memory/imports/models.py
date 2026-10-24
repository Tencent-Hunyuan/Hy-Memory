import hashlib
import json
import re
import unicodedata
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

EVIDENCE_NAMESPACE = UUID("a8ecfd99-1a7d-45f5-8a22-c3eaf3fcf71f")
MAX_VERBATIM_CHARACTERS = 1_000_000
MAX_RAW_META_BYTES = 65_536
Identifier = Annotated[str, Field(min_length=1, max_length=512)]


def identity_text(value: str) -> str:
    """The shared normalization every lane hashes into an Evidence identity.

    The hook lane and the file scan wrap the same words differently, so each
    lane hashes the text as its own source held it. This is the one function
    they both call, which is what lets a hook capture and a later transcript
    scan agree that they saw one message.
    """
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip()


class WrittenBy(StrEnum):
    PERSON = "person"
    AGENT = "agent"
    TOOL = "tool"
    UNKNOWN = "unknown"


class EvidenceOrigin(StrEnum):
    FILE = "file"
    HOOK = "hook"
    PLUGIN = "plugin"
    MCP = "mcp"
    SKILL = "skill"
    MANUAL_IMPORT = "manual_import"


class EvidenceContentKind(StrEnum):
    RAW_CONVERSATION = "raw_conversation"
    MEMORY_CONTENT = "memory_content"


class OriginMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    raw_meta: dict[str, JsonValue] = Field(default_factory=dict)
    source_version: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_raw_meta_size(self) -> "OriginMetadata":
        encoded = json.dumps(
            self.raw_meta,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
        if len(encoded) > MAX_RAW_META_BYTES:
            raise ValueError(f"raw_meta exceeds {MAX_RAW_META_BYTES} bytes")
        return self


class EvidenceInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: Identifier = "local"
    source_id: Identifier
    workspace: str | None = Field(default=None, max_length=4096)
    project: str | None = Field(default=None, max_length=1024)
    conversation_id: Identifier
    message_id: str | None = Field(default=None, min_length=1, max_length=512)
    correlation_id: str | None = Field(default=None, min_length=1, max_length=512)
    source_ordinal: str | None = Field(default=None, min_length=1, max_length=256)
    content_kind: EvidenceContentKind = EvidenceContentKind.RAW_CONVERSATION
    role: str = Field(min_length=1, max_length=128)
    # Legacy import/storage metadata only. Protocol `role` is authoritative;
    # this value must not affect identity, admission, scheduling or ownership.
    written_by: WrittenBy
    created_at: datetime | None = None
    verbatim: str = Field(min_length=1, max_length=MAX_VERBATIM_CHARACTERS)
    # What the source held before this build's cleaning stripped anything.
    # Identity hashes this, not `verbatim`, so a cleaning change cannot mint a
    # new identity for records that are already stored.
    raw_verbatim: str | None = Field(default=None, max_length=MAX_VERBATIM_CHARACTERS)
    origin_meta: dict[EvidenceOrigin, OriginMetadata] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_verbatim(self) -> "EvidenceInput":
        if not self.normalized_verbatim:
            raise ValueError("verbatim must contain non-whitespace text")
        if self.message_id and self.correlation_id and self.message_id != self.correlation_id:
            raise ValueError("message_id and correlation_id must match when both exist")
        if not (self.message_id or self.correlation_id or self.source_ordinal):
            raise ValueError("source_ordinal is required when no message or correlation ID exists")
        return self

    @property
    def origins(self) -> frozenset[EvidenceOrigin]:
        return frozenset(self.origin_meta)

    @property
    def normalized_verbatim(self) -> str:
        normalized = unicodedata.normalize("NFKC", self.verbatim)
        return re.sub(r"\s+", " ", normalized).strip()

    @property
    def identity_text(self) -> str:
        """The pre-cleaning text both lanes must hash the same way.

        Cleaning is what divides the hook lane from the file scan: each strips a
        different host wrapper around the same words. Hashing the cleaned body
        would give one message two identities, and every later cleaning change
        would re-key records that are already stored. Both lanes therefore hash
        the text as their source held it.
        """
        return identity_text(self.verbatim)

    @property
    def content_hash(self) -> str:
        """The fingerprint of what the source held, before this build cleaned it.

        Readers use it to tell "the host edited this message" from "we now strip
        something different", so it tracks the pre-cleaning text rather than the
        stored body.
        """
        return hashlib.sha256(self.identity_text.encode()).hexdigest()

    @property
    def idempotency_key(self) -> str:
        # Workspace is an explicit identity component: hosts can reuse a
        # conversation/message id in two projects, and a bare id would collapse
        # both histories into one Evidence row. The sentinel keeps an unset
        # workspace distinct from every literal workspace value.
        workspace = self.workspace if self.workspace is not None else "\uffffnull"
        source_message_id = self.message_id or self.correlation_id
        if source_message_id:
            identity = "\0".join(
                (
                    "message-v2",
                    self.user_id,
                    self.source_id,
                    workspace,
                    self.conversation_id,
                    source_message_id,
                )
            )
        else:
            # `role` is the protocol-level speaker identity. `written_by` was a
            # parser guess and differed between two imports of the same host
            # message (notably sub-agent/user and unknown/user), minting two
            # Evidence rows. Keep it out of every new identity.
            #
            # No workspace and no source_ordinal in the derived key: the hook
            # lane has neither (it reports no workspace and a constant ordinal),
            # so including either gave the same message a different key per lane
            # and stored it twice. Both are record metadata here, not identity.
            identity = "\0".join(
                (
                    "derived-v4",
                    self.user_id,
                    self.source_id,
                    self.conversation_id,
                    self.role,
                    self.content_hash,
                )
            )
            # `created_at` used to sit here, but a hook stamps the moment it
            # fired, so the same message captured twice got two identities and
            # two rows. The capture time is record metadata, not identity.
        return hashlib.sha256(identity.encode()).hexdigest()

    @property
    def written_by_derived_v3_idempotency_key(self) -> str:
        """The last identity that treated the parser's author guess as canonical.

        New builds key by protocol role. During an upgrade this key lets a
        rescan find and reuse the row already stored by derived-v3 instead of
        inserting the same host message under a second Evidence id.
        """
        identity = "\0".join(
            (
                "derived-v3",
                self.user_id,
                self.source_id,
                self.conversation_id,
                self.written_by,
                self.content_hash,
            )
        )
        return hashlib.sha256(identity.encode()).hexdigest()

    @property
    def written_by_derived_v3_idempotency_keys(self) -> tuple[str, ...]:
        """Every legacy author guess this protocol role may have carried."""
        guesses = {
            "user": (
                WrittenBy.PERSON,
                WrittenBy.AGENT,
                WrittenBy.UNKNOWN,
            ),
            "assistant": (WrittenBy.AGENT, WrittenBy.UNKNOWN, WrittenBy.PERSON),
            "tool": (WrittenBy.TOOL, WrittenBy.UNKNOWN),
        }.get(self.role, tuple(WrittenBy))
        ordered = (self.written_by, *guesses)
        keys: list[str] = []
        for written_by in dict.fromkeys(ordered):
            identity = "\0".join(
                (
                    "derived-v3",
                    self.user_id,
                    self.source_id,
                    self.conversation_id,
                    written_by,
                    self.content_hash,
                )
            )
            keys.append(hashlib.sha256(identity.encode()).hexdigest())
        return tuple(keys)

    @property
    def legacy_message_idempotency_key(self) -> str | None:
        """The pre-workspace key for a message-shaped input, when one exists.

        `message-v2` is required so two projects cannot collapse into one
        Evidence row. Databases written before that change still hold
        `message-v1` rows, so an upgraded store has to recognize the old key
        during replay instead of inserting a second copy of the same message.
        """
        source_message_id = self.message_id or self.correlation_id
        if source_message_id is None:
            return None
        identity = "\0".join(
            (
                "message-v1",
                self.user_id,
                self.source_id,
                self.conversation_id,
                source_message_id,
            )
        )
        return hashlib.sha256(identity.encode()).hexdigest()

    @property
    def legacy_derived_idempotency_key(self) -> str:
        """The pre-workspace key for an input without a message id.

        Older imports of file records without a message id used this material.
        It is retained solely so an upgraded store can recognize a replay
        instead of inserting the same derived record under a new v2 identity.
        """
        return self.derived_v2_idempotency_key("derived-v1", workspace_agnostic=True)

    @property
    def prior_derived_idempotency_key(self) -> str:
        """The key this same input held under the derived-v2 scheme.

        BUG-03 moved the derived key off the capture time and onto the
        pre-cleaning text. Rows already stored carry a derived-v2 key, so a
        replay has to recognize them as the same message instead of inserting a
        second copy under the new v3 identity.
        """
        return self.derived_v2_idempotency_key("derived-v2", workspace_agnostic=False)

    def derived_v2_idempotency_key(
        self,
        label: str,
        *,
        workspace_agnostic: bool,
        written_by: WrittenBy | None = None,
    ) -> str:
        identity = "\0".join(
            (
                label,
                self.user_id,
                self.source_id,
                *([] if workspace_agnostic else [self.workspace or "\uffffnull"]),
                self.conversation_id,
                written_by or self.written_by,
                self.created_at.isoformat() if self.created_at else "",
                self.source_ordinal or "",
                # v2 hashed the cleaned body; v1 predates raw_verbatim entirely,
                # so both replay against the stored body the old build kept.
                hashlib.sha256(self.normalized_verbatim.encode()).hexdigest(),
            )
        )
        return hashlib.sha256(identity.encode()).hexdigest()

    @property
    def older_written_by_idempotency_keys(self) -> tuple[str, ...]:
        keys: list[str] = []
        for written_by in WrittenBy:
            keys.append(
                self.derived_v2_idempotency_key(
                    "derived-v1",
                    workspace_agnostic=True,
                    written_by=written_by,
                )
            )
            keys.append(
                self.derived_v2_idempotency_key(
                    "derived-v2",
                    workspace_agnostic=False,
                    written_by=written_by,
                )
            )
        return tuple(keys)

    @property
    def evidence_id(self) -> str:
        return str(uuid5(EVIDENCE_NAMESPACE, self.idempotency_key))


class EvidenceRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_id: str
    user_id: str
    source_id: str
    workspace: str | None
    project: str | None
    conversation_id: str
    message_id: str | None
    correlation_id: str | None
    source_ordinal: str | None
    content_kind: EvidenceContentKind
    role: str
    # Retained while old databases and export formats still carry the column.
    written_by: WrittenBy
    created_at: datetime | None
    verbatim: str
    origin_meta: dict[EvidenceOrigin, OriginMetadata]
    content_hash: str
    revision: int
    imported_at: datetime
    updated_at: datetime

    @property
    def origins(self) -> frozenset[EvidenceOrigin]:
        return frozenset(self.origin_meta)


def materialize_write_input(
    value: EvidenceInput,
    *,
    observed_at: datetime | None = None,
) -> EvidenceRecord:
    """Freeze source input directly into a write-job payload, without Evidence SQL."""
    now = observed_at or datetime.now(UTC)
    return EvidenceRecord(
        evidence_id=value.evidence_id,
        user_id=value.user_id,
        source_id=value.source_id,
        workspace=value.workspace,
        project=value.project,
        conversation_id=value.conversation_id,
        message_id=value.message_id,
        correlation_id=value.correlation_id,
        source_ordinal=value.source_ordinal,
        content_kind=value.content_kind,
        role=value.role,
        written_by=value.written_by,
        created_at=value.created_at,
        verbatim=value.verbatim,
        origin_meta=value.origin_meta,
        content_hash=value.content_hash,
        revision=1,
        imported_at=now,
        updated_at=now,
    )
