from collections.abc import Mapping, Sequence
from datetime import datetime

from one_memory.imports.models import EvidenceRecord
from one_memory.matters.models import Claim, SampleRecord


def merge_equivalent_claims(claims: Sequence[Claim]) -> list[Claim]:
    merged: list[Claim] = []
    positions: dict[tuple[str, ...], int] = {}
    for claim in claims:
        identity = (
            " ".join(claim.statement.split()),
            claim.kind,
            claim.state,
            claim.scope,
            claim.basis,
            claim.change_note,
        )
        previous = positions.get(identity)
        if previous is not None:
            original = merged[previous]
            citations = {
                (cite.evidence_id, cite.revision, cite.quote): cite
                for cite in original.citations + claim.citations
            }
            refs = {
                (ref.memory_id, ref.revision): ref
                for ref in original.memory_refs + claim.memory_refs
            }
            if len(citations) <= 96 and len(refs) <= 32:
                merged[previous] = original.model_copy(
                    update={
                        "citations": list(citations.values()),
                        "memory_refs": list(refs.values()),
                    }
                )
                continue
        positions[identity] = len(merged)
        merged.append(claim.model_copy(deep=True))
    return merged


def validate_claim(claim: Claim, records: Mapping[str, EvidenceRecord | SampleRecord]) -> Claim:
    citations = []
    authors = set()
    for citation in claim.citations:
        record = records.get(citation.evidence_id)
        if record is None:
            raise ValueError("unknown citation evidence")
        text = record.text if isinstance(record, SampleRecord) else record.verbatim
        if not citation.quote.strip() or citation.quote not in text:
            raise ValueError("citation quote is not in the source")
        author = {
            "user": "person",
            "assistant": "agent",
            "tool": "tool",
        }.get(record.role, "unknown")
        authors.add(author)
        created = record.created_at
        citations.append(
            citation.model_copy(
                update={
                    "revision": record.revision,
                    "source_id": record.source_id,
                    "written_by": author,
                    "created_at": created.isoformat() if isinstance(created, datetime) else created,
                }
            )
        )
    basis = (
        "mixed"
        if len(authors) > 1
        else {"person": "human_statement", "agent": "agent_report"}.get(
            next(iter(authors)), "unknown"
        )
    )
    kind = claim.kind
    if claim.memory_refs and all(ref.origin == "agent" for ref in claim.memory_refs):
        basis = "agent_report"
        if kind in {"decision", "constraint", "goal"}:
            kind = "proposal"
    if kind in {"decision", "constraint", "goal"} and "person" not in authors:
        kind = "proposal"
    state = claim.state
    if (
        basis == "agent_report"
        and kind in {"progress", "open_question"}
        and all(citation.created_at is None for citation in citations)
        and state in {"current", "unresolved"}
    ):
        state = "historical"
    return claim.model_copy(
        update={
            "citations": citations,
            "basis": basis,
            "kind": kind,
            "state": state,
            "verification": "citation_checked",
        }
    )
