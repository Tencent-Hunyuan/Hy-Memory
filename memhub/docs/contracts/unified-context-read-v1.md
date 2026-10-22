# Unified Context Read v1

Status: legacy compatibility wire used only before the SDK data-plane cutover.
After cutover, Host Bridge uses SDK-native Context v2. The two schemas are
validated and returned independently; SDK data is never fabricated into v1
revision or Evidence fields.

## V1 compatibility surface

- `POST /api/v1/context/search`; the host tool `om_context` consumes it.
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

All permission/deleted/user predicates precede candidate limits. V1 uses the
legacy SQLite/FTS and governance semantics that existed before cutover. Read
connections are read-only, check deadline/progress, and must not leave a timed-out
thread running unbounded. Raw returns at most three independently authorized
same-conversation messages per item; excerpts are bounded and carry the actual
legacy evidence revision/author. Memory returns current confirmed content with
revision and bounded evidence IDs, never evidence body without raw authorization.

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

## Verification

Tests require no-model/no-HY, denied-lane zero invocation, source/user/workspace
isolation before limits, revision/revoke races, long body/metadata, count/text/
timeout budgets, cancellation, read-only DB, unavailable vs empty, authenticated
route and unchanged legacy guards. Synthetic tests are not actual host adoption.
