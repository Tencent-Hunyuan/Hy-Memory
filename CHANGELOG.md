# Changelog and Release Status

Hy Memory and MemHub are versioned and released independently.

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

## Hy Memory 2.0.3 — 2026-10-17

- Added an optional runtime HTTP transport factory so embedded products can
  enforce request budgets without wrapping or restricting SDK local-store
  access.
- Published
  [`hy-memory==2.0.3`](https://pypi.org/project/hy-memory/2.0.3/) with
  unchanged memory extraction, reconciliation, storage, and Coding/Working
  behavior.

## Unreleased

- Added a MemHub-managed native Hermes MemoryProvider with automatic prefetch,
  persistent per-session memory-ID deduplication, authorized Broker reads, and
  recall-context stripping to prevent memory recursion.
- Added an extensible MemHub interface-localization layer with Simplified
  Chinese, English, and Spanish catalogs shared by Web and Electron-native
  surfaces. Interface language remains independent from memory content and
  model processing language.
- `search(..., layers=["l1_raw"])` now explicitly searches SDK raw records in
  pro/ultra mode and keeps existing user/agent/session/source filters; default
  search continues to hide raw.
- Added `import_memory_exact` for idempotent migration of an existing semantic
  memory with its canonical ID, lifecycle and supersession links, without
  extraction or reconciliation.
- Added SDK-authoritative lifecycle listing with explicit status filters,
  idempotent durable write outcomes, exact raw import/get/list APIs, and
  quiesced local data-plane snapshot/restore contracts for the MemHub
  authority migration.
- Physical delete now reports incomplete VDB/Graph cleanup as a partial failure
  instead of claiming success after only one store was changed.
- Coding search no longer injects the version-evolution diff block
  (`chain_block`) by default. Enable with
  `MEMORY_CODING_CHAIN_BLOCK_ENABLED=true` or
  `CodingConfig.chain_block_enabled=True` (write-side observe unchanged).
- Fixed governed-memory revisions and repeated evidence links failing when the
  visibility dirty-marker trigger encounters the same memory twice. Startup
  now replaces the affected legacy trigger without changing schema versions
  or governed and visibility data.
- Serialized each SDK SQLite store's shared connection lifecycle so concurrent
  history/cache operations cannot race with `close()` and crash the process.
- Scoped Coding reconciliation targets to the current HY authority,
  conversation, and workspace, preventing near-duplicate sessions from
  mutating an experience or working fact owned by another evidence context.
- Kept post-write authority verification failures available for read-only
  reconciliation while preventing automatic or generic confirmed retries from
  invoking the model again. Automatic reconciliation skips those sticky rows
  before applying its batch limit, and linked durable jobs exclusively own
  retry scheduling for their memory tasks.
- Clarified that `add(metadata=...)` carries legacy SDK processing options,
  while persistent caller business context belongs in `meta`.
- Added an opt-in `get(..., include_raw=True)` path for reading `L1_RAW`
  memories, including memories written in lite mode. The default remains
  hidden for backward compatibility.
- Removed MemHub's task-scoped HyPermission store wrappers and global
  model-content consent switch. The trusted embedded Hy Memory SDK now uses
  its native local stores directly.
- Existing installations ignore the retired config flag, readmit previously
  parked pre-invocation work, and drop only the obsolete
  `hy_node_permissions` proof table. Evidence, governed memories, model
  profiles, SDK stores, and source/host grants are preserved.

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

## MemHub 1.0.3 — Unreleased

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
