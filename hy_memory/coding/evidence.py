"""Canonical consolidation inputs shared by orchestration and persistence.

Links, evolution state and inherited text deliberately do not affect a basis.
"""

import hashlib
import json
from dataclasses import asdict
from typing import Any, Dict, List, Tuple, Union

from .types import CodingMemory, ConsolidationScope, EvidenceReference, WorkingFact

_DETAILS_INHERIT_MARKER = "--- inherited from superseded facts ---"


def _own_details(details: str) -> str:
    """Return details authored by this generation, excluding inherited blocks."""
    return (details or "").split(_DETAILS_INHERIT_MARKER, 1)[0].rstrip()


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def project(material: Union[CodingMemory, WorkingFact]) -> Dict[str, Any]:
    """The complete model-visible projection; also the sole invalidation rule."""
    common = {name: getattr(material, name) for name in (
        "user_id", "agent_id", "source", "workspace_id", "branch", "session_id")}
    common["memory_at"] = (int(material.memory_at.timestamp())
                           if material.memory_at is not None else None)
    common["files"] = list(material.files)
    if isinstance(material, WorkingFact):
        common.update(kind="fact", material_id=material.fact_id,
                      statement=material.statement, subject=material.subject,
                      details=_own_details(material.details))
    else:
        common.update(kind="experience", material_id=material.experience_id,
                      task=material.task, search_keys=list(material.search_keys),
                      solution=material.solution, boundary_envs=material.boundary_envs,
                      confidence=material.confidence)
    return common


def reference(projection: Dict[str, Any]) -> EvidenceReference:
    return EvidenceReference(projection["kind"], projection["material_id"],
                             projection["session_id"], digest(projection),
                             projection["memory_at"])


def identity(
    scope: ConsolidationScope, sessions: List[str], refs: List[EvidenceReference],
) -> Tuple[str, str, str]:
    group = digest({"scope": asdict(scope), "session_ids": sorted(set(sessions))})
    basis = digest([r.to_dict() for r in sorted(refs, key=lambda r: (r.kind, r.material_id))])
    return group, basis, digest({"group_key": group, "basis_digest": basis})


def validate_sessions(session_ids: List[str]) -> List[str]:
    if not isinstance(session_ids, (list, tuple)) or any(
        not isinstance(s, str) or not s.strip() for s in session_ids
    ):
        raise ValueError("session_ids must contain nonempty strings")
    sessions = sorted(set(session_ids))
    if len(sessions) < 2:
        raise ValueError("consolidation requires at least two distinct sessions")
    return sessions
