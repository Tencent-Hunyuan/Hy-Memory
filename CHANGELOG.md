# Changelog and Release Status

Hy Memory and MemHub are versioned and released independently.

## Unreleased

- MemHub: backups now include the memories. A backup (`memhub backup create`,
  `POST /api/v1/backup`) used to hold only MemHub's management database; the
  memories and raw turns in the SDK stores under `hy-memory/` were never in
  it, so a restore could not bring them back. A backup is now a directory
  `backups/<name>.memhub-backup/` (owner-only) with a manifest (schema 2),
  the management database and every SDK file of the home: Chroma with the
  live embedding space and any space kept for retention, the Kuzu graphs and
  the SDK's SQLite caches; on a Qdrant server also a JSONL export of every
  memory through the SDK (every layer and status, raw turns included; no
  vectors, which hy-memory 2.0.15 does not return). SDK writes are paused with
  the embedding switch's gate, every SDK client is closed (Kuzu checkpoints)
  and the stores are held offline while files are copied (SQLite through its
  backup API); writes resume on every path. The pause is reported
  (`pause_ms`; about 1.3 s for 1,000 memories). The manifest records the
  backend, the embedding space, the SDK version, the users and the counts per
  user and layer, per vector collection and per graph layer. `memhub backup
  create` shows the size first (`GET /api/v1/backup/estimate`) and writes
  with `--yes`. A restore verifies every file, stages the data, swaps it in
  under a journal, runs the registry, space-scan and graph-probe checks (a
  corrupt graph is quarantined and rebuilt), counts again through the SDK and
  rolls back on any mismatch (`backup_restore_counts_mismatch`); the previous
  data is kept in `backups/pre-restore-*.memhub-previous/`. A killed restore
  is rolled back by the next start. Old backups (one `.sqlite3` file) restore
  as before and say they hold no memories. Restores across backends are
  refused (`backup_restore_backend_mismatch`), and restoring memories onto
  Qdrant is refused (`backup_restore_remote_sdk_unsupported`) until MemHub
  pins an SDK that can write a memory back with its id and vector. The evolve
  schedule no longer holds a read client for good, which kept a backup (and an
  ultra embedding switch's graph re-embed) from closing every client. Contract:
  `memhub/docs/contracts/backup-v2.md`.
- Hy Memory: `HyMemoryClient.export_user_memories()` /
  `import_user_memories()` (and their `async_` forms) export one user's
  vector-store memories with their ids and vectors, and write them back into
  an empty store as they were: every layer including `L1_RAW`, every status,
  the evolution pointers and the times. A node without its vector is
  re-embedded with the current embedding service; a width that does not match
  the store, a node of another user or a non-empty target is refused, and a
  failed write is rolled back. The graph is not touched (rebuild it with
  `build_memory_graph()`). This is what a backup needs to restore memories
  onto another store, a Qdrant server included; `clone_user()` only copies
  within one store under new ids, and `add_extracted()` re-reconciles. No
  version change.
- MemHub: the graph and vector stores can be chosen from the CLI and the API.
  The default stays the embedded Kuzu + Chroma pair. `memhub settings backends
  show|set|test|reset` (and `GET/PUT/DELETE /api/v1/settings/storage-backends`,
  `POST .../test`) select a Neo4j or Memgraph server for the ultra graph and a
  Qdrant server for vectors. The setting is persisted in `config.json`
  (`storage_backends`); the graph password and the Qdrant API key go to the
  secret store. Core connects to the chosen servers before it saves anything,
  then rebuilds the SDK clients the way a mode change does. Before, MemHub
  hard-coded `kuzu` and `chroma`, so `MEMORY_GRAPH_PROVIDER=neo4j` or
  `MEMORY_VECTOR_STORE=qdrant` still logged `provider=kuzu` / `provider=chroma`;
  those SDK variables still have no effect (the setting is the only source).
  Qdrant collections carry a per-home prefix so homes sharing a server stay
  apart; a Neo4j/Memgraph database that already holds a graph must be adopted
  explicitly. Switching moves no memory: what the old store holds stays there,
  untouched, and switching back shows it again. FAISS and Qdrant's embedded
  mode are refused (`storage_backend_faiss_unsupported`,
  `storage_backend_vector_host_required`): they are not safe with the several
  SDK clients MemHub runs on one home. On a custom backend, the embedding
  switch is refused on Qdrant, and in ultra on Neo4j/Memgraph
  (`embedding_switch_backend_unsupported`, until the SDK can re-embed them).
  The Kuzu open probe, the graph-width tools, the startup stray-space scan and
  every upgrade migration are skipped, and none of them touch the old Kuzu or
  Chroma files. Backup, restore, search, recall, delete, evolve and the
  memory map work on every backend. `memhub status` and `memory-processing`
  show the active backends and whether they answer. The App shows them
  read-only ("存储后端：Neo4j + Qdrant（通过 CLI 配置）") in every language and
  offers no control. The frozen Core now ships the `neo4j` and
  `qdrant-client` drivers (+6 MB on macOS arm64, after no longer shipping the
  SDK's unused `.py` copies). A managed restore now also accepts a backup taken
  after the SDK cutover; it used to refuse any backup without the legacy
  `evidence` table. Contract: `memhub/docs/contracts/storage-backends-v1.md`.
- The CLI keeps the refusal Core names in a 5xx body instead of dropping
  it. `memhub models configure` on a machine without a key store answered
  `http_503_result_unknown` while Core had sent `{"detail":"secure_storage_unavailable"}`
  -- an instruction, not a status: the machine has no key store, and the fix
  is to give it one. The four mutation verbs in `core-operations.ts` and
  `submitMemoryWrite` in `core-client.ts` now carry that code
  (`secure_storage_unavailable_result_unknown`). `state` stays `unknown`,
  because a 5xx may have committed part of the request. The body value is held
  to the shape every Core refusal code has -- lowercase words joined by
  underscores -- so a bind address such as `127.0.0.1:8765` can never be
  promoted to a code, which the looser 4xx matcher would have allowed.
- MemHub: a knowledge graph that freezes or kills the process opening it is
  quarantined and rebuilt instead of restarting Core forever. In ultra, Kuzu's
  open of a corrupt graph file held the GIL, grew RSS to 5–13 GB and the OS
  killed Core after 26–33 s; the Runtime relaunched it into the same file in an
  endless `restart_scheduled → spawn_started → ready` loop. Before Core reports
  ready, the live graph is now opened once by a short-lived child process,
  bounded by a wall clock (60 s plus 0.5 s per MB, at most 120 s;
  `ONE_MEMORY_GRAPH_PROBE_TIMEOUT_S`) and an RSS cap polled from Core (2 GB;
  `ONE_MEMORY_GRAPH_PROBE_MAX_RSS_MB`). A probe that is killed, crashes or
  exits non-zero renames the graph and its sidecars to
  `graph.quarantined-<UTC>` on the first failure (never deleted, the two newest
  kept), records `graph_store.quarantined`, starts Core without it and runs the
  normal per-user graph rebuild. `graph-open-in-progress.json` still
  quarantines after two starts in which Core itself died mid-probe. The
  graph-width check reads through the same child and is primed by the probe,
  so Core no longer opens its own `kuzu.Database` (with Kuzu's default buffer
  pool) beside the SDK's. An embedding switch's staging graph is never
  touched, and quarantine waits while a switch is open. `memory-processing`,
  `settings/memory-mode` and `memhub status` report `pipeline.graph_store`
  (`opening` / `ready` / `rebuilding_after_quarantine`, the quarantine count
  and reason), and the App shows "知识图谱文件损坏，已自动重建" in all 11
  languages. The Runtime supervisor waits for the declared startup phase
  (now at most 3 minutes) instead of killing Core at the readiness budget,
  respawns a Core that died inside it (bounded), restarts a Core that is alive
  but has not answered for 90 s after ready (twice the longest loop stall seen
  in the field), and only forgives a crash streak once Core stayed up 2
  minutes, so the relaunch delay grows and the circuit opens instead of a
  relaunch every ~20 s.
- MemHub: `agent_max_tokens` (512–32000) is now a persistent memory-features
  setting (`memhub settings features set agent_max_tokens=6000 --yes` /
  `reset agent_max_tokens`), applied to the SDK's `MemoryConfig` when clients
  are built, so evolve and System 2 output from local models is no longer cut
  off at 2000 tokens without a `launchctl setenv` that is lost on reboot. An
  existing `MEMORY_AGENT_MAX_TOKENS` still sets the default while it is unset.
- MemHub pins `hy-memory==2.0.15`. In ultra, the read client and the write
  coordinator's client in one Core now share one `kuzu.Database` on the live
  graph instead of each opening their own, whose checkpoints overwrote each
  other and lost graph writes; that graph is opened with a 512 MB buffer pool
  and at most 4 Kuzu threads (`MEMORY_GRAPH_BUFFER_POOL_MB`,
  `MEMORY_GRAPH_MAX_THREADS`). The startup graph-open probe now opens the
  graph with the same bounds.
- Memory Strength idle decay now also applies when a memory's timestamp is
  timezone-aware, an ISO string with an offset, or an epoch integer (the shape
  `bump_access` writes back). `compute_strength` could raise `TypeError` on
  those and fell back to `idle_days = 0`; both sides are now converted to epoch
  seconds before subtracting. Unreadable timestamps still fall back to
  `idle_days = 0` and are logged at debug.
- Hermes and OpenCode history imports now produce memories. Neither adapter
  ended a file with a `ScanCursorTouch`, and that event is the only thing that
  turns staged records into a write job: `finalize_sdk_file` is reached from
  the runner's cursor-touch branch alone, while `flush_idle_assemblies` covers
  `priority='live'` and history is staged as `normal`. A history import of
  either source therefore reported `completed` with a non-zero `stored` count
  and left every record in `state='assembling'`, so the library stayed empty.
  Both adapters now emit the touch after a file is read to its end, matching
  `sources/common.py`; a read that stopped early or failed emits none, so a
  partial scan never claims to have reached the end.
- `memory-processing` reports stranded writes on the SDK data plane instead of
  a constant zero. `active_depths()` returns the same number twice there — both
  are the count of `running` rows — so the legacy `running - in_flight_count`
  difference was always zero, and the panel called a stalled queue healthy
  however many workers had died. The field exists for exactly that case (#231),
  and had been constant since #145 moved memory authority to the SDK. The new
  `stranded_depths()` counts the rows whose lease is missing or lapsed, from
  the same read as `running`. The web client also treated only `undefined` as
  "not reported"; JSON carries an unknown value as `null`, which read as a
  healthy zero and suppressed the "status unverified" line.
- MemHub host capture hooks no longer write the database. `one-memory
  capture <host>` used to open the main SQLite file from its own short-lived
  process on every hook, re-create the retired `evidence` / FTS tables on an
  SDK-authority home and write the write queue outside Core's single writer,
  contending for the file lock with Core and with other hosts' hooks. It now
  reads only the runtime receipt and token and posts the hook's JSON to the
  running Core (`POST /api/v1/live-capture/{source}`, 1.5 s budget). Core
  applies the same gates (grant, sync switch, rejection counting, automated
  sessions, the Cursor/Claude Code rule; the migration fence stays on the hook
  side) and stages the turn in-process through the live file-sync path. When
  Core is not running the turn is dropped silently; nothing falls back to a
  local write.
- MemHub no longer sends one turn to the SDK twice when a host's hook and live
  file sync both see it. The two lanes gave the same message different
  identities (the hook carries no workspace and often no message id) and
  separate assembly rows, so both were written as raws. Hook input for a
  source that live sync reads from its own files (every hooked host except
  Qwen Work, while live sync is on) is now left to the file path; only a
  source without file polling is staged from its hook. Tables an older hook
  re-created on an SDK home are dropped by the existing startup cleanup.

## Hy Memory 2.0.15 — 2026-11-10

- The Kuzu graph store bounds its memory, shares one database per
  file, and keeps its setup off the event loop. Every `kuzu.Database` the SDK
  opens (the graph store and the re-embed copies) now gets an explicit buffer
  pool, 512 MB by default (`GraphStoreConfig.buffer_pool_mb` /
  `MEMORY_GRAPH_BUFFER_POOL_MB`; 0 keeps Kuzu's default of about 80% of RAM),
  and at most 4 query threads (`max_threads` / `MEMORY_GRAPH_MAX_THREADS`).
  Graph stores in one process that point at the same file now share a single
  `kuzu.Database`, each with its own connection. Before, every client opened
  its own, and Kuzu's file lock does not stop a second open from the same
  process: the two databases overwrote each other's checkpoints, losing
  writes, and could corrupt the primary-key index. A file corrupted this way
  can make the next open allocate gigabytes. `close()` and
  `build_memory_graph` now run `CHECKPOINT`, so no WAL is left for the next
  open to replay. The schema check and DDL run in the same worker thread as
  the open instead of on the loop thread. One limit remains: kuzu 0.11 holds
  the GIL for the whole `kuzu.Database()` call and has no option to release
  it, so an open that stalls inside Kuzu still stalls every thread in the
  process.

## Hy Memory 2.0.14 — 2026-11-04

- `search()` / `async_search()` take an optional `statuses` for explicit
  L1_RAW search (`layers=["l1_raw"]`, or any search in lite mode), e.g.
  `statuses=["active", "shadow"]`, so raw turns a pro extraction already
  shadowed can still be searched. Default stays active-only. Raw hits now
  report their real `status`. Passing `statuses` to a non-raw search or to
  `scene="all"` raises `ValueError` instead of being ignored.

## Hy Memory 2.0.13 — 2026-11-02

- A model failure during a write now reports failure and stores
  nothing. **Behaviour change for direct SDK users:** writes that used to
  return `success: true` in a degraded state now return `success: false`;
  retry them with a new `request_id` once the model is back.
  - Reconcile (pro / ultra `add` and `add_extracted`): when the reconcile model
    call fails, returns HTTP 200 with empty content, or returns a reply that
    cannot be parsed, or reconcile raises before anything is stored, the write
    used to fall back to adding every extracted memory as-is (no dedup or
    evolution) and report success. That fallback is removed, with its
    `degraded` / `degraded_reason` result fields. The write now returns
    `success: false`, `error_code: 502`,
    `agent_error_code: "RECONCILE_FAILED"`, and the cause in the new
    `reconcile_error_code` (`LLM_UNAVAILABLE`, `EMPTY_RESPONSE`,
    `JSON_PARSE_FAILED`, `RECONCILE_EXCEPTION`). None of the new memories are
    stored, the episode counter does not advance, and `add` rolls back its
    raw turn the same way as an extraction failure
    (`l1_rollback.retry_safe: true`). An empty 200 reply was previously
    counted as "no changes" and reported success with 0 memories; only a
    literal `[]` reply still means that. `RECONCILE_ENABLED=false` is a
    configuration choice and still writes directly.
  - Working / coding path: an extraction model failure used to return
    success with 0 operations. It now returns `success: false` with
    `agent_error_code` `LLM_ERROR`, `EMPTY_RESPONSE` or `JSON_PARSE_FAILED`.
    A model that answers with nothing worth remembering is still a success.
    An embedding failure used to store the experience or fact without a
    vector, where search never finds it, and report success; it now returns
    `EMBEDDING_FAILED` and the node is not stored. A fact embedding is checked
    before any older fact is superseded. A failed write no longer appends its
    turns to the recent trace and records no `chain_rounds`.
    With `MEMORY_CODING_VERBATIM_FALLBACK_ENABLED` turned on, extraction
    failures keep the verbatim fallback.
  - Working / coding reconcile: when the experience reconcile or the fact
    reconcile model call fails, returns empty content, or returns a reply that
    is not a JSON object with an `ops` list, the write returns
    `success: false`, `agent_error_code: "RECONCILE_FAILED"` and the cause in
    `reconcile_error_code` (`LLM_UNAVAILABLE`, `EMPTY_RESPONSE`,
    `JSON_PARSE_FAILED`), the same as chat. Before, experience reconcile fell
    back to adding every draft, and fact reconcile inserted the new fact
    without superseding older ones. Both model decisions now run before
    anything is stored, so a failure in either leaves no experience or fact
    from the write and supersedes no older fact. An experience reply of
    `{"ops": []}` is a valid "no changes" decision: the write succeeds and no
    experience is stored. A non-empty `ops` list with no recognizable op is a
    parse failure. A fact reply of `{"ops": []}` inserts the new fact without
    superseding anything. Fact reconcile still makes no model call when there
    are no candidates.

## Hy Memory 2.0.12 — 2026-11-02

- Intention dedup is now configurable per client
  (`MemoryConfig.recall.intention_dedup_enabled`, default `true`;
  `recall.intention_dedup_threshold`, default `MEMORY_DEDUP_THRESHOLD` or
  `0.95`). Env defaults: `MEMORY_INTENTION_DEDUP_ENABLED`,
  `MEMORY_INTENTION_DEDUP_THRESHOLD`. When on, behaviour is unchanged
  (duplicates found at read time are still deleted); when off, no intention
  dedup runs, at read time or within a write batch, and nothing is hidden,
  dropped or deleted. The threshold applies to read-time dedup only. Refs #241.

- MemHub pins `hy-memory==2.0.12` and exposes that intention dedup switch in
  its memory-features setting (`GET`/`PUT /api/v1/settings/memory-features`,
  `memhub settings features`): the feature `intention_dedup` (default on) and
  the numeric `intention_dedup_threshold` (default 0.95, accepted 0.8–1.0,
  anything else is 422 or a CLI usage error). Unset, both keep the SDK
  default, so nothing changes for the App; a change rebuilds the SDK clients
  like the other feature switches. The API answer gains `parameters`, and
  `pipeline.features` can now carry the threshold as a number. Refs #241.

## MemHub 1.0.6 — 2026-11-04

Build 46, on `hy-memory==2.0.14`. Linux x64/arm64 Runtime and macOS arm64
App and Runtime.

- MemHub App now exposes the complete memory-mode lifecycle. New installs
  explicitly choose lite or pro during onboarding; pro completes only after a
  generation model is active, while ultra remains an advanced Settings choice.
  Settings shows mode effects, graph backfill, the default 03:00 evolve
  schedule and manual evolve. The memory page shows original conversations in
  lite and advanced memories in pro/ultra. Existing installs remain pro unless
  the user changes them, and embedding maintenance blocks mode/evolve changes.
- MemHub pins `hy-memory==2.0.14`. Lite search is plain RAG over raw: the
  App's lite search, CLI context search, `/api/v1/recall` and the Host Bridge
  (`MemHub_recall`, context search) return every vector hit above the floor,
  ranked by score, over active and shadow raws, so history pro already
  processed is still found after switching to lite. The turn view no longer
  drops a hit whose turns do not literally contain the query; it shows the
  hit's closest turn instead. Pro and ultra raw search stays active-only.
  Fixes every raw search failing in packaged builds since #316, which passed
  `statuses` to an SDK (2.0.13) that did not accept it. The lite raw-turn
  page also stops re-listing the whole raw layer on each poll when a live
  session only adds new raws.
- MemHub Core no longer stalls writes or freezes the App for tens of seconds
  on a machine with a long live-sync history. Finished live-sync import jobs
  are pruned to the newest 20 per source (at startup, in bounded batches, and
  every 10 minutes); history imports and every unfinished job are kept. Job
  lookups are filtered in SQL with new indexes (schema migration 63) instead
  of reading the whole table. The durable write queue runs its claims,
  settles and lease renewals on its own threads, so import work can no longer
  starve a lease. Import abandon, resume and enqueue, and the file watcher's
  path attribution, no longer touch the database or the disk on the event
  loop, and the WAL cadence checkpoint runs on its own thread instead of on
  the writer's. The write drain now restarts itself after an unexpected exit,
  reports the failure with its stack, and shows `write_drain` /
  `write_drain_error` in memory-processing and `memhub status`.
- Model Center (#293, #295): Settings splits into 界面语言 / 记忆模式 /
  模型配置 pages, with separate 记忆整理 (generation) and 记忆搜索
  (embedding) lanes. Changing one lane sends only that lane's patch. An
  embedding change shows the switch plan, confirmation, progress, cancel and
  rollback, and the search model only changes after the rebuilt index is
  verified. Model panels open inside the lane that launched them and close
  only after Core confirms the profile is active (#310).
- Lite keeps raw conversations (#297): lite raw writes are no longer stopped
  by the "pause model sedimentation" switch, the memory page lists every
  imported raw conversation by role (by turn since #316), and lite adds a
  manual raw input (source `one-memory-manual`) that never calls a
  generation model.
- The home page follows the memory mode (#298): lite counts raw
  conversations and previews the latest one; pro and ultra read memories.
  Pro and ultra now require an active generation model, in both the mode
  preview and the switch.
- Onboarding (#304) asks for lite or pro and resumes after a restart (schema
  migration 62); pro finishes only once a generation model is active.
- Smaller App fixes: compact home status copy (#307), clearer mode-switch
  effects (#312), Ultra topic nodes are hover-only (#315), and settings
  controls use the content width (#317).
- Interfaces in 11 languages: English, Simplified and Traditional Chinese,
  Japanese, Spanish, German, Portuguese, French, Korean, Russian and
  Indonesian. Every interface language, Simplified Chinese included, sets the
  SDK prompt language (#270). **User-visible:** zh-Hans users now get Chinese
  prompts and Chinese memory text for every conversation, English ones
  included; the App sends the language it displays, also when it follows the
  system. Only an unset language (CLI/API installs, `memhub settings language
  system`) keeps the SDK's zh/en detection.
- Memory modes: lite (no LLM, keeps raw conversations), pro (LLM extraction)
  and ultra (pro plus the knowledge graph and a nightly evolve run). Set with
  `GET`/`PUT /api/v1/settings/memory-mode` or `memhub settings mode`. Raw
  conversations are now kept permanently. `POST /api/v2/evolve` and
  `memhub evolve` run System 2 on demand.
- Safe embedding-model switching: an embedding-space registry, a switch that
  pauses writes, re-embeds every user's vectors and the graph, verifies and
  commits atomically, and resumes after a crash or an App update. Every start
  scans the vector store and merges data left in other spaces. When the
  embedding service is unavailable, search returns `embedding_unavailable`.
- Multi-user Runtime: memory APIs and the CLI take `user_id` (default
  `local`); records are isolated per user; Hermes profiles can be connected as
  separate users. The App keeps using `local`. Hardening (#280): read
  responses echo `user_id`; deleting another user's memory, or one that does
  not exist, is 404 `memory_not_found` (the App and `memhub memories delete`
  treat it as already gone); Hermes never reads a `profiles/` directory, and
  a root that is the `profiles` container or overlaps another user's grant is
  refused (`hermes_root_is_profiles_container`,
  `hermes_root_overlaps_other_user`).
- Configurable SDK features through the CLI and API (`memhub settings
  features`): graph rerank, episode recall, session episodes, episode
  summaries, intention and few-shot, and the intention dedup switch with its
  threshold (`intention_dedup`, `intention_dedup_threshold`, #277). Defaults
  are unchanged.
- Memory writes retry themselves (#278), each time under a new SDK request
  id after checking whether the previous one landed; interrupted writes
  re-run automatically. A model provider failure (401/403, 404, 408, 429,
  5xx, timeout, unreachable) keeps the job pending in place without spending
  a retry, and a per-profile circuit breaker paces the queue (5 s doubling to
  180 s, one probe job at a time). Any other failure is retried after 30, 60
  and 120 s, then `failed`. With `hy-memory==2.0.13` a model failure is no
  longer a silent success, and a failed job's `error_code` names the SDK's
  cause (`hy_sdk_reconcile_empty_response`, `hy_sdk_llm_error`, ...) rather
  than `hy_sdk_write_failed`. **User-visible:** the manual 重新整理 button,
  the "整理结果不确定" display and
  `POST /api/v1/memory-processing/retry-unknown` are gone; a banner
  ("模型配置有问题……记忆暂停处理，已有 N 条在排队") with a button to the model
  settings shows while a model problem pauses processing, and clears on
  recovery.
- Queue, WAL and recall (#278): the WAL stays bounded (64 MiB journal limit,
  idle `TRUNCATE` checkpoints); a disabled or revoked source's jobs are
  `suspended` and resume when it is turned back on; SDK writes and recall
  reads use separate thread pools, and recall has a total budget (504
  `recall_timeout`, `broker_timeout` in clients) instead of starving behind
  stuck writes.
- One Runtime per data dir (#273). App quit asks the service manager at quit
  time: an enabled background service is never stopped, a disabled one has
  every Runtime of the data dir stopped (only after an authenticated identity
  check), and an unknown state stops nothing. At start the App adopts a
  Runtime of the same build, replaces an older one, and silently adopts a
  newer one. The tray quit label follows the current state.
- DeepSeek Harness: the app page's one switch installs and grants recall for
  every detected profile, and keeps new profiles granted without a restart
  (#230); versioned session files (`session.v3.jsonl.zstd`,
  `session.v4.jsonl.zstd`) are imported, and v4 tool output goes through the
  same clipping and scrubbing (#175).
- The packaged Runtime accepts only a full 40-character commit, lowercased; a
  malformed `ONE_MEMORY_COMMIT` fails the build (#235, #236).
- Search and recall pass through the SDK's search parameters with server-side
  score floors; the memory library filters by layer; `/api/v2/raw` returns
  data again; v2 source and import routes; profile version history.
- Upgrade fixes: migration no longer stops without a model profile (#234) or
  on a pre-existing readback mismatch (#229, #240); source grants work after
  the cutover (#233); reads use the active embedding (#242); the embedding
  endpoint no longer follows the generation `base_url`.
- Hermes import drops gateway notices and probes and unwraps relayed
  messages; `memhub logs` no longer reports valid older records as damaged.

## Hy Memory 2.0.11 — 2026-11-01

- Published
  [`hy-memory==2.0.11`](https://pypi.org/project/hy-memory/2.0.11/).
- LLM prompts now ship in 11 languages: `en`, `zh-Hans`,
  `zh-Hant`, `ja`, `es`, `de`, `pt`, `fr`, `ko`, `ru` and `id`. They live in
  one registry, `hy_memory/prompts/`, with one module per language. Call
  sites use `get_prompt(name, language)`, so a new language only needs a new
  module. The registry covers chat extraction (system prompt, basic_info,
  intentions, few-shot including Example 11), the no-intention variant,
  reconcile and its search-query prompt, the working/coding extractor, both
  coding reconcilers and the chain summary, the episode and topic summaries,
  System 2 evolve, and the user name line. The English and Simplified Chinese
  strings are unchanged byte for byte, and a test pins them. The old names
  (`prompts_zh.*_ZH`, `extractor.EXTRACT_SYSTEM_PROMPT`, ...) still import.
  The nine new languages are machine translations, and native-speaker review
  is recommended.
- New optional `language` to choose the prompt language
  explicitly. Set it on `MemoryConfig.language` (env `MEMORY_LANGUAGE`) for
  a client default, or per call on `add` / `async_add`, `add_extracted`,
  `finalize_session`, `evolve` and `sweep`. A per-call value overrides the
  config. The value goes through every LLM call of the request: extract and
  basic profile, reconcile, the coding path, the summaries and System 2.
  Aliases: `zh`, `zh-CN` → `zh-Hans`; `zh-TW`, `zh-HK` → `zh-Hant`; `pt-BR` →
  `pt`. An unknown code raises `ValueError` listing the supported codes, and
  the HTTP server returns 400. With an explicit language, memories are
  written in that language even when the conversation uses another one.
  Without a language, the existing zh/en auto-detection runs unchanged and
  memories keep the source language. `search` has no `language`: its judge
  and rewrite prompts are English-only and keep the query's language.
- The HTTP server accepts `language` on `/api/v1/add` and
  `/api/v1/add_extracted`.

## Hy Memory 2.0.10 — 2026-11-01

- Published
  [`hy-memory==2.0.10`](https://pypi.org/project/hy-memory/2.0.10/).
- New `hy_memory.reembed` module for switching the embedding
  model. Writers must be paused for the whole run. Nothing is derived from
  the provider or endpoint: source and target are named explicitly, and the
  identity of a space is the model name plus the width (`space_identity`).
  Only Chroma and Kuzu are supported.
  - `vector_space_names(config, base_name=None)` returns every physical
    collection a client opens for the six vector roles: memory, entities,
    coding keys and working facts, each in the working and coding variants.
  - `reembed_vector_space(source, target, embedding=...)` pages every raw
    source collection, covering all users and all layers including L1_RAW. It
    embeds the text the write path embedded (`reembed_text`: the canonical
    state's `state_retrieval_text`, a schema's `retrieval_text`, otherwise the
    document). It then writes each record into the target with the same id,
    document and metadata. Each batch's widths are checked before anything is
    written. A cancel stops at a batch boundary (`ReembedCancelled`). A rerun
    skips the ids the target already holds, so it resumes, and a merge never
    overwrites the target. The source is never written.
  - `plan_reembed`, `verify_reembed` and `drop_vector_space` complete the
    vector side. `verify_reembed` checks ids, metadata, per-user counts and
    the width, and runs a self-recall sample. `drop_vector_space` refuses
    collections that a live client in this process holds open.
  - `scan_vector_spaces(persist_directory)` is read-only. It lists every
    space on disk with its roles, counts, stored width, per-user counts and
    the models recorded in metadata. `diff_spaces(source, target)` returns the
    ids that the source holds and the target lacks, whatever their status.
  - `reembed_graph(source_path, target_path, embedding=..., target_dims=...)`
    builds a new Kuzu graph at the new width. It copies every node and edge
    and recomputes `Memory.embedding`, `Memory.beh_embedding`, and the Topic
    and HierarchyTopic embeddings, then rebuilds the HNSW index. The source
    graph is opened read-only and kept for rollback. A run that fails or is
    cancelled leaves no target behind, and a rerun starts over.
    `verify_graph_reembed` and `drop_graph` complete the graph side.
- `EmbedService.embed_batch` now splits large batches into
  requests of `embedder.batch_size` texts each (`MEMORY_EMBEDDER_BATCH_SIZE`,
  default 64); before, it sent them all in one request. On a 429 it waits for
  the `Retry-After` time plus jitter, capped at 60 s. It takes a per-call
  `max_retries`. A returned vector whose width differs from the configured
  `embedding_dims` now raises `EmbeddingDimensionMismatchError` and is not
  retried.
- Raw conversations are stored already trimmed. MemHub now keeps every raw
  forever and lite mode stores nothing else, so tool output and long pasted
  logs no longer stay in full. One rule (`hy_memory/utils/raw_trim.py`) is
  applied to each message when the raw is written:
  - `user` and `system` turns are kept in full;
  - `assistant` turns and other speakers (`[John]:`) longer than 500
    characters, after stripping, are cut to the first 500 plus `...`. This is
    the limit history injection used before;
  - `tool` turns keep their head and tail, and the middle becomes
    `[...N bytes omitted by truncate...]`, with the coding limit
    `coding.tool_result_max_bytes` (`MEMORY_CODING_TOOL_RESULT_MAX_BYTES`,
    default 2048).

  The rule is idempotent. It covers chat `L1_RAW` in lite, pro and ultra (lite
  keeps tool turns, because it has no coding route) and the `memory_history`
  ADD record. The coding/working `coding_trace` was already trimmed when it
  was written. Its tool and assistant truncations are now idempotent, so
  reading the history back no longer rewrites the omitted-count markers. The
  text embedded for `L1_RAW` is the trimmed text. New raws carry
  `custom.raw_trimmed=true`.
- Extractor history ("last k messages") now joins the stored raws directly
  and does not trim them again. Raws written before this change have no
  `raw_trimmed` flag, so they are still trimmed with the same rule when they
  are read. The extractor still receives the current batch in full (tool
  turns are still removed on the chat route).

## MemHub 1.0.5 — 2026-10-28

Build 36, on `hy-memory==2.0.8`. Linux x64/arm64 Runtime and macOS arm64
App and Runtime. Upgrading from 1.0.4 migrates the existing library to SDK
authority on first launch.

- MemHub turns off the HY pipeline log and trace at every diagnostic level
  (hy-memory 2.0.8 `MEMORY_PIPELINE_LOG_ENABLED=false`,
  `MEMORY_PIPELINE_TRACE_ENABLED=false`), so the per-step JSONL files under
  `hy-memory/governed-v2/logs/` and the `pipeline_logs` rows stop growing.
  The Debug panel no longer shows the HY pipeline section. Existing files are
  left in place.
- HY SDK text logs are written once, to `hy_memory.log`. They are no longer
  copied to `service.log` and `service-error.log` through the SDK's
  stdout/stderr streams. SDK warnings and errors also go to `core.log`, so the
  Debug panel still shows them. A live switch of the diagnostic level now also
  changes the level of `hy_memory.log`.

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

## Hy Memory 2.0.9 — 2026-11-01

- Published
  [`hy-memory==2.0.9`](https://pypi.org/project/hy-memory/2.0.9/).
- Placeholder values no longer overwrite the user's basic profile (L0). A
  small extraction model filled `basic_info` with `{"name": "用户"}`,
  `The user` or `N/A` when the user had said nothing about themselves, and
  each placeholder superseded the real value, so the L0 chain read
  费天翔 → Fei → 用户 → The user. Two defences:
  - `upsert_basic_profile` drops placeholder values: empty, `用户`,
    `the user`, `user`, `n/a`, `na`, `none`, `null`, `unknown`, `未提供`,
    `未知`, `不详`, `-`, `?` and the field's own name, compared
    case-insensitively with whitespace and punctuation stripped. When nothing
    is left it writes no new version and no new L0 head. Placeholders already
    stored in the chain no longer override earlier real values when the
    profile is assembled, so `get_user_profile` of an affected user shows the
    last real value again without any migration. This also stops an
    assistant quoting an old memory ("The user's name is 用户.") from being
    written back.
  - The extraction prompts (zh and en) state the rule: only what the user
    said in a `user` turn, never the field name or a placeholder, `{}`
    otherwise, with a small-talk counter-example.

## Hy Memory 2.0.8 — 2026-10-28

- Published
  [`hy-memory==2.0.8`](https://pypi.org/project/hy-memory/2.0.8/).
- New `MEMORY_PIPELINE_LOG_ENABLED` (default `true`) turns the pipeline JSONL
  log (`{MEMORY_LOG_DIR}/<subdir>/<date>.log`) off. With `false` no JSONL
  file or directory is written. When `MEMORY_PIPELINE_TRACE_ENABLED` is also
  `false`, `store_pipeline_log` becomes a no-op that returns `True` at once:
  no timing, redaction, background task or database write. The init log line
  now states whether the JSONL log is on or off.
- Conversation and memory text no longer appears in INFO or WARNING logs. The
  `[add]` input, search queries and rewrites, coding experience tasks, fact
  statements and subjects, reconcile op details, extracted info, embedding
  inputs and unparsable model output moved to DEBUG. INFO lines keep ids,
  counts and character lengths; parse-failure warnings remain but report only
  the length of the output, e.g. `failed to parse output (812 chars)`. The
  `[embed]` INFO line now carries `request.input_chars` instead of
  `request.input`. The `JSON_PARSE_FAILED` error message from extraction no
  longer embeds the model output either.

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
