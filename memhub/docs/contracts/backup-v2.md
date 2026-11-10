# Backup v2: backups include the memories

Status: MemHub 1.0.7. Code: `apps/core/src/one_memory/memory_backup.py`
(format, copy, restore), `backup_routes.py` (HTTP), `cli.py` (`one-memory
restore`), CLI `memhub backup`. Tests: `test_backup_includes_memories.py`
(real stores), `test_memory_backup_format.py`.

## 1. Why

Until 1.0.6 a MemHub backup (`memhub backup create`, the API) was one SQLite
file: the management database (`one-memory.sqlite3`: sources, write queue,
model profiles, the embedding-space registry). The memories and raw turns
live in the SDK's stores under `<home>/hy-memory/governed-v2/` (Chroma,
Kuzu, the SDK's SQLite caches), or on servers a `storage_backends` setting
names, and were never in it. A restore put back the management database
and left the memories as they were.

A v2 backup is what a person needs to get their memories back.

## 2. What a backup contains

A v2 backup is a directory `<data dir>/backups/<name>.memhub-backup/`
(0700; every file 0600). `backups/x` and the v1 habit `backups/x.sqlite3`
both name `backups/x.memhub-backup`. The target must be inside the data
directory and must not exist.

```
manifest.json
management/one-memory.sqlite3      SQLite backup API
sdk/governed-v2/...                every SDK file of this home
export/memories.jsonl              only when vectors are on a Qdrant server
```

| Backend (`storage_backends`) | `backend.kind` | What holds the memories in the backup |
|---|---|---|
| Kuzu + Chroma (default) | `local` | `sdk/governed-v2`: the Chroma store (every embedding space it holds: the live one and any kept for the 14-day retention), the live graph and the graphs of retained spaces (`graph`, `graph-<id>`), the SDK's SQLite files (cache with write outcomes, history, coding). |
| Neo4j/Memgraph graph + Chroma | `remote_graph` | The same local files. The graph on the server is derived data and is not exported: a restore rebuilds it (section 5). |
| Qdrant vectors (any graph) | `remote_vector` | The local files (SDK SQLite: raw caches, coding, history, cache; any Chroma/Kuzu files from before the switch), plus `export/memories.jsonl`: every memory on the server, through the SDK. |

Never copied: `logs/`, quarantined graphs (`graph.quarantined-*`, #413),
archived mismatched graphs (`graph.dim*.bak`), names starting with `.`
(staging and restore directories), `*.tmp`, `*.lock`, `*.partial`, `*.pid`,
and SQLite `-wal`/`-shm`/`-journal` sidecars (the backup API folds them into
the copy). A symlink anywhere in the SDK tree refuses the backup
(`backup_sdk_root_symlink`).

### The export (Qdrant)

One JSON line per memory: `{"user_id", "layer" | "scene", "memory"}`, where
`memory` is the SDK's own `list_memories` item. It is read through the SDK's
public API, paged per user and per layer (`l0_basic_info`, `l1_raw`,
`l2_fact`, `l3_summary`, `l4_identity`), every status (`active`,
`superseded`, `negated`, `conflicted`, `shadow`, `archived`), raw turns
included; Coding/Working experiences per scene. It is streamed to disk.
hy-memory 2.0.15 has no public read that returns a memory's vector, so
`export.embeddings_included` is `false`; the manifest records the space's
model and width so a future restore can re-embed. Users are the ones Core
knows (`local`, every user with a grant or a queued write): the SDK has no
"list users".

## 3. Consistency

On the running Core, `POST /api/v1/backup`:

1. pauses SDK writes with the embedding switch's gate (`writes_open` is
   false: the write drain puts jobs back, direct edits answer 409
   `backup_in_progress`), takes the lifecycle lock (evolve, mode change, graph
   backfill between users) and waits for the drain to put down its running
   job (120 s each at most: `backup_lifecycle_busy`, `backup_writes_busy`);
   a backup during an embedding switch is refused
   (`backup_embedding_maintenance_in_progress`);
2. counts what it is about to copy (section 4) and, on Qdrant, writes the
   export;
