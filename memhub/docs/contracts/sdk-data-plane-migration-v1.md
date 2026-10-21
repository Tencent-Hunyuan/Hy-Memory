# SDK Data Plane Migration v1

Updated: 2026-10-21.

This contract migrates released MemHub installations from the historical
Evidence/Governance authority to the Hy Memory SDK data plane defined by
[`../ARCHITECTURE.md`](../ARCHITECTURE.md).

It is an offline, resumable migration. It is not a permanent dual-read or
dual-write design.

## 1. Authority before and after cutover

Before the cutover marker is committed:

- `evidence*` and `governed_*` remain the product read authority;
- SDK stores are inspected and backfilled, but are not merged into live search;
- new imports, writes, queue drains, corrections, deletes, Matters generation,
  and session delivery are paused.

After the cutover marker is committed:

- SDK memory/raw/vector/graph/coding stores are the only memory data authority;
- MemHub stores management state and `memory_id` / raw references only;
- legacy Evidence/Governance tables are read-only compatibility data;
- startup must not fall back to legacy authority because an SDK read failed.

The marker, not table existence, selects the authority.

## 2. Supported source state

The migration preflight records and validates:

- source MemHub Runtime tag/build and management schema version;
- migration Runtime SDK capabilities and the actual on-disk store shape;
- SDK namespace, vector/graph/cache/history/coding providers and locations;
- embedding model identity and dimensions;
- source grants, model profiles, config, queue leases, and pending operations.

The migration fails closed when the installed SDK lacks:

- complete lifecycle/raw inventory;
- exact raw and exact memory import;
- durable request outcome lookup;
- consistent snapshot/export and restore.

MemHub tags `v1.0.0` through `v1.0.4` all use the `governed-v2` namespace and
the same SDK SQL table families (`memory_operations_v2`, `pipeline_logs_v2`,
`system_metrics`, `episode_state`, `memory_history`, `coding_memory_meta`,
`working_fact`, and `coding_trace`). They therefore use one migration selected
by inspected schema/store capabilities, not one branch per historical SDK pin.
The old SDK version is informational only and is not a migration discriminator.

The migration Runtime itself must expose the required APIs (currently the
`2.0.5.dev2` development preview); old data is not rejected merely because an
earlier App wrote it.

## 3. Legacy-state classification

While Core and Runtime writers are stopped, build four inventories:

- `E`: Evidence rows and revisions;
- `G`: current Governance rows, statuses, supersession links, and latest content;
- `M`: task-output mappings from legacy `memory_id` to SDK `hy_memory_id`;
- `S`: complete SDK inventory, including raw and non-active lifecycle records.

Every item must end in exactly one class:

| Class | Meaning | Required action |
| --- | --- | --- |
| matched | Governance/mapping and SDK identity, content, owner and lifecycle agree | verify only |
| mapped mismatch | legacy ID maps to a different existing SDK ID | keep SDK ID; record alias |
| Governance-only | current legacy memory has no SDK entity | exact import without extraction |
| SDK-only | SDK entity has no legacy mirror | retain and adopt |
| divergent | both exist but content, owner or lifecycle differs | reconcile explicit user intent or quarantine |
| raw-only | Evidence has no SDK raw receipt | exact raw import |
| unknown outcome | a task may have reached SDK without a confirmed result | SDK outcome inquiry; never blind retry |

An unclassified item blocks cutover.

## 4. User edits, deletion and supersession

The target product has no MemHub revision history or Undo.

- The latest non-deleted user correction is imported as the current SDK body.
- Historical `governed_memory_versions` remain in the compatibility backup, but
  are not recreated as product-visible SDK versions.
- A legacy user deletion is applied as an SDK physical delete. Deleted
  Governance tombstones are not imported and no deleted-list UI survives.
- SDK supersession remains an SDK lifecycle relationship. Existing SDK chains
  are retained. Governance links are used only to repair a missing SDK chain
  during exact import.
- Pending correction/delete records are settled before ordinary backfill.

`update(memory_id, content)` modifies the same SDK ID. There is no product CAS,
revision number, or restore operation.

## 5. Evidence to SDK raw

Evidence must not be replayed through ordinary `add()`: doing so would extract
and reconcile again, changing semantic memories.

The SDK exact-raw import preserves:

- source, conversation/session, workspace, role and author;
- original event time and stable source ordering;
- verbatim content and source identity;
- a deterministic migration key.

The operation is idempotent and returns an SDK raw ID. Evidence revisions and
repairs are preserved in the migration bundle for audit; only the current
accepted raw record becomes the normal product read.

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

## 7. Durable migration ledger

The MemHub management database owns operational migration tables:

- `sdk_data_plane_migrations`: one run, source/target fingerprints, bundle,
  phase, cutover state and reconciliation digest;
- `sdk_data_plane_migration_items`: source identity/revision/hash, target SDK
  identity/hash, deterministic operation key, state, attempts and error code;
- `memory_id_aliases`: historical ID to canonical SDK ID for old Matters,
  call-events, task receipts and links.
- `sdk_data_plane_cutover`: the singleton authority marker and immutable
  SDK/backend/bundle/reconciliation fingerprints.

Progress is committed only after SDK verification. Retrying an item uses the
same deterministic SDK operation key and must not create another memory.

## 8. Queue and unknown outcomes

Queue rows and leases remain MemHub management state.

- `hy_invoked=0` may be safely retried after normal lease recovery.
- `hy_invoked=1`, `memory_tasks.status=unknown`, or any non-terminal SDK outcome
  must use `get_write_outcome(request_id)`.
- `running`/`unknown` is not absence and must not be replayed.
- confirmed SDK IDs become task receipts; Governance publication is not part of
  completion after cutover.

## 9. Matters, call events and session delivery

- Matters retain organization and canonical `memory_id` / SDK raw references.
- Embedded memory bodies are replaced by read-time SDK resolution.
- Historical IDs remain resolvable through `memory_id_aliases`.
- `call_events` remains metadata-only; reference titles/content resolve through
  SDK APIs.
- Evidence-based session inbox/outbox is disabled at cutover unless replaced by
  a management-event or SDK-event contract. It must not send shadow memory
  bodies.

## 10. Cutover

Cutover requires:

- no unclassified or quarantined item;
- complete raw coverage;
- no unsafe unknown task;
- pending corrections and deletions settled;
- Matters/call-event references resolvable;
- exact counts and hashes reconciled;
- deterministic full-ID get verification;
- a verified full migration bundle.

The final transaction writes an immutable marker with migration ID, SDK/store
version, namespace/backend fingerprint, bundle ID, digest and completion time.

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

## 12. Legacy retention and cleanup

For at least one compatibility release:

- keep legacy tables read-only;
- keep the verified migration bundle;
- expose diagnostics for migration ID, phase, counts and last stable error code;
- do not silently delete old Evidence/Governance content.

Cleanup is a separate explicit operation after retention expires. It requires a
verified bundle and no unresolved alias/reference.

## 13. Acceptance matrix

The release gate covers:

- a clean install with no legacy rows;
- Governance-only, SDK-only, mapped-ID and divergent fixtures;
- Evidence/raw-only content;
- pending correction and physical deletion;
- unknown SDK outcome and crash after SDK commit;
- interruption before/after each ledger transition;
- full bundle restore and hash/count mismatch;
- Matters/call-event alias preservation;
- old Runtime against a post-cutover home;
- local and supported remote backend snapshot behavior.

No narrow unit test may be used as evidence that the full migration is safe.
