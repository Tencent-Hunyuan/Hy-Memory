# Storage backends v1

Status: Core + CLI implemented. The App shows the active backends read-only
and offers no control (owner decision, MemHub 1.0.7).

MemHub keeps its memories in the stores of the Hy Memory SDK. By default these
are the two embedded stores every MemHub has always used: **Kuzu** for the
ultra knowledge graph and **Chroma** for vectors, both files under
`~/.one-memory/hy-memory/governed-v2/`. A person can choose other SDK stores
through the CLI or the HTTP API:

| Side | Providers | Default | Notes |
|---|---|---|---|
| graph | `kuzu`, `neo4j`, `memgraph` | `kuzu` | Opened only in ultra. Neo4j and Memgraph are servers (bolt URL). |
| vector | `chroma`, `qdrant` | `chroma` | Qdrant must be a server (`host`). |

The SDK also has a FAISS vector store. MemHub refuses it
(`storage_backend_faiss_unsupported`): the SDK's FAISS store keeps its index
in each client's memory and rewrites the whole file on every write, and
MemHub always runs several clients on one home (reads beside writes, Coding
and Working stores beside chat). They would overwrite each other's writes.
For the same reason MemHub refuses Qdrant's embedded local mode
(`storage_backend_vector_host_required`): it locks its directory to one client.

Raw turns, Coding/Working metadata, the SDK cache and its history stay in this
home's SQLite files whatever the backends.

## 1. The setting

`config.json` `storage_backends`, written by Core only, and only once someone
set it (an untouched install's file keeps the shape older builds read):

```json
{
  "storage_backends": {
    "graph": {"provider": "neo4j", "url": "bolt://graph.internal:7687", "username": "neo4j", "database": "memories"},
    "vector": {"provider": "qdrant", "host": "vectors.internal", "port": 6333},
    "namespace": "mh1a2b3c4d"
  }
}
```

- **Secrets** never reach `config.json`, a log or a reply. The graph password
  is stored in the secret store as `storage-backend:graph-password`, the Qdrant
  API key as `storage-backend:vector-api-key`. A reply says only
  `has_password` / `has_api_key`.
- **`namespace`** is this home's prefix on a shared vector server: `mh` and 8
  random hex characters, chosen on the first `set` and kept for good, through
  `reset` too. Every Qdrant collection this home opens is
  `<collection_prefix or namespace>_<space collection base>`, so two homes on
  one server never read or drop each other's collections, and setting the
  same server again finds the same collections. `collection_prefix` overrides
  it (`^[a-z0-9][a-z0-9_-]{0,31}$`).
- **The graph has no namespace.** The SDK's Neo4j/Memgraph store uses fixed
  labels and one vector index per database. A database must hold one home's
  graph only. A `set` whose probe finds `Memory` nodes already in a database
  that is not the one in use is refused with `storage_backend_graph_not_empty`
  unless the request says `adopt_existing_graph: true` (`--adopt-existing-graph`).
- **The setting is the only source.** `build_sdk_config` sets every backend
  field itself, so the SDK's environment defaults (`MEMORY_GRAPH_PROVIDER`,
  `MEMORY_VECTOR_STORE`, `MEMORY_VECTOR_HOST`, ...) have no effect: a stray
  variable never sends memories to a server nobody chose
  (`test_hy_network_egress`). There is no environment override.

## 2. HTTP API

All routes need the Runtime bearer token; unknown query parameters and body
fields answer 422. The setting is global (no `user_id`).

### `GET /api/v1/settings/storage-backends`

```json
{
  "schema_version": 1,
  "graph": {"provider": "neo4j", "url": "bolt://graph.internal:7687", "username": "neo4j",
            "database": "memories", "has_password": true, "driver_available": true},
  "vector": {"provider": "qdrant", "host": "vectors.internal", "port": 6333,
             "collection_prefix": "mh1a2b3c4d", "has_api_key": true, "driver_available": true},
  "is_default": false,
  "label": "Neo4j + Qdrant",
  "namespace": "mh1a2b3c4d",
  "capabilities": {"embedding_switch": {"state": "refused", "code": "embedding_switch_backend_unsupported"}, "...": {}},
  "health": {"ok": true, "graph": {"...": "BackendProbe"}, "vector": {}},
  "providers": {"graph": [{"provider": "kuzu", "supported": true, "driver_available": true, "code": null}],
                "vector": [{"provider": "faiss", "supported": false, "driver_available": false,
                            "code": "storage_backend_faiss_unsupported"}]}
}
```

`health` is the last probe of the configured backends (null until one ran);
see section 4.

### `PUT /api/v1/settings/storage-backends`

```json
{
  "graph": {"provider": "neo4j", "url": "bolt://graph.internal:7687", "username": "neo4j", "database": "memories", "password": "…"},
  "vector": {"provider": "qdrant", "host": "vectors.internal", "port": 6333, "api_key": "…"},
  "adopt_existing_graph": false
}
```

