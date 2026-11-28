"""Balanced, exact-L2 persona catalog for Ultra/System2.

The catalog is generated question-blind during ``evolve``.  It stores exact
active/latest L2 text grouped into a fixed domain taxonomy and keeps evidence
links to every selected L2.  It is a special L6 node, but it never participates
in ordinary L6 retrieval or consumes a schema slot.  Ultra deterministically
appends its content to the one visible L0 chain; Pro never enables this path.

Raw and S1 records are never modified by this module.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence

from ..agent.llm_provider import LLMProvider
from ..config import MemoryConfig
from ..data.graph_store_base import GraphStoreBase
from ..data.vector_store_base import VectorStoreBase
from ..models.memory import (
    MemoryLayer,
    MemoryNode,
    MemoryStatus,
    SourceType,
)
from ..utils.source_filter import merge_sources


CATALOG_SCHEMA_KIND = "balanced_persona_catalog"
CATALOG_VERSION = 4
CATALOG_MAX_PER_DOMAIN = 4
CATALOG_MAX_TOTAL = 32
CATALOG_DOMAINS = (
    "identity_relationships",
    "health_accessibility",
    "preferences_collections",
    "habits_items",
    "work_education",
    "culture_creative_experience",
    "values_coping",
    "current_constraints_goals",
)
_CATALOG_ITEM_RE = re.compile(r"^- \[([^\]]+)\] (.*)$")

_FORGET_RE = re.compile(
    r"(?i)\b(?:forget|forgotten|do not remember|asked (?:the )?assistant to forget|"
    r"requested (?:that )?.* be forgotten)\b"
)
_ASSISTANT_ADVICE_RE = re.compile(
    r"(?i)^\s*(?:the )?assistant (?:advised|recommended|suggested|explained|outlined)\b"
)
_GENERIC_QUESTION_RE = re.compile(
    r"(?i)^\s*the user (?:asked|wants to know|is curious|wondered)\b"
)
_EXPLICIT_FORGET_EN_RE = re.compile(
    r"(?:"
    r"\b(?:asked|told|requested)\b.{0,100}\bforget\s+"
    r"(?:that\s+(?:they|the user)\b|(?:their|the user's)\b|"
    r"(?:the\s+)?(?:detail|information|preference)\s+about\b)"
    r"|"
    r"\brequested\b.{0,40}\b(?:their|the user's)\b.{0,120}\bbe\s+forgotten\b"
    r")",
    re.I,
)
_EXPLICIT_FORGET_ZH_RE = re.compile(
    r"用户.{0,30}(?:请|要求|让).{0,20}忘记.{0,10}(?:自己|其|他们|她|他)"
)


def is_explicit_forget_marker(text: str) -> bool:
    """Conservative detector copied from the validated direct-forget path."""
    value = str(text or "").strip()
    if not value:
        return False
    lowered = value.lower()
    if not (lowered.startswith("the user ") or value.startswith("用户")):
        return False
    return bool(
        _EXPLICIT_FORGET_EN_RE.search(value)
        or _EXPLICIT_FORGET_ZH_RE.search(value)
    )


def extract_forget_targets(marker_text: str) -> List[str]:
    """Extract atomic forget targets using the validated direct-forget grammar."""
    text = " ".join(str(marker_text or "").split()).strip()
    if not text:
        return []
    sentences = [
        part.strip()
        for part in re.split(r"(?<=[.!?。！？])\s+", text)
        if part.strip()
    ]
    targets: List[str] = []
    seen = set()
    for sentence in sentences:
        target = sentence
        embedded_passive = re.search(
            r"(?i)\b(?:and\s+)?requested\s+that\s+(.+?)\s+be\s+forgotten\.?$",
            target,
        )
        embedded_active = re.search(
            r"(?i)\b(?:and\s+)?asked(?:\s+the\s+assistant)?\s+to\s+forget\s+"
            r"(?:that\s+)?(.+?)\.?$",
            target,
        )
        if embedded_passive:
            target = embedded_passive.group(1).strip()
        elif embedded_active:
            target = embedded_active.group(1).strip()
        passive = re.sub(
            r"(?i)^the user\s+requested\s+that\s+", "", target, count=1,
        )
        passive = re.sub(r"(?i)\s+be\s+forgotten\.?$", "", passive).strip()
        if passive != target:
            target = passive
        for pattern in (
            r"(?i)^the user\s+(?:asked|told|requested)(?:\s+the assistant)?"
            r"(?:\s+that)?\s+to\s+forget\s+(?:that\s+)?",
            r"(?i)^the user\s+requested\s+that\s+the assistant\s+forget\s+"
            r"(?:that\s+)?",
            r"(?i)^(?:please\s+)?forget\s+(?:that\s+)?",
        ):
            updated = re.sub(pattern, "", target, count=1)
            if updated != target:
                target = updated
                break
        target = re.sub(
            r"(?i)^the assistant\s+forget\s+(?:that\s+)?", "", target, count=1,
        )
        target = re.sub(
            r"(?i),?\s+and\s+the\s+assistant\s+"
            r"(?:confirmed|acknowledged|agreed|said|stated)\b.*$",
            "", target, count=1,
        ).strip()
        for atomic_target in re.split(
            r"(?i),?\s+(?:and\s+)?that\s+"
            r"(?=(?:they|their|the user|i|my|me)\b)",
            target,
        ):
            atomic_target = re.sub(
                r"^(?:请)?忘记(?:我|我的|本人)?", "", atomic_target,
            ).strip()
            atomic_target = re.sub(
                r"(?i)\s+from your memory\.?$", "", atomic_target,
            ).strip(" .")
            atomic_target = re.sub(
                r"(?i)^(?:their|the user's)\s+preference\s+for\s+",
                "", atomic_target,
            )
            if not atomic_target:
                continue
            key = atomic_target.casefold()
            if key not in seen:
                seen.add(key)
                targets.append(atomic_target)
    return targets


def _forget_terms(text: str) -> set[str]:
    stop = {
        "the", "user", "asked", "assistant", "please", "forget", "that",
        "they", "their", "this", "from", "memory", "about", "with",
        "and", "for", "into", "instead", "daily", "occasionally", "regularly",
        "prefer", "prefers", "preference", "enjoy", "enjoys", "like", "likes",
        "love", "loves", "use", "uses", "using", "have", "has", "had",
        "feel", "feels", "felt", "managed", "manage", "seeking", "valuing",
        "take", "takes", "keep", "keeps", "get", "gets", "visit", "visits",
    }
    output = set()
    for token in re.findall(r"[a-z0-9][a-z0-9_-]+", str(text or "").lower()):
        if len(token) < 3 or token in stop:
            continue
        if token == "apps":
            token = "app"
        elif token.endswith("ies") and len(token) > 5:
            token = token[:-3] + "y"
        elif token.endswith("s") and len(token) > 4 and not token.endswith("ss"):
            token = token[:-1]
        output.add(token)
    return output


def text_matches_forget_targets(text: str, targets: Iterable[str]) -> bool:
    """Full-anchor matcher shared with direct-forget; weak overlap is rejected."""
    text_terms = _forget_terms(text)
    for target in targets:
        anchors = _forget_terms(target)
        if not anchors:
            continue
        needed = len(anchors) if len(anchors) <= 3 else max(
            3, (len(anchors) * 3 + 4) // 5,
        )
        if len(anchors & text_terms) >= needed:
            return True
    return False


def collect_forget_targets(facts: Iterable[Any]) -> List[str]:
    targets: List[str] = []
    seen = set()
    for node in facts:
        content = str(getattr(node, "content", "") or "")
        if not is_explicit_forget_marker(content):
            continue
        for target in extract_forget_targets(content):
            key = target.casefold()
            if key not in seen:
                seen.add(key)
                targets.append(target)
    return targets


def suppress_forget_targets(
    entries: Iterable[Dict[str, Any]],
    targets: Iterable[str],
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    target_list = list(targets)
    kept: List[Dict[str, Any]] = []
    suppressed: List[Dict[str, Any]] = []
    for entry in entries:
        if text_matches_forget_targets(str(entry.get("content") or ""), target_list):
            # Never persist target plaintext.  Node/domain IDs are sufficient
            # for internal audit and rebuilding.
            suppressed.append({
                "node_id": str(entry.get("node_id") or ""),
                "domain": str(entry.get("domain") or ""),
            })
        else:
            kept.append(entry)
    return kept, suppressed


def _env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def balanced_catalog_generation_enabled() -> bool:
    return _env_bool("MEMORY_EVOLING_BALANCED_CATALOG_ENABLED")


def balanced_catalog_user_profile_enabled() -> bool:
    value = os.getenv("MEMORY_USER_PROFILE_BALANCED_CATALOG_ENABLED")
    if value is not None:
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return _env_bool("MEMORY_HYBRID_V2_BALANCED_CATALOG_L0_ENABLED")


def balanced_catalog_l0_enabled() -> bool:
    """Backward-compatible alias for the pre-profile-split helper name."""
    return balanced_catalog_user_profile_enabled()


def is_balanced_catalog_custom(custom: Optional[Dict[str, Any]]) -> bool:
    return str((custom or {}).get("schema_kind") or "") == CATALOG_SCHEMA_KIND


def parse_custom(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value or "{}")
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, json.JSONDecodeError):
            return {}
    return {}


def is_balanced_catalog_hit(hit: Dict[str, Any]) -> bool:
    return is_balanced_catalog_custom(parse_custom(
        hit.get("custom_json") or hit.get("custom") or {}
    ))


def catalog_reject_reason(content: str) -> str:
    value = " ".join(str(content or "").split()).strip()
    if not value:
        return "empty"
    if _FORGET_RE.search(value):
        return "forget"
    if _ASSISTANT_ADVICE_RE.search(value):
        return "assistant_advice"
    if _GENERIC_QUESTION_RE.search(value):
        return "generic_question"
    return ""


def _is_user_subject_fact(node: Any) -> bool:
    owner = str(getattr(node, "owner", "") or "").strip().lower()
    if owner in {"agent", "assistant"}:
        return False
    content = str(getattr(node, "content", "") or "").strip().lower()
    return bool(re.match(r"^(?:the\s+user|user)(?:\b|\s|[,:])|^用户", content))


def eligible_catalog_facts(nodes: Iterable[Any]) -> List[Any]:
    output = []
    for node in nodes:
        if getattr(node, "layer", None) != MemoryLayer.L2_FACT:
            continue
        if getattr(node, "status", None) != MemoryStatus.ACTIVE:
            continue
        if not bool(getattr(node, "is_latest", True)):
            continue
        if not _is_user_subject_fact(node):
            continue
        if (getattr(node, "custom", None) or {}).get("memory_kind") == "canonical_state":
            continue
        output.append(node)
    return sorted(output, key=lambda node: str(getattr(node, "node_id", "") or ""))


def source_bank_fingerprint(facts: Iterable[Any]) -> str:
    rows = [
        {
            "node_id": str(getattr(node, "node_id", "") or ""),
            "content": " ".join(str(getattr(node, "content", "") or "").split()),
            "tags": [str(value) for value in getattr(node, "tags", None) or []],
        }
        for node in facts
    ]
    encoded = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def catalog_prompt(max_per_domain: int = 4, max_total: int = 32) -> str:
    return f"""Build a balanced long-term persona catalog from numbered recorded facts.
