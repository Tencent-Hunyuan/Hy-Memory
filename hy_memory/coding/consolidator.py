"""Explicit cross-session consolidation with strict, whole-batch publication."""

import asyncio
import json
import math
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from ..config import MemoryConfig

import tiktoken

from ..prompts import get_prompt, resolve_language, with_output_language
from .evidence import canonical, validate_sessions
from .store import _run_in_pool
from .types import (
    CodingMemory, CodingWriteError, ConsolidationConflict,
    ConsolidationResult, ConsolidationScope, ConsolidationSnapshot,
)


if TYPE_CHECKING:
    from ..agent.llm_provider import LLMProvider, LLMResponse
    from .store import CodingMemoryStore


def _unique_object(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


class ExperienceConsolidator:
    """Uses caller-owned store/provider resources; never creates loops or connections."""

    def __init__(
        self, store: "CodingMemoryStore", provider: "LLMProvider", config: MemoryConfig,
    ) -> None:
        self.store = store
        self.provider = provider
        self.config = config

    def _validate_output(
        self, response: "LLMResponse", snapshot: ConsolidationSnapshot,
    ) -> List[CodingMemory]:
        if getattr(response, "finish_reason", "") == "length":
            raise CodingWriteError("OUTPUT_TRUNCATED", "consolidation output was truncated")
        content = response.content
        if not isinstance(content, str) or not content.strip():
            raise CodingWriteError("EMPTY_RESPONSE", "consolidation response is empty")
        try:
            output = json.loads(content, object_pairs_hook=_unique_object,
                                parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
            if not isinstance(output, dict) or set(output) != {"experiences"}:
                raise ValueError("expected an object containing only experiences")
            if not isinstance(output["experiences"], list):
                raise ValueError("experiences must be a list")
            refs = {(ref.kind, ref.material_id): ref for ref in snapshot.read_set}
            memories = []
            required = {"task", "search_keys", "solution", "boundary_envs", "confidence",
                        "supports", "conditions", "counterexamples", "verification_status"}
            for item in output["experiences"]:
                if not isinstance(item, dict) or set(item) != required:
                    raise ValueError("every experience must contain exactly the required fields")
                for field in ("task", "solution", "boundary_envs"):
                    if not isinstance(item[field], str) or not item[field].strip():
                        raise ValueError(f"{field} must be a nonempty string")
                if (not isinstance(item["search_keys"], list) or any(
                    not isinstance(key, str) or not key.strip() for key in item["search_keys"]
                )):
                    raise ValueError("search_keys must be a list of nonempty strings")
                confidence = item["confidence"]
                if (type(confidence) not in (float, int) or not math.isfinite(confidence)
                        or not 0 <= confidence <= 1):
                    raise ValueError("confidence must be finite and between 0 and 1")
                if item["verification_status"] not in ("verified", "unverified", "mixed"):
                    raise ValueError("invalid verification_status")
                evidence = []
                sessions = set()
                for role in ("supports", "conditions", "counterexamples"):
                    if not isinstance(item[role], list) or (role == "supports" and not item[role]):
                        raise ValueError("each experience needs supporting evidence")
                    seen = set()
                    for ref in item[role]:
                        if (not isinstance(ref, dict) or set(ref) != {"kind", "material_id", "reason"}
                                or not all(isinstance(v, str) and v.strip() for v in ref.values())):
                            raise ValueError("evidence requires kind, material_id and a substantive reason")
                        key = (ref["kind"], ref["material_id"])
                        if key not in refs or key in seen:
                            raise ValueError("unknown or duplicate evidence reference")
                        seen.add(key)
                        source = refs[key]
                        sessions.add(source.session_id)
                        evidence.append({**source.to_dict(), "role": role, "reason": ref["reason"],
                                         "verification_status": item["verification_status"]})
                if len(sessions) < 2:
                    raise ValueError("each conclusion must substantively involve at least two sessions")
                scope = snapshot.scope
                now = datetime.utcnow()
                memories.append(CodingMemory(
                    experience_id=uuid.uuid4().hex, user_id=scope.user_id, agent_id=scope.agent_id,
                    workspace_id=scope.workspace_id, branch=scope.branch, source=scope.source,
                    task=item["task"], search_keys=item["search_keys"], solution=item["solution"],
                    boundary_envs=item["boundary_envs"], confidence=confidence,
                    extraction_source="consolidation", consolidation_id=snapshot.consolidation_id,
                    evidence_refs=evidence, created_at=now, updated_at=now,
                ))
            return memories
        except (ValueError, TypeError, KeyError) as error:
            raise CodingWriteError("INVALID_CONSOLIDATION_OUTPUT", str(error)) from error

    async def consolidate(
        self, scope: ConsolidationScope, session_ids: List[str], *, language: Optional[str] = None,
    ) -> ConsolidationResult:
        sessions = validate_sessions(session_ids)
        if scope.scene != self.store.variant:
            raise ValueError("consolidation scene does not match the store variant")
        repository = self.store._repository
        if not repository.supports_consolidation:
            raise NotImplementedError("Repository does not support atomic consolidation")
        snapshot = await _run_in_pool(repository.consolidation_snapshot, scope, sessions)
        memories = []
        reuse = snapshot.reusable is not None
        attempt_id = uuid.uuid4().hex
        if not reuse:
            code = resolve_language(language, canonical(snapshot.materials))
            prompt = get_prompt("CODING_CONSOLIDATION_PROMPT", code).format(
                materials=canonical(snapshot.materials), scene=scope.scene,
            )
            prompt = with_output_language(prompt, code, explicit=language is not None)
            budget = self.config.coding.consolidation_max_input_tokens
            output_budget = self.config.coding.consolidation_max_output_tokens
            if type(budget) is not int or type(output_budget) is not int or min(budget, output_budget) <= 0:
                raise ValueError("consolidation token budgets must be positive integers")
            if len(tiktoken.get_encoding("cl100k_base").encode(prompt, disallowed_special=())) > budget:
                raise CodingWriteError("INPUT_BUDGET_EXCEEDED", "complete consolidation input exceeds budget")
            try:
                response = await self.provider.complete(prompt=prompt, max_tokens=output_budget,
                                                        audit_stage="coding_consolidate")
            except Exception as error:
                raise CodingWriteError("LLM_ERROR", f"consolidation model failed: {error}") from error
            memories = self._validate_output(response, snapshot)
            try:
                for memory in memories:
                    await self.store.prewrite_consolidated(memory)
            except BaseException:
                await self._cleanup(memories)
                raise
        try:
            receipt = await _run_in_pool(repository.publish_consolidation, snapshot, memories,
                                         attempt_id, reuse=reuse)
        except ConsolidationConflict:
            await self._cleanup(memories)
            raise
        except (Exception, asyncio.CancelledError):
            # Unclassified publication errors cannot prove that commit did not happen.
            # Keep attempt-unique vectors and expose the stable ID for SQL verification.
            return ConsolidationResult("outcome_unknown", snapshot.consolidation_id)
        # Publication returned a committed authoritative manifest. Only losing attempt IDs
        # can be removed; no cleanup ever writes or removes a winner's nodes.
        winner = set(receipt.experience_ids)
        await self._cleanup([m for m in memories if m.experience_id not in winner])
        return ConsolidationResult(receipt.decision, snapshot.consolidation_id, receipt,
                                   reused=reuse or receipt.attempt_id != attempt_id)

    async def _cleanup(self, memories: List[CodingMemory]) -> None:
        for memory in memories:
            await self.store._delete_vec_keys_by_count(memory.experience_id, 1 + len(memory.search_keys))