- An absent side keeps what is configured. Inside a side, an absent
  `password` / `api_key` keeps the stored secret, `null` removes it. A stored
  secret is kept only for the same server (graph: same URL and user; vector:
  same host); pointing a side at another server without a new secret removes
  the old one, so a credential is never sent somewhere it was not given for.
- Core **probes first** (section 4) and saves nothing the probe could not
  reach: `422 {"detail": "<probe code>", "probe": {...}}`.
- Refused while an embedding switch or the startup scan runs
  (`409 embedding_maintenance_in_progress`) or while evolve or the graph
  backfill runs (`409 memory_lifecycle_busy`), as a mode change is.
- On success: `200` with the new state plus `changed`, `previous_label`,
  `probe` and `notice: "existing_memories_not_moved"` whenever a backend
  changed. The SDK clients are rebuilt through their handle keys, as for a
  mode or feature change: a write in flight finishes on the store it started
  on, the next job and the next read use the new one. An ultra home whose
  graph moved starts the graph backfill, which rebuilds the derived graph on
  the new server from the memories the new vector store holds.

### `POST /api/v1/settings/storage-backends/test`

Same body as `PUT` (or none: the configured setting). Runs the probe, saves
nothing: `{"schema_version": 1, "label", "probe", "graph_adoption_required"}`.

### `DELETE /api/v1/settings/storage-backends`

Back to Kuzu + Chroma. Both secrets are deleted; `namespace` stays. Same
reply as `PUT`, same refusals.

### Validation codes (422 `detail`)

`storage_backend_graph_kuzu_takes_no_connection`,
`storage_backend_graph_url_required`, `storage_backend_graph_url_invalid`
(not bolt/neo4j, carries credentials, a path or a bad port),
`storage_backend_graph_username_invalid`, `storage_backend_graph_database_invalid`,
`storage_backend_vector_chroma_takes_no_connection`,
`storage_backend_faiss_unsupported`, `storage_backend_vector_host_required`,
`storage_backend_vector_host_invalid`, `storage_backend_vector_port_invalid`,
`storage_backend_vector_prefix_invalid`, `storage_backend_invalid` (anything else).

## 3. Nothing is moved

Switching backends moves no memory. What is in the store that was left stays
there, and the new store starts as it is:

- The old Kuzu graph and Chroma files are never deleted, renamed, rewritten
  or even opened by MemHub's maintenance while another store is configured
  (startup scan, merge, retention sweep, metadata repair, space cleanup and
  graph export are all skipped or refused; see section 5).
- Switching back shows those memories again.
- There is no copy tool between backends. MemHub's upgrade migrations do not
  run on a home with a custom backend.

The CLI says so before it asks for confirmation (`docs/cli/EXPERIENCE.md`).

## 4. Probe and health

The probe connects and reads only; it creates no collection and writes no node.

- Neo4j/Memgraph: `verify_connectivity`, then `MATCH (m:Memory) RETURN count(m)`
  (`existing_memory_nodes`; null when the role may not read).
- Qdrant: `get_collections`, counting the ones that carry this home's prefix
  (`collections_in_namespace`).
- Kuzu and Chroma are local files: `state: "local"`, never probed.

Codes: `storage_backend_driver_missing`, `storage_backend_unreachable`,
`storage_backend_auth_failed`, `storage_backend_database_not_found`,
`storage_backend_probe_failed`. Only the error class is reported, never its
message (it can carry a URL or a user name).

`pipeline.storage_backends` in `GET /api/v1/memory-processing` (and so in
`memhub status --json`) is the status line:

```json
{"graph": "neo4j", "vector": "qdrant", "is_default": false, "label": "Neo4j + Qdrant",
 "health": "ok", "health_code": null}
```

`health` is `local` (embedded files), `ok` (the servers answered the last
probe), `failed` (`health_code` says why) or `unknown` (not probed yet). The
probe is refreshed in the background at most once a minute; a status request
never waits on the network.

## 5. What works on each backend

`capabilities` in the `GET` reply reports this for the configured setting and
mode. `works` / `skipped` (nothing to do there, nothing touched) / `refused`
(a stable code, nothing done).