Return strict JSON only:
{{"domains":{{"identity_relationships":[1],"health_accessibility":[2]}}}}

Use exactly these domain keys:
- identity_relationships: identity and the user's own family/relationship structure
- health_accessibility: explicit health, sensory, mobility, ergonomic constraints
- preferences_collections: explicit durable likes, dislikes, tastes, collections
- habits_items: recurring habits and personally owned/carried/used items
- work_education: durable role, expertise, education, recurring responsibilities
- culture_creative_experience: concrete cultural/creative interests and strong experiences
- values_coping: durable values and personally practiced coping resources
- current_constraints_goals: ongoing burdens, constraints, or durable active goals

Select at most {max_per_domain} entry numbers per domain and at most {max_total}
unique entries total. Prefer specific, discriminative, user-owned facts that
could materially personalize future answers. Preserve diversity within and
across domains. A family member or partner may be selected only when it
establishes the user's relationship/family structure or a constraint on the user.

Reject forget requests, assistant advice, generic questions or curiosity without
a personal fact, temporary logistics, one-off writing/translation requests, and
facts mainly about another person. Do not infer, summarize, rewrite, or create
facts. An entry may appear in at most one domain. Output entry numbers only."""


def _parse_json_object(text: str) -> Optional[Dict[str, Any]]:
    value = str(text or "").strip()
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", value, re.S | re.I)
    payload = match.group(1) if match else value
    try:
        parsed = json.loads(payload)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        try:
            from json_repair import repair_json
            parsed = repair_json(payload, return_objects=True)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None


def normalize_catalog_selection(
    raw: Optional[Dict[str, Any]],
    *,
    candidate_count: int,
    max_per_domain: int = 4,
    max_total: int = 32,
) -> Dict[str, List[int]]:
    source = raw.get("domains") if isinstance(raw, dict) else None
    if not isinstance(source, dict) and isinstance(raw, dict):
        if any(domain in raw for domain in CATALOG_DOMAINS):
            source = raw
    if not isinstance(source, dict):
        source = {}
    output: Dict[str, List[int]] = {domain: [] for domain in CATALOG_DOMAINS}
    used = set()
    for domain in CATALOG_DOMAINS:
        for value in source.get(domain) or []:
            try:
                index = int(value)
            except (TypeError, ValueError):
                continue
            if not 1 <= index <= candidate_count or index in used:
                continue
            used.add(index)
            output[domain].append(index)
            if len(output[domain]) >= max_per_domain or len(used) >= max_total:
                break
        if len(used) >= max_total:
            break
    return output


def prepare_catalog_entries(
    domains: Dict[str, List[int]],
    facts: List[Any],
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    selected: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    for domain in CATALOG_DOMAINS:
        for index in domains.get(domain) or []:
            node = facts[index - 1]
            content = " ".join(str(getattr(node, "content", "") or "").split()).strip()
            reason = catalog_reject_reason(content)
            record = {
                "domain": domain,
                "source_index": index,
                "node_id": str(getattr(node, "node_id", "") or ""),
                "content": content,
                "tags": [str(value) for value in getattr(node, "tags", None) or []],
            }
            if reason:
                rejected.append({**record, "reason": reason})
            else:
                selected.append(record)
    return selected, rejected


def compact_verified_catalog_entries(
    entries: Iterable[Dict[str, Any]],
    *,
    max_per_domain: int = CATALOG_MAX_PER_DOMAIN,
    max_total: int = CATALOG_MAX_TOTAL,
) -> List[Dict[str, Any]]:
    """Keep the verified model order, capped uniformly across fixed domains.

    Compaction intentionally happens after deterministic reject/forget checks.
    This lets a later valid model nominee backfill an invalid earlier nominee,
    matching the validated dev322 offline mechanism.
    """
    grouped = {domain: [] for domain in CATALOG_DOMAINS}
    for entry in entries:
        domain = str(entry.get("domain") or "")
        if domain in grouped:
            grouped[domain].append(dict(entry))
    output: List[Dict[str, Any]] = []
    for domain in CATALOG_DOMAINS:
        for entry in grouped[domain][:max(0, int(max_per_domain))]:
            if len(output) >= max(0, int(max_total)):
                return output
            output.append(entry)
    return output


def render_catalog_content(entries: Iterable[Dict[str, Any]]) -> str:
    return "System2 verified persona catalog:\n" + "\n".join(
        f"- [{entry['domain']}] {entry['content']}" for entry in entries
    )


def parse_rendered_catalog_entries(content: str) -> List[Dict[str, str]]:
    """Parse this module's exact line-oriented visible catalog format."""
    marker = "System2 verified persona catalog:"
    value = str(content or "")
    if marker not in value:
        return []
    output: List[Dict[str, str]] = []
    for line in value.split(marker, 1)[1].strip().splitlines():
        match = _CATALOG_ITEM_RE.match(line.strip())
        if not match:
            continue
        output.append({"domain": match.group(1), "content": match.group(2)})
    return output


