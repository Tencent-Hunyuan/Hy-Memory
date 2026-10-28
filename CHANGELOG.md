# Changelog and Release Status

Hy Memory and MemHub are versioned and released independently.

## Unreleased

- MemHub shows when working and coding experiences were talked about. After
  the SDK upgrade every one of them read "时间未知" (time unknown) because the
  SDK never stored their conversation time; hy-memory 2.0.7 stores it and
  Core now passes it through the memory list, detail, search and home
  summary. An experience stored before 2.0.7 shows its write time, labelled
  "写入于" / "写入时间", instead of "时间未知". Metadata repair also writes
  the conversation time it recovers from the migration bundle into the SDK's
  coding/working stores when they already have the column.
- The SDK migration no longer fails its first run on a home with legacy
  vectors: reattaching them left a Chroma instance open with its own settings,
  and the readback that followed could not open the same store
  ("An instance of Chroma already exists ... with different settings").
- MemHub "添加记忆" (add a memory) no longer waits behind unprocessed history.
  Notes are claimed ahead of the event-time window that keeps history strictly
  old to new; they still run one at a time and in order within their session.
- The SDK memory page now reads its list, count, source and time labels and
  graph from one page response for one source filter, so rows, totals and the
  map no longer disagree. Memories with no recorded source are shown and
  filterable as "unidentified source"; library search keeps only memories
  that contain the words within the SDK's relevance window.
- Metadata repair v2 follows `governed_memory_evidence` across every
  pre-cutover bundle, which restores the source of memories the cutover
  backfilled without one, and moves impossible future conversation times back
  to the write time.
- Reused the existing three-column memory map for SDK-authority list/detail
  views and removed the retired "original evidence" surface. SDK-native status,
  source, conversation time and creation time now render without fabricated
  revisions; source and text filters are applied by the paged Core endpoint.
- SDK memory search now calls the SDK search contract instead of listing the
  entire library in Core. Corrupt empty records are skipped and diagnosed
  without mutating data or failing a page, and post-cutover metadata repair
  restores source and original times from the verified migration bundle.
- Kept Cursor's WAL change token separate from the file mtime so a hash can no
  longer appear as a conversation date centuries in the future.
- A trusted packaged Runtime now repairs a dirty same-build installation by
  staging a clean replacement, and `runtime_service_not_ready` is rendered as
  user-facing startup guidance instead of an internal code.
- Fixed legacy-to-SDK startup migration to adopt native Coding Experience and
  Working Fact IDs instead of importing them as Chat layers. Raw migration now
  rebuilds bounded user/assistant turns from the latest three days, excludes
  tools, and enforces the same rolling retention after cutover.
- Paused host capture writes with a live Desktop migration fence; Cursor
  History catch-up resumes from its existing checkpoint after cutover.
- Removed MemHub's private `hy_memory_bridge` implementation. MemHub now calls
  public `HyMemoryClient` methods at runtime; prompt/reconcile/response
  monkey-patches and private VDB/Coding runtime access were deleted. Kuzu Graph
  projection/admin and stopped-writer App migration are the documented product
  exceptions.
- Added a MemHub-managed native Hermes MemoryProvider with automatic prefetch,
  persistent per-session memory-ID deduplication, authorized Broker reads, and
  recall-context stripping to prevent memory recursion.
- Added an extensible MemHub interface-localization layer with Simplified
  Chinese, English, and Spanish catalogs shared by Web and Electron-native
  surfaces. Interface language remains independent from memory content and
  model processing language.
- Fixed governed-memory revisions and repeated evidence links failing when the
  visibility dirty-marker trigger encounters the same memory twice. Startup
  now replaces the affected legacy trigger without changing schema versions
  or governed and visibility data.
- Kept post-write authority verification failures available for read-only
  reconciliation while preventing automatic or generic confirmed retries from
  invoking the model again. Automatic reconciliation skips those sticky rows
  before applying its batch limit, and linked durable jobs exclusively own
  retry scheduling for their memory tasks.
- Removed MemHub's task-scoped HyPermission store wrappers and global
  model-content consent switch. The trusted embedded Hy Memory SDK now uses
  its native local stores directly.
- Existing installations ignore the retired config flag, readmit previously
  parked pre-invocation work, and drop only the obsolete
  `hy_node_permissions` proof table. Evidence, governed memories, model
  profiles, SDK stores, and source/host grants are preserved.

## Hy Memory 2.0.7 — 2026-10-28

