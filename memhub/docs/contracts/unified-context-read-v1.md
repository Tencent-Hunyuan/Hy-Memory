# Unified Context Read v1


> **架构变更（2026-10-21）：** 现行权威见 [`../ARCHITECTURE.md`](../ARCHITECTURE.md) **Top Rules**。~~MemHub Evidence / `governed_*` 作原文或 memory 权威~~ 已废弃为目标；memory 读写与 backend 只经 SDK；MemHub 可存 `memory_id` 引用。下文未逐段删线的历史表述，凡与 Top Rules 冲突处以架构文档为准。

HC-06/08/09/10 and D47 integration slice. Implementation in progress, not host
installation or real-data acceptance. No new user approval is inferred from this
technical contract; the unified-read direction was explicitly approved.

## Frozen Integration Surface

- New `POST /api/v1/context/search`; proposed host tool `om_context` consumes it.
  Existing recall/conversations tools retain their permission and response shape.
- Pydantic source of truth: `context_models.py`. Request has query, kinds,
  **raw_source_ids**, total limit, character_budget and timeout_ms. The renamed
  source filter deliberately affects raw only, never silently filters memories.
- Strict limits: query 1..2000 nonblank, kinds unique/raw-memory, raw sources
  1..32 exact IDs, total results 1..20, text 200..20000, deadline 50..30000ms.
  No caller user/workspace/installation/permission fields. Unknown fields reject.
- Envelope remains existing broker identity and existing underlying tools. At
  least one requested path must be authorized. Raw needs om_conversations plus
  all-apps and source grants; memory needs om_recall plus an existing all-apps or
  workspace scope. Source scopes alone never authorize arbitrary memories.
- Explicit raw source filter outside current allowed sources is 403. A missing
  optional lane is not_authorized without querying it. No legal requested lane
  means 403. Invalid external envelope/changed installation must reject wholesale.
- Local authenticated calls may read the existing local data instance; they do
  not scan source directories, create grants, enqueue HY, reindex or create a
  persistent event. Legacy recall's background reindex is not invoked here.

## Read Adapters and Budgets

Two read-only adapters take the validated request, an absolute monotonic deadline
and trusted authority arguments. Raw adapter receives explicit permitted source
IDs (None only for trusted local); memory receives permitted memory scopes (None
only local). They return ContextLaneResult and never log private content.

All permission/deleted/user predicates precede candidate limits.

~~Use existing SQLite/FTS and governance semantics, not a new memory engine.~~  
**新：** memory 车道走 **SDK search/get**（可加 MemHub 授权与 `memory_id` 过滤）；~~Governance SQLite 正文语义~~ 不作权威。raw 车道目标改为 **SDK raw**（迁移完成前实现可能仍读旧表，以架构 Top Rules 为准）。

Read connections check deadline/progress; a timed-out thread must not run unbounded.
~~Raw returns… evidence revision/author. Memory returns current confirmed content with revision and bounded evidence IDs…~~  
**新：** raw/memory 正文与身份以 SDK 为准；MemHub 只做授权与组织引用。Exact query / lexical terms 可作为辅助，不替代 SDK 语义检索合同。

One aggregate limit and character budget applies to both lanes, fairly interleaved
when both have results. Count raw message text and memory content once in the
emitted items. Preserve anchors and reference identities when truncating; no
silent suppression without path truncation metadata. Shared deadline includes
both adapters and final current-version/source authorization checks. No late
result is released after cancellation. Metadata bounds are explicit in models.

Adapters must revalidate selected evidence/memory IDs before release. Cross-lane
final revalidation runs after both lanes finish so a revocation during a slower
lane cannot leak an earlier result. Installation-revision final guard belongs
to the Broker, not an untrusted request payload.

Response schema1 has request_id, state, raw/memory paths, discriminated items,
actual used_characters, truncation, elapsed time, adoption=unknown. Normal empty
differs from unavailable; any incomplete requested path yields partial when some
authorized query completed, even with no hits. All allowed reads failing yields
unavailable. Safe fixed reason codes only; no private candidate counts/errors.

`used_characters` counts Unicode code points, matching Python's `len()`. It is
not an encoding length and not a UTF-16 code-unit count, so a client in another
language must not compare it against `String.length`: an emoji outside the BMP
is one character here and two there.

## Ownership and Verification

Main owns models/API/external route guard/TS and integration. Workers own new
raw adapter, memory adapter or aggregator/test files only, no existing schema,
recall.py or model-processing changes. Other task owns Host integration in its
separate worktree, consuming this contract after acknowledgment.

Tests require no-model/no-HY, denied-lane zero invocation, source/user/workspace
isolation before limits, revision/revoke races, long body/metadata, count/text/
timeout budgets, cancellation, read-only DB, unavailable vs empty, authenticated
route and unchanged legacy guards. Synthetic tests are not actual host adoption.
