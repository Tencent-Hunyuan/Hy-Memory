from __future__ import annotations

import asyncio
import os
import re
import time
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from one_memory.hy_sdk_types import HySdkMemoryRecord
from one_memory.memory_governance import GovernedMemory, MemoryStatus
from one_memory.semantic_deadline import (
    SemanticMemoryHit,
    SemanticSearch,
    hy_wait_budget,
    ledger_wait_budget,
    normalize_semantic_hits,
    search_within,
)


def recall_blend_mode() -> str:
    """D102: `score` blends the two lanes by their own relevance, `tiered`
    keeps the shipped two-tier order (HY first, keyword after)."""
    raw = os.environ.get("ONE_MEMORY_RECALL_BLEND", "score").strip().lower()
    return raw if raw in {"score", "tiered"} else "score"


class RecallState(StrEnum):
    GROUNDED = "grounded"
    EMPTY = "empty"


class TruncationBudget(StrEnum):
    """Which of the three parallel budgets stopped or capped recall.

    Count, characters, and wall time run together; the receipt names the one
    that actually fired so "I did not finish looking" is never mistaken for
    "you have no such memory".
    """

    COUNT = "count_budget"
    CHARACTER = "character_budget"
    TIMEOUT = "timeout_budget"


class RecallRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    query: str = Field(min_length=1, max_length=2_000)
    scope: str = Field(default="all-apps", min_length=1, max_length=512)
    #: The directory the caller is working in, when it has one.
    #:
    #: A scope string cannot be produced outside Core — it hashes an identity
    #: the importing host chose — so this is how a caller reaches the memories
    #: from the project it is actually in. It can only ever name its own
    #: directory, which is why this does not widen what a host can see: every
    #: other workspace stays as unreachable as before.
    workspace_path: str | None = Field(default=None, max_length=4_096)
    limit: int = Field(default=8, ge=1, le=20)
    character_budget: int = Field(default=4_000, ge=200, le=20_000)
    #: Wall-clock budget for candidate gather + score. Not a substitute for
    #: raising the old newest-500 page; it is the third parallel budget.
    timeout_ms: int = Field(default=2_000, ge=50, le=30_000)