- Published
  [`hy-memory==2.0.7`](https://pypi.org/project/hy-memory/2.0.7/).
- Coding and working experiences and facts now keep their conversation time.
  `add(..., memory_at=...)` reached only the chat path before; the
  productivity path dropped it, so every experience had only its write time
  and MemHub showed them as "时间未知" (time unknown). `coding_memory_meta`
  and `working_fact` gain a nullable `memory_at` column (epoch seconds) in
  both the working and coding stores; existing databases get it on open, and
  their old rows stay `None`. Without `memory_at` a write uses the time the
  request arrived, as chat does. When an experience is updated by a later
  conversation it keeps the later of the two times.
- `list_memories` (`scene="coding"`, `"working"` and `"all"`), `get` and
  `search` return `memory_at` for experiences and facts in the same field and
  format as chat (epoch seconds, `None` when unknown). Experience search
  results now also carry `created_at`.

## Hy Memory 2.0.6 — 2026-10-27

- Published
  [`hy-memory==2.0.6`](https://pypi.org/project/hy-memory/2.0.6/).
- `search(..., rerank=False)` skips every reranking stage for that one
  call (the local cross-encoder, the memory-graph rerank, and the BM25 rerank of
  working/coding facts), so results come back in retrieval-score order. The
  flag travels on the read request, so concurrent searches do not affect each
  other. The default (`True`) keeps current behavior; `/api/v1/search` accepts
  it too.
- MMR diversity selection now runs only when the requested `limit` is
  below 20 (`MMR_MAX_TOP_K`) and is skipped for larger limits, where its
  quadratic token-overlap pass is slow and gains little. Previously it ran for
  every limit. `MEMORY_HYBRID_V2_MMR_LAMBDA` still sets the weight.
  Near-duplicate demotion follows the same gate: it compares each candidate
  with every earlier non-duplicate, which is quadratic in a pool that grows
  with `limit`, and large result pages should show similar items anyway.
- `search(scene="all")` searches chat, working and coding at the same time
  and merges them with Reciprocal Rank Fusion (k=60) over each scene's own
  ranking, so no scene is crowded out by the other scenes' score scales. Each
  result says which scene and kind it came from. A scene that fails or is turned
  off is reported in `errors` and does not fail the search. Source filters apply
  to all three.
- `list_memories(scene="all")` lists chat, working and coding memories
  together, newest update first, with `limit`/`offset` paging over the merged
  order and `total` summing all three. `scene="coding"` and `"working"` now
  honor `include_sources` / `exclude_sources`, and `total` counts only the
  filtered memories.
- `update()` and `delete()` now work on working/coding experiences and
  facts, not just chat memories. They take the same optional `kind` / `variant`
  as `get()`, and an ID found in more than one place raises
  `AmbiguousMemoryIdError` instead of guessing. Updating an experience replaces
  its solution and keeps its task, unless a new `task=` is also passed; updating
  a fact replaces its statement. Deleting removes the stored row and its vectors.
- `list_memories(scene="chat" | "coding" | "working")` lists one kind of
  memory. `coding` and `working` list that scene's whole store, newest update
  first (`workspace_id` still narrows it), and `total` is now the real count
  rather than the page size. The new `memory_stats()` returns the count and the
  latest created/updated time for each scene and their total, without reading
  any memory text. Previously coding and working memories could not be listed
  or counted without a workspace, so a library of 45 memories listed 22.
- Reconcile keeps time order. Both reconcile prompts now state that only
  `memory_at` decides which memory is newer, that an older memory must never
  supersede or overwrite a newer one, and that an unknown time is not newer;
  the Chinese prompt now lists `memory_at` among the new-memory fields. The
  reconciler also enforces this in code: a SUPERSEDE or UPDATE aimed at a memory
  with a later `memory_at` becomes an ADD, so the newer memory stays current.
- The HTTP server returns 409 `ambiguous_memory_id` and 400 for a bad
  `kind`/`variant` instead of 500, passes `kind`/`variant` (and `task` on PUT)
  to get/update/delete, and no longer reads a PUT/DELETE query string as part
  of the memory id.
- `memory_stats()` reads working/coding times, which are stored as naive UTC,
  as UTC rather than local time.

## Hy Memory 2.0.5 — 2026-10-22

- Published
  [`hy-memory==2.0.5`](https://pypi.org/project/hy-memory/2.0.5/).
- Extended `get()` across Chat, Coding Experience, Working Fact, and raw IDs;
  the returned `layer`, `kind`, and `variant` identify the actual record.
- Added explicit raw retrieval through the existing `search()` and
  `list_memories()` contracts, including user, agent, session, and source
  filters.
- Added public embedding-service injection and corrected extraction language
  detection for structured OpenAI-style messages.
- Added lifecycle status filters, durable idempotent write outcomes, and
  quiesced local data-plane snapshot/restore.
- Kept legacy migration mechanics out of the public Client API; separate
  migration-only raw/import methods from development previews were removed.
- Physical delete now reports partial VDB/Graph cleanup as failure.
- Coding search no longer injects the version-evolution diff block by default;
  opt in with `MEMORY_CODING_CHAIN_BLOCK_ENABLED=true`.
- Serialized SDK SQLite cache/history connection lifecycle and tightened
  Coding reconciliation to its agent/session/workspace authority.
- Clarified persistent caller context through `meta`, while `metadata` remains
  legacy processing configuration.

## Hy Memory 2.0.5.dev4 — 2026-10-21

- Development preview used for SDK-authority migration validation; not the
  final `2.0.5` release.
- Published as
  [`hy-memory==2.0.5.dev4`](https://pypi.org/project/hy-memory/2.0.5.dev4/).

## Hy Memory 2.0.5.dev3 — 2026-10-21

- Development preview; not the final `2.0.5` release.
- Published as
  [`hy-memory==2.0.5.dev3`](https://pypi.org/project/hy-memory/2.0.5.dev3/).
- Lets the existing `search` API explicitly query SDK raw with
  `layers=["l1_raw"]`, including existing source filters.

## Hy Memory 2.0.5.dev2 — 2026-10-21

- Development preview; not the final `2.0.5` release.
- Published as
  [`hy-memory==2.0.5.dev2`](https://pypi.org/project/hy-memory/2.0.5.dev2/).
- Adds exact, idempotent semantic-memory import for legacy MemHub cutover
  without extraction, reconciliation or identity changes.

## Hy Memory 2.0.5.dev1 — 2026-10-21

- Development preview for the MemHub SDK-authority migration; this is not the
  final `2.0.5` release.
- Published as
  [`hy-memory==2.0.5.dev1`](https://pypi.org/project/hy-memory/2.0.5.dev1/).
- Adds lifecycle status filtering, durable idempotent write outcomes, exact raw
  APIs, offline local data-plane snapshot/restore, and fail-closed physical
  delete receipts.

## Hy Memory 2.0.4 — 2026-10-17

- Published
  [`hy-memory==2.0.4`](https://pypi.org/project/hy-memory/2.0.4/) as a
  maintenance release.

## Hy Memory 2.0.3 — 2026-10-17

- Added an optional runtime HTTP transport factory so embedded products can
  enforce request budgets without wrapping or restricting SDK local-store
  access.
- Published
  [`hy-memory==2.0.3`](https://pypi.org/project/hy-memory/2.0.3/) with
  unchanged memory extraction, reconciliation, storage, and Coding/Working
  behavior.

## Hy Memory 2.0.2 — 2026-10-17

- Published
  [`hy-memory==2.0.2`](https://pypi.org/project/hy-memory/2.0.2/) with the
  current SDK fixes and Apache-2.0 package metadata.

## Hy Memory 2.0.1 — 2026-10-16

- Published the Python distribution as
  [`hy-memory==2.0.1`](https://pypi.org/project/hy-memory/2.0.1/).
- The repository subsequently adopted the Hy-Memory Apache-2.0 license text.
  Because PyPI release metadata is immutable, the next SDK patch release must
  carry the updated license metadata; `2.0.1` retains its published metadata.

## MemHub 1.0.5 — Unreleased

- Updated the source version to `1.0.5` (build 14).
- Pinned the public SDK to the stable `hy-memory==2.0.5` release.

## MemHub 1.0.4 — 2026-10-18

- Updated the source version to `1.0.4` (build 13).
- Published the `v1.0.4` source and release metadata.

## MemHub 1.0.3 — 2026-10-17

- Updated the source version to `1.0.3` (build 7).
- Pinned the public SDK to `hy-memory==2.0.3`.
- Removed the HyPermission store wrappers and global model-content consent
  switch; the trusted embedded SDK now uses native local stores.
- Prevented uncertain writes from blocking later source work and added one
  reconciled automatic retry after ten seconds.
- Added product-facing memory pipeline counters and the log troubleshooting
  runbook.
- Allowed a verified packaged Desktop Runtime from a different commit to take
  over an equal-build install atomically while preserving downgrade and
  tamper refusal.

## MemHub 1.0.2 — 2026-10-17

- Updated the source version to `1.0.2` (build 6).
- Pinned the public SDK to `hy-memory==2.0.2`.
- Pinned `onnxruntime==1.23.2` to retain macOS x64 packaging support.
- Fixed relaunch and upgrade failures when a prior managed Runtime exists.
- Embedded canonical build identity when an external packaging pipeline omits
  release environment fields.
- Added automatic Linux Runtime upload to tagged Draft Releases.

## MemHub 1.0.1 — Superseded before release

- The candidate tag was not published as a GitHub Release; `1.0.2` replaces it
  after the managed-Runtime relaunch fix.

## MemHub 1.0.0

- Published the initial GitHub Release with macOS arm64 and Linux Runtime
  artifacts.