3. closes every SDK client and holds the stores offline (`hold_stores_offline`,
   as the switch's graph re-embed does). Closing the SDK client is Kuzu's
   CHECKPOINT (hy-memory 2.0.15 checkpoints on close); Chroma's files are
   copied while no client exists. SQLite files go through SQLite's backup API
   and are checked with `quick_check`; the rest is streamed with a SHA-256;
4. releases the stores and resumes writes on every exit path, failure
   included.

`pause_ms` (whole pause) and `copy_ms` (stores offline) are in the answer and
the manifest. Reads wait only while the stores are offline. On a synthetic
home of ~1,000 memories over two users (600 raw turns, 400 facts, ultra
graph, 23 MB), the pause was 1.25 s, of which 0.75 s closing the SDK clients
and 0.07 s copying; the restore took 0.5 s.

Hashing and the graph count (a read-only child on the copy) run after
writes resume. The backup is published by one rename of its staging
directory; a failed backup leaves no directory behind.

## 4. Manifest (schema 2)

```json
{
  "format": "memhub-backup",
  "schema_version": 2,
  "created_at": "...", "runtime_build": "1.0.7",
  "sdk_version": "2.0.15", "sdk_namespace": "governed-v2",
  "memories_included": true,
  "backend": {"kind": "local", "vector": "chroma", "graph": "kuzu", "vector_prefix": null},
  "memory_mode": "ultra",
  "embedding_space": {"space_id": "...", "model": "...", "dims": 512, "name_dims": 512,
                      "collection_base": "memhub_...", "graph_dir": "graph", "state": "live"},
  "embedding_spaces": [...],
  "users": ["local", "alice"],
  "counts": {
    "layers": {"local": {"l0_basic_info": 0, "l1_raw": 23, "l2_fact": 4, "l3_summary": 0,
                         "l4_identity": 0, "scene:coding": 0, "scene:working": 0}},
    "vector_store": {"memhub_...|512": {"memory": {"local": 27, "alice": 6}}},
    "graph": {"local": {"l2_fact": 2}},
    "export": null
  },
  "contents": {"management": "management/one-memory.sqlite3", "sdk_root": "sdk/governed-v2",
               "export": null},
  "export": null,
  "pause_ms": 84, "copy_ms": 15,
  "files": [{"path": "...", "bytes": 0, "sha256": "..."}],
  "file_count": 9, "total_bytes": 9132196
}
```

- `counts.layers`: per user, the SDK's count of each vector layer over every
  status (`list_memories` totals) and of each experience scene
  (`memory_stats`). `null` when no SDK client could be built (the files are
  still copied).
- `counts.vector_store`: every Chroma record per space, role and `user_id`,
  from the SDK's own space scan (the one the startup stray-space scan uses).
  `null` on Qdrant.
- `counts.graph`: Memory nodes per user and layer in the copied Kuzu graph,
  read by the graph-open probe child in read-only mode. `null` if the copy
  could not be read.
- `counts.export`: lines written per user and layer/scene (Qdrant).

v1 was the bare database file and had no manifest. A manifest with
`schema_version` above 2 is refused (`backup_restore_source_too_new`).

## 5. Restore

Only a non-runtime Core restores (`backup_restore_runtime_running`
otherwise): the CLI stops nothing itself, refuses while the Runtime runs, and
drives a maintenance Core (`one-memory serve --maintenance`) to
`POST /api/v1/backup/restore`; `one-memory restore --source <dir> --yes` does
the same offline. The CLI keeps the "type the path twice" rule:
`--yes --confirm <same path>`.

A v2 restore:

1. finds the source in `backups/` (no symlink), checks the manifest and the
   size and SHA-256 of every file (`backup_restore_verify_failed`), the
   management database's identity and schema
   (`backup_restore_source_invalid`, `..._too_new`), the backend
   (section 6) and free space (`backup_restore_disk_space_insufficient`);
2. copies `sdk/governed-v2` next to the live tree and verifies the copy
   again. Nothing live has changed so far;
3. pauses writes, closes every SDK client, holds the stores offline and
   drops Chroma's process cache; writes a journal (`.memhub-restore.json`),
   copies the live management database to
   `backups/pre-restore-<UTC>.memhub-previous/management/`, renames the live
   `hy-memory/governed-v2` into that directory, renames the staged tree in
   and replaces the management database atomically (`restore_from`);
4. runs the startup checks on what it restored: the embedding-space registry
   of the restored database names the live space and its graph; the space
   scan counts every Chroma record per space, role and user; the #413 graph
   probe opens the live Kuzu graph in a bounded child: a graph that cannot
   be opened is quarantined (`graph.quarantined-<UTC>`, never deleted) and
   a rebuild of every user's graph is scheduled (`evolve-state.json`
   `graph_build`, picked up by `MemoryGraphBackfill` at the next start); a
   graph that opens is counted. Then a fresh SDK client built from the
   restored database counts every layer per user;
5. compares every count with the manifest. Any difference, or any error in
   steps 3-4, renames everything back and restores the previous management
   database: `backup_restore_counts_mismatch` (or
   `backup_restore_replace_failed`). A process killed in step 3-4 leaves the
   journal, and the next start of Core (`serve`) rolls back before anything
   reads the home;
6. on success removes the journal. The previous data stays in
   `backups/pre-restore-<UTC>.memhub-previous/` (`previous_backup_path`) until
   the person deletes it.

The answer:

```json
{"state": "restored", "backup_schema_version": 2, "memories_included": true,
 "restored_bytes": 9132196, "restored_files": 9,
 "previous_backup_path": ".../backups/pre-restore-...memhub-previous",
 "source_schema_version": 63,
 "backend": {...},
 "verification": {"vector_store": "verified", "layers": "verified", "layers_reason": null,
                  "graph": "verified|quarantined|absent|rebuild_scheduled|skipped",
                  "mismatches": []},
 "graph_rebuild_scheduled": false, "duration_ms": 262, "note": null}
```

