# SDK Data Plane Migration v1

Updated: 2026-10-21.

This contract migrates released MemHub installations from the historical
Evidence/Governance authority to the Hy Memory SDK data plane defined by
[`../ARCHITECTURE.md`](../ARCHITECTURE.md).

It is an offline, resumable migration executed inside the App's visible startup
loading phase, before Core accepts product traffic. It is not a permanent
dual-read or dual-write design and is not a separate manual upgrade flow for
ordinary users.

## 1. Authority before and after cutover

Before the cutover marker is committed:

- `evidence*` and `governed_*` remain the product read authority;
- SDK stores are inspected and backfilled, but are not merged into live search;
- new imports, writes, queue drains, corrections, deletes and session delivery
  are paused.
- Host capture commands detect the live Desktop migration fence and return
  silently without writing Evidence. After cutover, History catch-up runs from
  the existing source cursor before ordinary live sync.

After the cutover marker is committed:

- SDK memory/raw/vector/graph/coding stores are the only memory data authority;
- MemHub stores management state and `memory_id` / raw references only;
- legacy Evidence/Governance/retrieval/session-shadow tables are physically
  dropped before the normal App window is revealed;
- startup must not fall back to legacy authority because an SDK read failed.

The surviving `memory_authority_state` singleton, not legacy table existence,
selects the authority.

### Fresh install

A fresh home does not execute the historical application schema chain. It
bootstraps the latest management-plane schema directly with SDK authority and
never creates Evidence, Governance, legacy retrieval, any Matter, or
session-shadow tables. Historical schema migrations are an input format reader
for old homes only.

## 2. Supported source state

The migration preflight records and validates:

- source MemHub Runtime tag/build and management schema version;
- migration Runtime SDK capabilities and the actual on-disk store shape;
- SDK namespace, vector/graph/cache/history/coding providers and locations;
- embedding model identity and dimensions;
- source grants, model profiles, config, queue leases, and pending operations.

The migration fails closed when it cannot inspect every SDK memory namespace
(Chat, Coding Experience, Working Fact), write a missing ordinary memory, or
create and restore a consistent data-plane snapshot.

MemHub tags `v1.0.0` through `v1.0.4` all use the `governed-v2` namespace and
the same SDK SQL table families (`memory_operations_v2`, `pipeline_logs_v2`,
`system_metrics`, `episode_state`, `memory_history`, `coding_memory_meta`,
`working_fact`, and `coding_trace`). They therefore use one migration selected
by inspected schema/store capabilities, not one branch per historical SDK pin.
The old SDK version is informational only and is not a migration discriminator.

The migration Runtime itself must expose the required APIs. Its exact SDK
version is defined by the release lockfiles; old data is not rejected merely
because an earlier App wrote it.

## 3. Legacy-state classification

While Core and Runtime writers are stopped, build four inventories:

- `E`: completed user/assistant turns from the rolling three-day legacy window;
- `G`: current Governance rows, statuses, supersession links, and latest content;
- `M`: task-output mappings from legacy `memory_id` to SDK `hy_memory_id`;
- `S`: complete SDK inventory, including Coding/Working and non-active records.

Every item must end in exactly one class:

| Class | Meaning | Required action |
| --- | --- | --- |
| matched | Governance/mapping and SDK identity, content, owner and lifecycle agree | verify only |
| mapped mismatch | legacy ID maps to a different existing SDK ID | keep SDK ID; record alias |
| Governance-only | current legacy memory has no SDK entity | exact import without extraction |
| SDK-only | SDK entity has no legacy mirror | retain and adopt |
| divergent | both exist but content, owner or lifecycle differs | reconcile explicit user intent or quarantine |
| raw turn missing | canonical recent turn has no matching SDK raw | write one bounded L1 raw turn |
| unknown outcome | a task may have reached SDK without a confirmed result | SDK outcome inquiry; never blind retry |

An unclassified item blocks cutover.

## 4. User edits, deletion and supersession

The current product has no MemHub revision history or Undo.

- The latest non-deleted user correction is imported as the current SDK body.
- Historical `governed_memory_versions` remain in the compatibility backup, but
  are not recreated as product-visible SDK versions.