def catalog_evidence_fingerprint(entries: Iterable[Dict[str, Any]]) -> str:
    values = [
        (str(entry.get("domain") or ""), str(entry.get("node_id") or ""))
        for entry in entries
    ]
    encoded = json.dumps(values, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _catalog_node_id(
    user_id: str,
    source_fingerprint: str,
    evidence_fingerprint: str,
) -> str:
    value = (
        f"balanced-catalog:v{CATALOG_VERSION}:user-profile:{user_id}:"
        f"{source_fingerprint}:{evidence_fingerprint}"
    )
    return f"catalog_{uuid.uuid5(uuid.NAMESPACE_URL, value).hex}"


async def _active_catalogs(
    graph_store: GraphStoreBase,
    *,
    user_id: str,
) -> List[MemoryNode]:
    canonical = await graph_store.get_all_nodes(
        isolation_key=MemoryNode.build_isolation_key(user_id, ""),
        layer=MemoryLayer.L6_SCHEMA,
        status=MemoryStatus.ACTIVE,
        limit=200,
    )
    nodes_by_id = {node.node_id: node for node in canonical}
    get_by_user = getattr(graph_store, "get_nodes_by_user", None)
    if callable(get_by_user):
        for node in await get_by_user(
            user_id=user_id,
            layer=MemoryLayer.L6_SCHEMA,
            status=MemoryStatus.ACTIVE,
            limit=1000,
        ):
            nodes_by_id[node.node_id] = node
    return [
        node for node in nodes_by_id.values()
        if is_balanced_catalog_custom(node.custom)
    ]


async def _adopt_cloned_catalog(
    *,
    current: Sequence[MemoryNode],
    facts: List[Any],
    source_fingerprint: str,
    user_id: str,
    vector_store: VectorStoreBase,
    graph_store: GraphStoreBase,
) -> Optional[Dict[str, Any]]:
    """Adopt a correctly remapped clone without re-running catalog selection.

    Clone remaps VDB/Graph node ids and DERIVED_FROM edges, but historical
    ``custom.domains`` values and the source fingerprint still reference the
    source namespace.  A normal same-namespace bank change keeps those custom
    evidence ids valid, so adoption is allowed only when *all* recorded custom
    evidence ids are stale while every visible exact catalog line maps uniquely
    to a current active/latest L2 fact.  This preserves the frozen catalog
    selection across clone namespaces without masking real bank changes.
    """
    fact_by_content: Dict[str, List[Any]] = {}
    current_ids = {str(getattr(node, "node_id", "") or "") for node in facts}
    for fact in facts:
        content = " ".join(str(getattr(fact, "content", "") or "").split()).strip()
        fact_by_content.setdefault(content, []).append(fact)

    for old in current:
        custom = dict(old.custom or {})
        if int(custom.get("balanced_catalog_version") or 0) != CATALOG_VERSION:
            continue
        recorded_ids = {
            str(node_id)
            for values in (custom.get("domains") or {}).values()
            for node_id in values or []
            if node_id
        }
        legacy_agent_scope = bool(str(getattr(old, "agent_id", "") or "").strip())
        if not recorded_ids:
            continue
        if not legacy_agent_scope and any(
            node_id in current_ids for node_id in recorded_ids
        ):
            continue
        parsed = parse_rendered_catalog_entries(old.content)
        expected_count = int(custom.get("selected_count") or 0)
        if not parsed or len(parsed) != expected_count:
            continue
        mapped: List[Dict[str, Any]] = []
        valid = True
        for entry in parsed:
            matches = fact_by_content.get(entry["content"]) or []
            if len(matches) != 1 or entry["domain"] not in CATALOG_DOMAINS:
                valid = False
                break
            fact = matches[0]
            mapped.append({
                "domain": entry["domain"],
                "node_id": str(fact.node_id),
                "content": entry["content"],
                "tags": [str(value) for value in fact.tags or []],
            })
        if not valid:
            continue
        evidence_fingerprint = catalog_evidence_fingerprint(mapped)
        node_id = _catalog_node_id(
            user_id, source_fingerprint, evidence_fingerprint,
        )
        mapped_ids = {entry["node_id"] for entry in mapped}
        node = MemoryNode(
            node_id=node_id,
            user_id=user_id,
            agent_id="",
            source=merge_sources(
                getattr(fact, "source", None)
                for fact in facts
                if str(getattr(fact, "node_id", "") or "") in mapped_ids
            ),
            layer=MemoryLayer.L6_SCHEMA,
            content=str(old.content or "").strip(),
            confidence=1.0,
            status=MemoryStatus.ACTIVE,
            source_type=SourceType.COMPOSITE,
            memory_at=datetime.now(),
            tags=["balanced-persona-catalog"],
            evidence_chain=[entry["node_id"] for entry in mapped],
            custom={
                **custom,
                "schema_type": "internal_profile_chain",
                "schema_kind": CATALOG_SCHEMA_KIND,
                "balanced_catalog_version": CATALOG_VERSION,
                "source_bank_fingerprint": source_fingerprint,
                "evidence_fingerprint": evidence_fingerprint,
                "selected_count": len(mapped),
                "domains": {
                    domain: [
                        entry["node_id"] for entry in mapped
                        if entry["domain"] == domain
                    ]
                    for domain in CATALOG_DOMAINS
                },
                "clone_adoption_version": "exact-content-remap-v1",
                "profile_scope": "user",
                "retrieval_text": "",
            },
        )
        created_new = await graph_store.get_node(node_id) is None
        if created_new:
            await graph_store.upsert_memory_node(node)
        try:
            for evidence_id in node.evidence_chain or []:
                evidence_node = await vector_store.get_by_id(evidence_id)
                if evidence_node is None:
                    raise RuntimeError(f"adopted catalog evidence missing: {evidence_id}")
                await graph_store.ensure_vdbref(evidence_id, evidence_node.layer.value)
                if await graph_store.add_derived_from(node_id, evidence_id) is False:
                    raise RuntimeError(f"adopted catalog link failed: {evidence_id}")
        except Exception:
            if created_new:
                await graph_store.delete_node(node_id)
            continue

        retired: List[str] = []
        retirement_failed = False
        for candidate in current:
            if candidate.node_id == node_id:
                continue
            ok = await graph_store.update_node(candidate.node_id, {
                "status": MemoryStatus.SUPERSEDED.value,
                "is_latest": False,
                "valid_until": datetime.now(),
            })
            if not ok:
                retirement_failed = True
                break
            retired.append(candidate.node_id)
        if retirement_failed:
            for retired_id in retired:
                await graph_store.update_node(retired_id, {
                    "status": MemoryStatus.ACTIVE.value,
                    "is_latest": True,
                    "valid_until": None,
                })
            if created_new:
                await graph_store.delete_node(node_id)
            return None
        return {
            "success": True,
            "enabled": True,
            "skipped": True,
            "reason": "cloned_catalog_adopted",
            "created": [{
                "node_id": node_id,
                "evidence_list": list(node.evidence_chain or []),
                "selected_count": len(mapped),
            }] if created_new else [],
            "catalog_node_id": node_id,
            "source_count": len(facts),
            "selected_count": len(mapped),
            "retired_catalog_ids": retired,
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }
    return None


async def _run_balanced_catalog_generation_unlocked(
    *,
    user_id: str,
    vector_store: VectorStoreBase,
    graph_store: Optional[GraphStoreBase],
    config: MemoryConfig,
) -> Dict[str, Any]:
    if not balanced_catalog_generation_enabled():
        return {
            "success": True,
            "enabled": False,
            "created": [],
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }
    if graph_store is None or not hasattr(vector_store, "list_by_user"):
        return {
            "success": False,
            "enabled": True,
            "error": "catalog storage unavailable",
            "created": [],
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }

    all_active_l2 = list(await vector_store.list_by_user(
        user_id=user_id,
        status_filter=[MemoryStatus.ACTIVE],
        layers=[MemoryLayer.L2_FACT],
    ))
    forget_targets = collect_forget_targets(all_active_l2)
    facts = eligible_catalog_facts(all_active_l2)
    if not facts:
        return {
            "success": True,
            "enabled": True,
            "skipped": True,
            "reason": "no_eligible_facts",
            "created": [],
            "source_count": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }

    source_fingerprint = source_bank_fingerprint(facts)
    current = await _active_catalogs(
        graph_store, user_id=user_id,
    )
    matching = [
        node for node in current
        if not str(getattr(node, "agent_id", "") or "").strip()
        if int((node.custom or {}).get("balanced_catalog_version") or 0) == CATALOG_VERSION
        and str((node.custom or {}).get("source_bank_fingerprint") or "") == source_fingerprint
    ]
    if matching:
        return {
            "success": True,
            "enabled": True,
            "skipped": True,
            "reason": "current_catalog_exists",
            "created": [],
            "catalog_node_id": matching[0].node_id,
            "source_count": len(facts),
            "selected_count": int((matching[0].custom or {}).get("selected_count") or 0),
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }
    adopted = await _adopt_cloned_catalog(
        current=current,
        facts=facts,
        source_fingerprint=source_fingerprint,
        user_id=user_id,
        vector_store=vector_store,
        graph_store=graph_store,
    )
    if adopted is not None:
        return adopted

    max_source = max(1, int(os.getenv(
        "MEMORY_EVOLING_BALANCED_CATALOG_MAX_FACTS", "120"
    )))
    candidate_facts = facts[:max_source]
    content_chars = max(100, int(os.getenv(
        "MEMORY_EVOLING_BALANCED_CATALOG_CONTENT_CHARS", "180"
    )))
    prompt_lines = ["Recorded facts:"]
    for index, node in enumerate(candidate_facts, 1):
        tags = ", ".join(str(value) for value in list(node.tags or [])[:4])
        prefix = f"[{tags}] " if tags else ""
        content = " ".join(str(node.content or "").split())[:content_chars]
        prompt_lines.append(f"{index}. {prefix}{content}")
    provider = LLMProvider(config)
    try:
        response = await provider.complete_messages(
            messages=[
                {"role": "system", "content": catalog_prompt()},
                {"role": "user", "content": "\n".join(prompt_lines)},
            ],
            max_tokens=max(512, int(os.getenv(
                "MEMORY_EVOLING_BALANCED_CATALOG_MAX_TOKENS", "512"
            ))),
        )
    except Exception as exc:
        return {
            "success": False,
            "enabled": True,
            "error": str(exc),
            "created": [],
            "source_count": len(facts),
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }
    parsed = _parse_json_object(response.content or "")
    if parsed is None:
        return {
            "success": False,
            "enabled": True,
            "error": "invalid balanced catalog JSON",
            "created": [],
            "source_count": len(facts),
            "prompt_tokens": response.prompt_tokens or 0,
            "completion_tokens": response.completion_tokens or 0,
        }
    domains = normalize_catalog_selection(parsed, candidate_count=len(candidate_facts))
    entries, rejected = prepare_catalog_entries(domains, candidate_facts)
    entries, suppressed = suppress_forget_targets(entries, forget_targets)
    entries = compact_verified_catalog_entries(entries)
    if not entries:
        return {
            "success": False,
            "enabled": True,
            "error": "balanced catalog empty after verification",
            "created": [],
            "source_count": len(facts),
            "rejected": rejected,
            "suppressed_forget_target_entries": suppressed,
            "prompt_tokens": response.prompt_tokens or 0,
            "completion_tokens": response.completion_tokens or 0,
        }

    evidence_fingerprint = catalog_evidence_fingerprint(entries)
    node_id = _catalog_node_id(
        user_id, source_fingerprint, evidence_fingerprint,
    )
    content = render_catalog_content(entries)
    evidence_ids = [entry["node_id"] for entry in entries]
    node = MemoryNode(
        node_id=node_id,
        user_id=user_id,
        agent_id="",
        source=merge_sources(
            getattr(fact, "source", None)
            for fact in facts
            if str(getattr(fact, "node_id", "") or "") in set(evidence_ids)
        ),
        layer=MemoryLayer.L6_SCHEMA,
        content=content,
        confidence=1.0,
        status=MemoryStatus.ACTIVE,
        source_type=SourceType.COMPOSITE,
        memory_at=datetime.now(),
        tags=["balanced-persona-catalog"],
        evidence_chain=evidence_ids,
        custom={
            "schema_type": "internal_profile_chain",
            "schema_kind": CATALOG_SCHEMA_KIND,
            "balanced_catalog_version": CATALOG_VERSION,
            "source_bank_fingerprint": source_fingerprint,
            "evidence_fingerprint": evidence_fingerprint,
            "selected_count": len(entries),
            "catalog_max_per_domain": CATALOG_MAX_PER_DOMAIN,
            "catalog_max_total": CATALOG_MAX_TOTAL,
            "catalog_compaction_stage": "after_verifier_and_forget_suppression",
            "forget_suppression_version": "direct-forget-full-anchor-v1",
            "suppressed_forget_target_count": len(suppressed),
            "profile_scope": "user",
            "domains": {
                domain: [
                    entry["node_id"] for entry in entries if entry["domain"] == domain
                ]
                for domain in CATALOG_DOMAINS
            },
            # Empty by design: the node must never enter ordinary semantic L6 recall.
            "retrieval_text": "",
        },
    )

    created_new = await graph_store.get_node(node_id) is None
    if created_new:
        await graph_store.upsert_memory_node(node)
    try:
        for evidence_id in evidence_ids:
            evidence_node = await vector_store.get_by_id(evidence_id)
            if evidence_node is None:
                raise RuntimeError(f"catalog evidence missing: {evidence_id}")
            if (
                evidence_node.user_id != user_id
                or evidence_node.layer != MemoryLayer.L2_FACT
                or evidence_node.status != MemoryStatus.ACTIVE
                or not bool(evidence_node.is_latest)
            ):
                raise RuntimeError(f"catalog evidence outside active user scope: {evidence_id}")
            await graph_store.ensure_vdbref(evidence_id, evidence_node.layer.value)
            linked = await graph_store.add_derived_from(node_id, evidence_id)
            if linked is False:
                raise RuntimeError(f"catalog evidence link failed: {evidence_id}")
    except Exception as exc:
        if created_new:
            await graph_store.delete_node(node_id)
        return {
            "success": False,
            "enabled": True,
            "error": str(exc),
            "created": [],
            "source_count": len(facts),
            "selected_count": len(entries),
            "prompt_tokens": response.prompt_tokens or 0,
            "completion_tokens": response.completion_tokens or 0,
        }

    retired = []
    for old in current:
        if old.node_id == node_id:
            continue
        ok = await graph_store.update_node(old.node_id, {
            "status": MemoryStatus.SUPERSEDED.value,
            "is_latest": False,
            "valid_until": datetime.now(),
        })
        if not ok:
            for retired_id in retired:
                await graph_store.update_node(retired_id, {
                    "status": MemoryStatus.ACTIVE.value,
                    "is_latest": True,
                    "valid_until": None,
                })
            if created_new:
                await graph_store.delete_node(node_id)
            return {
                "success": False,
                "enabled": True,
                "error": f"failed to retire old catalog {old.node_id}",
                "created": [],
                "prompt_tokens": response.prompt_tokens or 0,
                "completion_tokens": response.completion_tokens or 0,
            }
        retired.append(old.node_id)

    return {
        "success": True,
        "enabled": True,
        "created": [{
            "node_id": node_id,
            "evidence_list": evidence_ids,
            "selected_count": len(entries),
        }] if created_new else [],
        "catalog_node_id": node_id,
        "source_count": len(facts),
        "selected_count": len(entries),
        "rejected": rejected,
        "suppressed_forget_target_entries": suppressed,
        "retired_catalog_ids": retired,
        "prompt_tokens": response.prompt_tokens or 0,
        "completion_tokens": response.completion_tokens or 0,
    }


async def run_balanced_catalog_generation(
    *, user_id: str, vector_store: VectorStoreBase,
    graph_store: Optional[GraphStoreBase], config: MemoryConfig,
) -> Dict[str, Any]:
    """Generate the one user-level catalog, serialized by user_id."""
    from .user_profile_scope import user_profile_lock

    async with user_profile_lock(user_id):
        return await _run_balanced_catalog_generation_unlocked(
            user_id=user_id,
            vector_store=vector_store,
            graph_store=graph_store,
            config=config,
        )


async def get_active_balanced_catalog(
    graph_store: Optional[GraphStoreBase],
    *,
    user_id: str,
) -> Optional[MemoryNode]:
    if not balanced_catalog_l0_enabled() or graph_store is None or not user_id:
        return None
    nodes = await _active_catalogs(
        graph_store, user_id=user_id,
    )
    if not nodes:
        return None
    nodes.sort(
        key=lambda node: (
            not bool(str(getattr(node, "agent_id", "") or "").strip()),
            getattr(node, "gmt_created", None) or datetime.min,
            node.node_id,
        ),
        reverse=True,
    )
    return nodes[0]


def attach_balanced_catalog_to_l0(
    l0_results: List[Dict[str, Any]],
    catalog: Optional[MemoryNode],
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    results = [dict(item) for item in l0_results]
    if catalog is None:
        return results, {"enabled": balanced_catalog_l0_enabled(), "attached": False}
    if not results:
        # Some legitimate banks have no extracted L0 basic-profile node.  The
        # catalog is itself a verified high-level memory, so expose it as the
        # one synthetic L0 instead of dropping the entire System2 contribution.
        # This row is read-only and is never persisted back to VDB/Graph.
        synthetic = {
            "node_id": catalog.node_id,
            "node": None,
            "content": str(catalog.content or "").strip(),
            "layer": MemoryLayer.L0_BASIC_INFO.value,
            "score": float(getattr(catalog, "confidence", 1.0) or 1.0),
            "source": "balanced_catalog_synthetic_l0",
            "_user_profile_channel": True,
            "_balanced_catalog_node_id": catalog.node_id,
            "_balanced_catalog_count": int(
                (catalog.custom or {}).get("selected_count") or 0
            ),
        }
        return [synthetic], {
            "enabled": True,
            "attached": True,
            "synthetic_l0": True,
            "catalog_node_id": catalog.node_id,
            "selected_count": synthetic["_balanced_catalog_count"],
            "catalog_chars": len(str(catalog.content or "")),
        }
    if len(results) != 1:
        return results, {
            "enabled": True,
            "attached": False,
            "reason": f"l0_count={len(results)}",
            "catalog_node_id": catalog.node_id,
        }
    item = dict(results[0])
    node = item.get("node")
    original = str(item.get("content") or getattr(node, "content", "") or "").rstrip()
    # Do not mutate ``content`` here. Evolved L0 nodes are rendered later from
    # ``evolution_chain`` and that formatter would overwrite an early content
    # change. Carry an explicit suffix through Stage 12 and append it only after
    # the complete Current/History capsule has been rendered.
    item["_balanced_catalog_suffix"] = str(catalog.content or "").strip()
    item["_balanced_catalog_original_content"] = original
    item["_balanced_catalog_node_id"] = catalog.node_id
    item["_balanced_catalog_count"] = int(
        (catalog.custom or {}).get("selected_count") or 0
    )
    results[0] = item
    return results, {
        "enabled": True,
        "attached": True,
        "synthetic_l0": False,
        "catalog_node_id": catalog.node_id,
        "selected_count": item["_balanced_catalog_count"],
        "catalog_chars": len(str(catalog.content or "")),
    }