class RecallHit(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    memory_id: str
    content: str
    kind: str
    scopes: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    updated_at: datetime
    score: float = Field(ge=0, le=1)
    #: HY already rendered memory time/evolution/capsule context into content.
    #: Hosts must not append their own governance timestamp in this case.
    #: "raw" is an excerpt of an imported conversation, not a memory.
    content_format: Literal["plain", "hy-sdk", "raw"] = "plain"
    #: Raw excerpts only: the app and conversation the excerpt came from.
    source: str | None = None
    session_id: str | None = None


class RecallReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    state: RecallState
    query: str
    scope: str
    hits: tuple[RecallHit, ...]
    used_characters: int
    truncated: bool
    #: Set whenever `truncated` is true — which budget fired.
    truncated_by: TruncationBudget | None = None
    #: How many scored candidates were left unreturned, plus any known surplus
    #: beyond the SQL candidate cap. Zero when the search finished cleanly.
    unexamined_count: int = Field(default=0, ge=0)
    #: D94: sources the library holds that this host may not read. Only
    #: populated when the result is empty, so "nothing came back" can say
    #: whether the answer is that nothing was found or that reading was not
    #: allowed.
    excluded_sources: tuple[str, ...] = ()
    #: "raw" when recall searched the imported conversations instead of
    #: memories, because no generation model is configured or no memory
    #: exists yet (`fallback_reason`). Raw hits are excerpts, not memories.
    mode: Literal["memory", "raw"] = "memory"
    fallback_reason: Literal["model_unavailable", "no_memories"] | None = None


class RecallTarget(Protocol):
    def recall(self, request: RecallRequest, *, user_id: str = "local") -> RecallReceipt: ...


class SdkMemoryRead(Protocol):
    async def __call__(self, memory_id: str) -> HySdkMemoryRecord | None: ...


class RecallGovernance(Protocol):
    def find_confirmed_matching_terms(
        self,
        *,
        user_id: str,
        terms: Collection[str],
        limit: int = 200,
        scopes: Collection[str] | None = None,
    ) -> tuple[list[GovernedMemory], bool]: ...

    def get(self, memory_id: str, *, user_id: str) -> GovernedMemory | None: ...

    def sources_behind_memories(
        self,
        *,
        user_id: str,
        memory_ids: Collection[str],
    ) -> dict[str, frozenset[str]]: ...


#: The one query answered by this module rather than by searching.
#:
#: A self-check of the recall link has to tell "this link is broken" from
#: "this link works and the library is empty", and searching cannot: a new
#: library really is empty, so both come back with nothing and look the same.
#: Three hosts once sat on a wrong discovery path looking perfectly healthy
#: for exactly this reason, which is what the self-check exists to catch.
#:
#: Matched on exact equality only, and the answer below is hardcoded, so it
#: cannot become a way to ask for anything other than itself.
PROBE_QUERY = "__om_probe__"

#: Fixed, because the point is that the answer owes nothing to the library.
_PROBE_ANSWER = "MemHub 链路自检正常"

#: Cap on SQL-matched candidates to score. Filters first; this is not
#: "the newest N memories in the whole library".
_CANDIDATE_LIMIT = 200
#: Candidate pages are widened in this step until the post-candidate filter
#: yields something readable or the store's own hard cap is reached. This is
#: still a bounded search: the limit is not a promise that a later page was
#: never inspected, and `more=True` keeps the receipt honest when it was.
_CANDIDATE_PAGE_STEP = 600
_CANDIDATE_HARD_LIMIT = 2_000
#: Past the deadline, how long a ranking that checks the deadline itself still
#: gets to hand back its own timeout receipt before recall fails closed.
_RANK_HANDOFF_S = 0.05


def _probe_receipt(request: RecallRequest) -> RecallReceipt:
    """The self-check answer: proof of a working link, and nothing else.

    Deliberately not a memory. It has no id to trace, spends none of the
    budget, and is not counted, so that nothing totting up what recall has
    returned mistakes a self-check for a recall the user asked for.
    """
    return RecallReceipt(
        state=RecallState.GROUNDED,
        query=request.query,
        scope=request.scope,
        hits=(
            RecallHit(
                memory_id="",
                content=_PROBE_ANSWER,
                kind="self_check",
                scopes=(),
                evidence_ids=(),
                # Fixed rather than "now", so two self-checks are identical
                # and neither can be read as recent activity.
                updated_at=datetime(1970, 1, 1, tzinfo=UTC),
                score=1.0,
            ),
        ),
        used_characters=0,
        truncated=False,
    )


class SdkRecallTarget:
    """Host-authorized recall over SDK-owned memory bodies and lifecycle."""

    def __init__(
        self,
        *,
        semantic_search: SemanticSearch,
        get_memory: SdkMemoryRead,
    ) -> None:
        self.semantic_search = semantic_search
        self.get_memory = get_memory

    async def recall_async(
        self,
        request: RecallRequest,
        *,
        user_id: str = "local",
    ) -> RecallReceipt:
        if request.query == PROBE_QUERY:
            return _probe_receipt(request)
        budget = request.timeout_ms / 1_000
        deadline = time.monotonic() + budget
        semantic_hits, outcome = await search_within(
            self.semantic_search,
            request.query,
            hy_wait_budget(budget),
        )
        hits: list[RecallHit] = []
        used = 0
        truncated_by: TruncationBudget | None = (
            TruncationBudget.TIMEOUT if outcome == "timed_out" else None
        )
        unexamined = 0
        for index, hit in enumerate(semantic_hits):
            if time.monotonic() >= deadline:
                truncated_by = TruncationBudget.TIMEOUT
                unexamined = len(semantic_hits) - index
                break
            remaining = max(0.0, deadline - time.monotonic())
            try:
                memory = await asyncio.wait_for(
                    self.get_memory(hit.memory_id),
                    timeout=remaining,
                )
            except TimeoutError:
                truncated_by = TruncationBudget.TIMEOUT
                unexamined = len(semantic_hits) - index
                break
            if memory is None or memory.status != "active":
                continue
            formatted = (
                hit.formatted_content
                if hit.sdk_formatted and hit.formatting_complete and hit.formatted_content
                else None
            )
            content = formatted or memory.content
            if len(hits) >= request.limit:
                truncated_by = TruncationBudget.COUNT
                unexamined = len(semantic_hits) - index
                break
            if used + len(content) > request.character_budget:
                truncated_by = TruncationBudget.CHARACTER
                continue
            created_at = (
                datetime.fromtimestamp(memory.gmt_created, tz=UTC)
                if memory.gmt_created is not None
                else datetime(1970, 1, 1, tzinfo=UTC)
            )
            hits.append(
                RecallHit(
                    memory_id=memory.memory_id,
                    content=content,
                    kind=memory.layer,
                    scopes=(),
                    evidence_ids=(),
                    updated_at=created_at,
                    score=hit.score,
                    content_format="hy-sdk" if formatted is not None else "plain",
                )
            )
            used += len(content)
        return RecallReceipt(
            state=RecallState.GROUNDED if hits else RecallState.EMPTY,
            query=request.query,
            scope=request.scope,
            hits=tuple(hits),
            used_characters=used,
            truncated=truncated_by is not None,
            truncated_by=truncated_by,
            unexamined_count=unexamined,
        )


class GovernedRecallTarget:
    """Read-only, scope-bound recall over user-confirmed governed memories."""

    def __init__(
        self,
        governance: RecallGovernance,
        semantic_search: SemanticSearch | None = None,
        resolve_workspace: Callable[[str], tuple[str, ...]] | None = None,
        filter_memories: Callable[[Sequence[GovernedMemory]], Sequence[GovernedMemory]]
        | None = None,
    ) -> None:
        self.governance = governance
        self.semantic_search = semantic_search
        self.resolve_workspace = resolve_workspace
        self.filter_memories = filter_memories or tuple

    def recall(self, request: RecallRequest, *, user_id: str = "local") -> RecallReceipt:
        if request.query == PROBE_QUERY:
            return _probe_receipt(request)
        deadline = time.monotonic() + request.timeout_ms / 1_000
        readable = self._readable_scopes(request)
        return self._rank(
            request,
            user_id=user_id,
            readable=readable,
            semantic_hits={},
            keywords=self._keyword_candidates(
                request, user_id=user_id, readable=readable, deadline=deadline
            ),
            deadline=deadline,
            timed_out=False,
        )

    async def recall_async(
        self,
        request: RecallRequest,
        *,
        user_id: str = "local",
    ) -> RecallReceipt:
        # Ahead of the search, which is the whole point: answered by searching
        # this would cost an embedding call and leave the self-check's own
        # words wherever real recalls are recorded.
        if request.query == PROBE_QUERY:
            return _probe_receipt(request)
        # One deadline for the whole recall, HY included: a caller that asked
        # for 1.5 s and gave up at 2.5 s used to lose even the keyword answer
        # to a 3 s HY wait it never agreed to. HY starts at once; every ledger
        # step is a blocking read that cannot be interrupted, so each is waited
        # on only until the deadline and then left to finish on its own. A step
        # whose output screens what may be returned fails the recall closed.
        budget = request.timeout_ms / 1_000
        deadline = time.monotonic() + budget
        semantic = asyncio.ensure_future(
            search_within(self.semantic_search, request.query, hy_wait_budget(budget))
        )
        steps: list[asyncio.Future[Any]] = [semantic]
        try:
            scopes = asyncio.ensure_future(asyncio.to_thread(self._readable_scopes, request))
            steps.append(scopes)
            await asyncio.wait({scopes}, timeout=ledger_wait_budget(deadline - time.monotonic()))
            if not scopes.done():
                return _timed_out_receipt(request, unexamined=1)
            readable = scopes.result()
            keywords = asyncio.ensure_future(
                asyncio.to_thread(
                    self._keyword_candidates,
                    request,
                    user_id=user_id,
                    readable=readable,
                    deadline=deadline,
                )
            )
            steps.append(keywords)
            semantic_hits, outcome = await semantic
            semantic_by_id = {hit.memory_id: hit for hit in semantic_hits}
            # A keyword read stuck behind a writer must not hold back HY's
            # answer past the caller's deadline; its rows are dropped instead.
            await asyncio.wait({keywords}, timeout=ledger_wait_budget(deadline - time.monotonic()))
            if keywords.done():
                found = keywords.result()
            else:
                found = _KeywordCandidates((), more=False, timed_out=True)
            # A failed HY is not marked: the receipt has no field that can say
            # "HY failed" without claiming a budget fired.
            ranking = asyncio.ensure_future(
                asyncio.to_thread(
                    self._rank,
                    request,
                    user_id=user_id,
                    readable=readable,
                    semantic_hits=semantic_by_id,
                    keywords=found,
                    deadline=deadline,
                    timed_out=outcome == "timed_out",
                )
            )
            steps.append(ranking)
            await asyncio.wait(
                {ranking}, timeout=max(0.0, deadline - time.monotonic()) + _RANK_HANDOFF_S
            )
            if ranking.done():
                return ranking.result()
            # Filtering did not finish, so nothing it was meant to screen may
            # leave: never return unfiltered.
            return _timed_out_receipt(
                request, unexamined=max(1, len(semantic_by_id) + len(found.memories))
            )
        finally:
            for step in steps:
                if not step.done():
                    step.cancel()

    def _readable_scopes(self, request: RecallRequest) -> frozenset[str] | None:
        """The scopes this request may read, or None for the wildcard.

        The caller's own scope plus whatever its directory is known as. It
        cannot name a directory it is not in, so this stays within the
        boundary the workspace design draws.
        """
        if request.scope == "*":
            return None
        scopes = {request.scope}
        if request.workspace_path and self.resolve_workspace is not None:
            scopes.update(self.resolve_workspace(request.workspace_path))
        return frozenset(scopes)

    def _keyword_candidates(
        self,
        request: RecallRequest,
        *,
        user_id: str,
        readable: frozenset[str] | None,
        deadline: float,
    ) -> _KeywordCandidates:
        """Content filters pushed into SQL.

        Scoring a newest-500 page first is what made buried matches look like
        they never existed.
        """
        query_terms = _terms(request.query)
        if not query_terms:
            return _KeywordCandidates((), more=False, timed_out=False)
        if time.monotonic() >= deadline:
            return _KeywordCandidates((), more=False, timed_out=True)
        # Scopes are already pushed into SQL. Revocation and freshness are
        # necessarily a post-candidate filter today, so widen the candidate
        # page when that filter removes every row. Otherwise 200 revoked-source
        # matches with smaller ids can make one readable match unreachable while
        # the receipt says empty. Every intermediate page is filtered before we
        # decide to widen; the hard cap preserves the common bounded path and
        # `more` remains true if the readable denominator is still not covered.
        limit = _CANDIDATE_LIMIT
        while True:
            matches, more = self.governance.find_confirmed_matching_terms(
                user_id=user_id,
                terms=query_terms,
                limit=limit,
                scopes=readable,
            )
            scoped = tuple(
                memory
                for memory in matches
                if readable is None or not readable.isdisjoint(memory.scopes)
            )
            filtered = tuple(self.filter_memories(scoped))
            if filtered or not more or limit >= _CANDIDATE_HARD_LIMIT:
                break
            if time.monotonic() >= deadline:
                return _KeywordCandidates((), more=more, timed_out=True)
            limit = min(limit + _CANDIDATE_PAGE_STEP, _CANDIDATE_HARD_LIMIT)
        return _KeywordCandidates(
            filtered,
            more=more,
            timed_out=time.monotonic() >= deadline,
        )

    def _rank(
        self,
        request: RecallRequest,
        *,
        user_id: str,
        readable: frozenset[str] | None,
        semantic_hits: Mapping[str, SemanticMemoryHit],
        keywords: _KeywordCandidates,
        deadline: float,
        timed_out: bool,
    ) -> RecallReceipt:
        timed_out = timed_out or keywords.timed_out
        query_terms = _terms(request.query)
        semantic_rank = dict(semantic_hits)
        candidates: dict[str, GovernedMemory] = {}

        # Semantic hits are fetched by id. They must not depend on appearing
        # in any newest-N page — that was how a first-ranked HY id still
        # produced empty once the library grew past 500.
        for memory_id in semantic_rank:
            if time.monotonic() >= deadline:
                timed_out = True
                break
            memory = self.governance.get(memory_id, user_id=user_id)
            if memory is None or memory.status is not MemoryStatus.CONFIRMED:
                continue
            if readable is not None and readable.isdisjoint(memory.scopes):
                continue
            candidates[memory.memory_id] = memory
        for memory in keywords.memories:
            candidates.setdefault(memory.memory_id, memory)

        # Two tiers, not one blended score. HY decides the order of what it
        # found; the ledger only removes what this caller may not see. Keyword
        # matches HY did not return follow after every HY hit, however many
        # words they share with the query, and never repeat one (candidates
        # are keyed by memory id).
        # Stopgap: the keyword tier only reaches memories written without
        # going through HY. Remove it once every memory write goes through HY.
        # D102: HY's real score is carried by `search_scores`; the HY-side
        # relevance floor is 0.35 (configurable) while the keyword lane keeps
        # its own 0.25 singleton gate.
        # D102: `score` blends the two lanes by their own relevance — HY's
        # score and the keyword overlap are both 0..1 — while `tiered` keeps
        # the shipped two-tier order (HY first, keyword after).
        blend = recall_blend_mode()
        scored: list[tuple[float, datetime, GovernedMemory]] = []
        filtered = tuple(self.filter_memories(tuple(candidates.values())))
        excluded_ids = set(candidates) - {memory.memory_id for memory in filtered}
        sources = self.governance.sources_behind_memories(
            user_id=user_id,
            memory_ids=tuple(excluded_ids),
        )
        from_hy: list[tuple[int, GovernedMemory]] = []
        from_keywords: list[tuple[float, GovernedMemory]] = []
        excluded_sources: set[str] = {
            source for memory_id in excluded_ids for source in sources.get(memory_id, frozenset())
        }
        for memory in filtered:
            if time.monotonic() >= deadline:
                timed_out = True
                break
            if readable is not None and readable.isdisjoint(memory.scopes):
                # D94: the library holds this memory, but this host may not
                # read it. Only worth naming when the answer is empty —
                # otherwise the person is reading results, not a grant report.
                excluded_sources.update(sources.get(memory.memory_id, frozenset()))
            semantic_hit = semantic_rank.get(memory.memory_id)
            if semantic_hit is not None:
                if blend == "score":
                    scored.append((semantic_hit.score, memory.updated_at, memory))
                else:
                    from_hy.append((len(from_hy), memory))
                continue
            overlap = query_terms & _terms(memory.content)
            lexical_score = len(overlap) / max(1, len(query_terms))
            if not overlap or (len(overlap) < 2 and lexical_score < 0.25):
                continue
            if blend == "score":
                scored.append((lexical_score, memory.updated_at, memory))
            else:
                from_keywords.append((lexical_score, memory))
        if blend == "score":
            scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
            ranked = [(score, memory) for score, _updated, memory in scored]
        else:
            from_hy.sort(key=lambda item: item[0])
            from_keywords.sort(key=lambda item: (item[0], item[1].updated_at), reverse=True)
            ranked = [
                (0.5 + 0.5 * (1 - index / max(1, len(semantic_rank))), memory)
                for index, memory in from_hy
            ] + [(0.5 * score, memory) for score, memory in from_keywords]

        hits: list[RecallHit] = []
        used = 0
        # A timeout outranks the other budgets once it has fired: "did not
        # finish looking" is the one a caller must not mistake for "no more".
        truncated_by: TruncationBudget | None = TruncationBudget.TIMEOUT if timed_out else None
        remaining = 0
        for index, (score, memory) in enumerate(ranked):
            if time.monotonic() >= deadline:
                truncated_by = TruncationBudget.TIMEOUT
                remaining = len(ranked) - index
                break
            if len(hits) >= request.limit:
                if truncated_by is not TruncationBudget.TIMEOUT:
                    truncated_by = TruncationBudget.COUNT
                remaining = len(ranked) - index
                break
            semantic_hit = semantic_rank.get(memory.memory_id)
            formatted = self._sdk_formatted_content(
                semantic_hit,
                memory,
                user_id=user_id,
                readable=readable,
            )
            content = formatted or memory.content
            if used + len(content) > request.character_budget:
                # Skip the oversized hit and keep looking for a smaller one,
                # but the character budget has already fired.
                if truncated_by is not TruncationBudget.TIMEOUT:
                    truncated_by = TruncationBudget.CHARACTER
                continue
            hits.append(
                RecallHit(
                    memory_id=memory.memory_id,
                    content=content,
                    kind=memory.kind,
                    scopes=memory.scopes,
                    evidence_ids=memory.evidence_ids,
                    updated_at=memory.updated_at,
                    score=score,
                    content_format="hy-sdk" if formatted is not None else "plain",
                )
            )
            used += len(content)

        if keywords.more and truncated_by is None:
            # Candidate pool capped while matches remain — that is truncation
            # even when the returned hits filled cleanly within count/chars.
            truncated_by = TruncationBudget.COUNT
        if keywords.more:
            remaining = max(remaining, 1)

        truncated = truncated_by is not None
        return RecallReceipt(
            state=RecallState.GROUNDED if hits else RecallState.EMPTY,
            query=request.query,
            scope=request.scope,
            hits=tuple(hits),
            used_characters=used,
            truncated=truncated,
            truncated_by=truncated_by,
            unexamined_count=remaining,
            excluded_sources=tuple(sorted(excluded_sources)) if not hits else (),
        )

    def _sdk_formatted_content(
        self,
        hit: SemanticMemoryHit | None,
        root: GovernedMemory,
        *,
        user_id: str,
        readable: frozenset[str] | None,
    ) -> str | None:
        """Use SDK rendering only after current governance validates every line."""
        if (
            hit is None
            or not hit.sdk_formatted
            or not hit.formatting_complete
            or not hit.formatted_content
        ):
            return None
        identifiers = tuple(dict.fromkeys((root.memory_id, *hit.member_ids)))
        members: list[GovernedMemory] = []
        for memory_id in identifiers:
            memory = (
                root
                if memory_id == root.memory_id
                else self.governance.get(memory_id, user_id=user_id)
            )
            if memory is None or memory.status is MemoryStatus.DELETED:
                return None
            if readable is not None and readable.isdisjoint(memory.scopes):
                return None
            members.append(memory)
        filtered = tuple(self.filter_memories(tuple(members)))
        if {memory.memory_id for memory in filtered} != set(identifiers):
            return None
        # A correction may have landed in the ledger after HY answered. Never
        # reintroduce the stale SDK text while the background index catches up.
        if any(memory.content not in hit.formatted_content for memory in members):
            return None
        return hit.formatted_content


def _timed_out_receipt(request: RecallRequest, *, unexamined: int) -> RecallReceipt:
    return RecallReceipt(
        state=RecallState.EMPTY,
        query=request.query,
        scope=request.scope,
        hits=(),
        used_characters=0,
        truncated=True,
        truncated_by=TruncationBudget.TIMEOUT,
        unexamined_count=unexamined,
    )


@dataclass(frozen=True)
class _KeywordCandidates:
    memories: tuple[GovernedMemory, ...]
    more: bool
    timed_out: bool


def _as_scores(hits: Sequence[object]) -> dict[str, float]:
    """Compatibility helper retained for callers/tests that inspect scores."""
    normalized = normalize_semantic_hits(hits)
    return {} if normalized is None else {hit.memory_id: hit.score for hit in normalized}


def _terms(text: str) -> set[str]:
    normalized = text.casefold()
    latin = set(re.findall(r"[a-z0-9][a-z0-9_-]{1,}", normalized))
    chinese_runs = re.findall(r"[\u3400-\u9fff]+", normalized)
    chinese = {
        run[index : index + 2] for run in chinese_runs for index in range(max(0, len(run) - 1))
    }
    chinese.update(
        run for run in chinese_runs if len(run) == 1 and run not in _SINGLE_CJK_STOP_TERMS
    )
    return latin | chinese


_SINGLE_CJK_STOP_TERMS = frozenset(
    "我你他她它的了是在和与或这那也都很没有就还而但却因且把被从给到又著着过啊呢吗吧"
)