- A legacy user deletion is applied as an SDK physical delete. Deleted
  Governance tombstones are not imported and no deleted-list UI survives.
- SDK supersession remains an SDK lifecycle relationship. Existing SDK chains
  are retained. Governance links are used only to repair a missing SDK chain
  during exact import.
- Mapped Coding Experience and Working Fact IDs are resolved in their native
  SDK stores and adopted without conversion to Chat layers. A mapped ID missing
  from its native store blocks cutover rather than fabricating another type.
- Pending correction/delete records are settled before ordinary backfill.

`update(memory_id, content)` modifies the same SDK ID. There is no product CAS,
revision number, or restore operation.

## 5. Legacy Evidence to bounded SDK raw

Evidence rows are message-level legacy storage, not SDK raw chunks. Migration
uses them only to rebuild completed turns from the three days preceding the
stable migration start time:

- one human `user` opener plus its following `assistant` records becomes one
  deterministic L1 raw node;
- tool rows and agent-authored user envelopes are excluded;
- a turn is bounded to 60,000 characters and records whether it was truncated;
- existing SDK raw outside this canonical set is removed in staging;
- high-level Chat/Coding/Working memories are retained independently of raw.

No extraction or reconciliation is rerun while rebuilding raw. After cutover,
the normal Cursor History catch-up scans forward from its source checkpoint and
recovers turns skipped while capture was fenced.

## 6. Full migration bundle

A successful preflight creates one versioned bundle containing:

- an online backup of `one-memory.sqlite3`;
- an SDK-consistent export/snapshot of raw, vector, graph, cache, history and
  coding stores;
- `config.json` and non-secret configuration needed to locate those stores;
- Runtime, SDK, schema, namespace, backend and embedding fingerprints;
- object/file hashes, row counts, object counts and creation time;
- migration ledger version and bundle ID.

Secrets are not copied into a plaintext manifest. The bundle records only
secret references and the secure-store identity required for restore.

The bundle is published only after an isolated restore verifies hashes, counts
and SDK reads. A remote backend without a supported snapshot/export mechanism
blocks migration; SQLite-only backup is not reported as success.

## 7. Authority state and migration ledger

The MemHub management database owns:

- `memory_authority_state`: the surviving singleton authority and migration ID;
- `sdk_data_plane_migrations`: one run, source/target fingerprints, bundle,
  phase, cutover state and reconciliation digest;
- `sdk_data_plane_migration_items`: source identity/revision/hash, target SDK
  identity/hash, deterministic operation key, state, attempts and error code;
- `memory_id_aliases`: temporary historical ID to canonical SDK ID mapping for
  call-events, write-job receipts, and links;
- `raw_id_aliases`: legacy Evidence ID/item index to canonical SDK raw ID.
- `sdk_data_plane_cutover`: a temporary detailed cutover receipt containing
  SDK/backend/bundle/reconciliation fingerprints.

Progress is committed only after SDK verification. Retrying an item uses the
same deterministic SDK operation key and must not create another memory.
Before cleanup, every surviving reference is rewritten to its canonical SDK
ID. Alias, item-ledger, and detailed cutover tables are then dropped; the
authority singleton and verified bundle remain.

Backfill never mutates the live SDK root. Startup first copies the complete
local SDK data plane, including SDK-only records, to a same-filesystem staging
home. Exact legacy writes and reconciliation run there. Cutover renames the
live root to a protective path, renames staging into place, and only then
commits SDK authority. A failed commit restores the previous root; restart
recovery resolves any interrupted rename before opening SDK stores.

## 8. Queue and unknown outcomes

Queue rows and leases remain MemHub management state.

- A pending job has not called the SDK and therefore has no SDK raw or memory
  ID yet.
- Its worker calls SDK `add()` once. The SDK owns raw persistence plus
  extraction/reconciliation; a successful job stores only the returned raw ID,
  canonical memory IDs and terminal outcome.
- Legacy raw and semantic backfill is an App-owned privileged offline
  operation while Runtime writers are stopped. It writes SDK stores directly;
  no migration-only import/get/list method is added to public
  `HyMemoryClient`, and this is not the first half of a two-stage normal write
  path.