| Feature | Kuzu + Chroma | Neo4j/Memgraph graph | Qdrant vectors |
|---|---|---|---|
| Write, read, search, recall, lite raw RAG, delete, library counts, multi-user isolation | works | works (SDK) | works (SDK) |
| Embedding switch (plan, pause, re-embed, verify, commit) | works | pro/lite: works; **ultra: refused** `embedding_switch_backend_unsupported` | **refused** `embedding_switch_backend_unsupported` |
| Embedding-space registry and collection naming | works | works | works; names carry the home prefix |
| Startup stray-space scan and merge, retention sweep, space cleanup | works | works | **skipped** `embedding_scan_skipped_custom_backend`; cleanup refused |
| Graph open probe and quarantine (#413) | works | **skipped** `graph_open_guard_not_kuzu` | works |
| Graph width check | works | **skipped** (no mismatch reported) | works |
| `graph-width` export / rebuild | works | **refused** `graph_export_backend_unsupported` | works |
| Graph backfill and evolve | works | works (SDK; backfill starts when the graph moves) | works |
| Memory map (graph view) | works | works (same Cypher) | works |
| Backup create / restore | works | works | works |
| Upgrade migrations (`sdk-migrate`, `sdk-migration-status`) | work | **skipped** `upgrade_migration_skipped_custom_backend` | **skipped** |

Notes:

- **Embedding switch.** The SDK's re-embed (hy-memory 2.0.15) copies Chroma
  collections and Kuzu graphs only. A Neo4j/Memgraph database keeps one vector
  index of one width and cannot hold two spaces side by side. The refusal is
  reported as a plan blocker before anything pauses. A home on Qdrant is never
  reported as an "empty home" whose space can move without re-embedding. To
  change the embedding model, `memhub settings backends reset` (memories stay
  on Qdrant), switch the model on Chroma, then set Qdrant again: the new
  space's Qdrant collections start empty. Follow-up: SDK re-embed for Qdrant
  (Tencent-Hunyuan/Hy-Memory#422, not yet released). Once MemHub pins a release
  containing it, `SdkReembedBackend` can pass the Qdrant location and this
  home's prefix, and the refusal on Qdrant can be lifted. The refusal for a
  Neo4j/Memgraph graph in ultra stays.
- **Backup and restore** cover MemHub's management database
  (`one-memory.sqlite3`: sources, write queue, profiles, the embedding-space
  registry). They never contained the memories themselves on any backend:
  the SDK stores are not in a MemHub backup, and a restore never replaces
  them. On a server backend, back the memories up with the server's own tools
  (Neo4j `neo4j-admin database dump`, Qdrant snapshots). `config.json` and
  the secrets are not in the backup either, so a restore never changes the
  backends.
- **Restore of an SDK-era backup.** Since this version a managed restore
  accepts a backup taken after the SDK cutover (identity: the
  `memory_authority_state` row and the write queue); it used to require the
  legacy `evidence` table, which such a home no longer has.
- **Upgrade migrations** repair the local Kuzu/Chroma stores and the legacy
  tables that fed them. A custom-backend home does not use those stores, so
  `sdk-migrate` returns `not_required` with `skipped` and touches nothing. A
  home that names a custom backend but has not reached SDK authority (only
  possible by editing `config.json`) is refused with
  `sdk_migration_custom_backend_unsupported`.

## 6. CLI

```text
memhub settings backends show [--json]
memhub settings backends set [--graph <kuzu|neo4j|memgraph>] [--graph-url <bolt-url>]
        [--graph-user <name>] [--graph-database <name>] [--graph-password-file <path>]
        [--vector <chroma|qdrant>] [--vector-host <host>] [--vector-port <port>]
        [--vector-prefix <prefix>] [--vector-api-key-file <path>]
        [--adopt-existing-graph] [--yes] [--json]
memhub settings backends test [same options as set] [--json]
memhub settings backends reset [--yes] [--json]
```

Secrets are read from files (`--graph-password-file`, `--vector-api-key-file`;
`-` reads stdin), never from the command line. `set` and `reset` probe, show
what changes and that existing memories are not moved, and ask for
confirmation (`--yes` skips it; without a terminal `--yes` is required).

## 7. App

The App offers no control. Settings and status show one read-only line,
`存储后端：Neo4j + Qdrant（通过 CLI 配置）`, in every interface language, and only
when a custom backend is active. Every other screen keeps working on the
configured backend; screens that read the graph or the embedding switch show
the codes above.

## 8. Packaging

The frozen Core collects `neo4j`, `qdrant_client` and `portalocker`
(qdrant-client's lock dependency) beside `kuzu` and `chromadb`. With what they
pull in (`pytz`, `h2`, `aiohttp` and its small deps) that is about 9 MB on
disk. The spec no longer ships the SDK's `.py` source copies next to its
frozen modules (4.8 MB that were never imported), so the folder Core grows by
6.1 MB on macOS arm64 (`one-memory-runtime` 350.8 MB → 356.9 MB). qdrant's
optional `fastembed` is not shipped: it downloads its BM25 model at runtime,
and without it the SDK's Qdrant store degrades keyword search to pure vector
search (the same as Chroma, whose keyword channel is empty). `faiss-cpu`
(~18 MB) is not shipped either, since MemHub refuses FAISS (above), and the
spec excludes it by name. `test_storage_backend_packaging` keeps the spec,
`pyproject.toml` and the offered providers in step. The hidden
`one-memory --storage-drivers` imports each driver and reports which ones a
frozen Core can load. Set `ONE_MEMORY_FROZEN_CORE` to run those checks
against a build.