`layers` is `skipped` with `layers_reason` `runtime_stopped` from the offline
`one-memory restore` (it builds no model client) and `sdk_client_unavailable`
when no client could be built; the store and graph counts are still checked.

The stray-space scan and merge, the retention sweep and the graph probe also
run at the next start, as on every start.

### Old backups

A v1 backup (one `.sqlite3` file in `backups/`) restores exactly as before:
the management database only, with a protective copy
`backups/pre-restore-<UTC>.sqlite3`. The answer says so:
`backup_schema_version: 1`, `memories_included: false`,
`note: "management_database_only_no_memories"`. The memories stay as they
are.

## 6. Across backends

A backup restores only onto a home whose current backend is the same kind
and the same providers (`local` onto `local`, Qdrant onto Qdrant, Neo4j onto
Neo4j); anything else is `backup_restore_backend_mismatch`, and nothing
changes.

- **Local backup onto a server backend (or the reverse): refused, not
  converted.** Converting means writing each memory into the other store
  with its id and its vector; hy-memory 2.0.15 has no public API that does
  that (`add_extracted` re-reconciles and makes new ids, `clone_user` only
  copies within one store), and MemHub never writes the SDK's stores itself
  (ARCHITECTURE: memory I/O only through the SDK). A local backup keeps its
  local files: `memhub settings backends reset` returns the home to Kuzu +
  Chroma, and the backup then restores.
- **Qdrant backup onto Qdrant: refused** (`backup_restore_remote_sdk_unsupported`)
  until MemHub pins an SDK with an import API that keeps ids and vectors. The
  backup itself works and holds the export; Qdrant's own snapshots back up the
  server. SDK follow-up: Tencent-Hunyuan/Hy-Memory#424 adds
  `HyMemoryClient.export_user_memories` / `import_user_memories` (ids,
  statuses, evolution pointers, times and vectors; re-embeds a node without
  its vector). Once a release with it is pinned, the export switches to it
  (vectors included) and the restore imports instead of refusing. Still to
  cover in the SDK before that: the Coding/Working experience vectors and
  Qdrant's retrieval-clause side index.
- **Neo4j/Memgraph graph with Chroma vectors**: restores the local files like
  a local backup; the server's graph is not replaced. A rebuild of every
  user's fact projection is scheduled with `clear_first` (the SDK's
  `build_memory_graph`), so no node of a memory the restore removed stays.
  Evolve outputs that live only in the graph (L6 schema, L7 intention) are
  regenerated by the nightly evolve, not restored.

`capabilities.restore` in `GET /api/v1/settings/storage-backends` reports
`refused` with this code on Qdrant.

## 7. Size

`GET /api/v1/backup/estimate[?target=<path>]` answers `bytes` (local files
plus, on Qdrant, ~1.5 KB per exported memory), `files`, `local_bytes`,
`export_bytes`, `free_bytes`, `backend` and, with `target`, the directory a
backup would be written to (the same refusals as the backup). A backup with
less free space than 105% of the estimate is refused
(`backup_disk_space_insufficient`, HTTP 507).

`memhub backup create --target <path>` shows this size and the target and
writes nothing; `--yes` writes the backup. There is no backup control in the
App (Web or desktop) in this release: the App shows no backup button.

## 8. Backup and export

MemHub has no `memhub export` command. The only exports are `memhub logs
export` (a diagnostics bundle without any memory) and `graph-width --decision
export_first` (a copy of the Kuzu graph file before a width rebuild). A
backup is the way to keep and move memories: it is complete, verified and
restorable. The Qdrant export inside a backup is a portable JSONL of the
memories, kept for a restore once the SDK can write it back and readable by
other tools now.

## 9. Codes

Backup: `backup_target_outside_data_dir` (422), `backup_target_exists` (409),
`backup_target_invalid`, `backup_in_progress`,
`backup_embedding_maintenance_in_progress`, `backup_lifecycle_busy`,
`backup_writes_busy`, `backup_clients_busy` (409),
`backup_disk_space_insufficient` (507), `backup_sdk_unavailable` (503, Qdrant
export without an SDK client), `backup_database_missing`,
`backup_sdk_root_symlink`.

Restore (422 unless noted): `backup_restore_runtime_running` (409),
`backup_restore_source_outside_home`, `backup_restore_source_invalid`,
`backup_restore_source_too_new`, `backup_restore_manifest_invalid`,
`backup_restore_verify_failed`, `backup_restore_backend_mismatch`,
`backup_restore_remote_sdk_unsupported`, `backup_restore_counts_mismatch`,
`backup_restore_disk_space_insufficient`, `backup_restore_stage_failed`,
`backup_restore_pending`, `backup_restore_replace_failed`,
`backup_restore_rollback_failed`, `backup_restore_protective_backup_failed`
(v1).