- `hy_invoked=0` may be safely retried after normal lease recovery.
- `memory_write_jobs.hy_invoked=1`, `state=unknown`, or any non-terminal SDK
  outcome must use `get_write_outcome(request_id)`.
- `running`/`unknown` is not absence and must not be replayed.
- confirmed SDK IDs become write-job outcomes/receipts; Governance publication is not part of
  completion after cutover.

## 9. Call events and session delivery

- Matter was an unpublished placeholder. Its rows are not migrated and every
  `matter*` table is deleted during cleanup.
- Before cleanup, historical IDs resolve through `memory_id_aliases`; surviving
  records are rewritten to canonical SDK IDs before that table is dropped.
- `call_events` remains metadata-only; reference titles/content resolve through
  SDK APIs.
- Evidence-based session inbox/outbox is disabled at cutover unless replaced by
  a management-event or SDK-event contract. It must not send shadow memory
  bodies.

## 10. Cutover

The packaged App owns orchestration. While the startup placeholder is visible,
it starts no product writer, creates or verifies the full bundle, resumes the
durable migration ledger, reconciles SDK state, and only then commits cutover.
The normal App window is revealed after this phase. A retry resumes the same
migration and deterministic operation keys; it does not create a second run.

If migration cannot safely complete, the marker remains absent and startup must
report the durable blocked/deferred reason. It must not silently continue into
an SDK/legacy dual-write mode. A missing model profile may defer migration while
legacy authority is still intact so the user can configure the App; it never
authorizes a partial cutover.

Cutover requires:

- no unclassified or quarantined item;
- complete raw coverage;
- no unsafe unknown task;
- pending corrections and deletions settled;
- call-event references resolvable;
- exact counts and hashes reconciled;
- deterministic full-ID get verification;
- a verified full migration bundle.

The final transaction writes the detailed cutover receipt and changes
`memory_authority_state` to SDK authority. Cleanup may remove the detailed
receipt after bundle verification; the authority singleton remains.

After that transaction, legacy memory/raw writes are rejected.

## 11. Rollback and version skew

Before cutover, a failed run resumes from the ledger or restores the full
bundle.

After cutover but before any SDK-only write, rollback restores the full bundle
and matching old Runtime together.

After an SDK-only write:

- automatic binary-only rollback is forbidden;
- operators either forward-fix, or explicitly restore the pre-cutover bundle
  while acknowledging loss of post-cutover writes;
- an old Runtime must fail closed on the post-cutover marker/schema.

Changing only the Runtime `current` symlink is not data rollback.

## 12. Legacy cleanup

Legacy tables are not a compatibility runtime. After every memory/raw
reference resolves, the verified bundle has passed restore validation, and the
SDK cutover marker is durable, the offline startup transaction drops:

- every `evidence*` and `governed_*` table;
- legacy retrieval/index tables;
- every `matter*` placeholder table;
- legacy deletion/index-sync/task-output and session-shadow tables;
- migration-only aliases and ledgers after their canonical references have
  been materialized into the surviving management tables.

Before that DROP, every surviving `memory_write_jobs` row is made
self-contained by freezing its source records into `payload_json`. Unstarted
legacy `memory_tasks` rows are converted to deterministic
`memory_write_jobs(idempotency_key="legacy-task:<task_id>")`; possibly-sent
task rows remain a cutover blocker until SDK request-ID reconciliation proves
their outcome.

The verified bundle is the audit and rollback artifact; keeping duplicate
plaintext tables in the live database is not. A cleanup failure keeps startup
blocked and never exposes a half-cleaned schema.

## 13. Acceptance matrix

The release gate covers:

- a clean install with no legacy rows;
- a clean install whose schema contains no legacy table names;
- Governance-only, SDK-only, mapped-ID and divergent fixtures;
- Evidence/raw-only content;
- pending correction and physical deletion;
- unknown SDK outcome and crash after SDK commit;
- interruption before/after each ledger transition;
- full bundle restore and hash/count mismatch;
- call-event alias preservation;
- old Runtime against a post-cutover home;
- successful migration followed by physical absence of every legacy table;
- local and supported remote backend snapshot behavior.

No narrow unit test may be used as evidence that the full migration is safe.
