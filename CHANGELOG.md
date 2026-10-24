# Changelog and Release Status

Hy Memory and MemHub are versioned and released independently.

## Unreleased

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
- A memory whose cited evidence row has been released no longer reads as a
  revoked source. The read policy is now spelled once instead of in four
  copies, so the memory library, the detail endpoint, call-event references
  and context assembly agree that missing provenance is a fact about the
  citation, not about the grant. Real revocations still hide memories; they
  are recorded on the grant and never on the evidence row.

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
