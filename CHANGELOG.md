# Changelog and Release Status

Hy Memory and MemHub are versioned and released independently.

## Unreleased

- MemHub's DeepSeek Harness app page can grant recall again (#230). The
  "一键插入应用" switch used to be greyed out with `unsupported_host`, and a
  profile installed from the CLI stayed `install-only` with no way to approve
  it. The one switch now covers every detected profile
  (`deepseek-harness:<profile>`): on installs each profile's MCP patch and
  grants recall, off removes the patch and revokes the grant. The page lists
  each profile with its state, and names any profile that failed while
  keeping the others. While the switch is on, a profile created later is
  installed and granted automatically, without restarting MemHub; the Runtime
  also no longer fails every host read when it meets a profile created after
  it started. The harness docs no longer describe an unmounted sources page.

- MemHub's `deepseek-harness` source imports session files whose names carry the
  format generation: `session.v3.jsonl.zstd` and `session.v4.jsonl.zstd` are now
  discovered alongside the original `session.jsonl[.zstd]`. Discovery accepts
  any `session[.vN].jsonl[.zstd]` name and the header's `version` decides
  whether the session is parsed (v0/v3/v4) or refused as `unsupported_schema`.
  A refused file is reported and keeps no cursor, so the release that learns
  its format reads it from the top.
  v4 tool results, which are written as plain `text` blocks rather than
  `tool-result` blocks, are read through the same clipping and credential
  scrubbing path. Previously every versioned session was invisible: the import
  job completed with `discovered 0` while the files sat on disk.

## Hy Memory 2.0.12 — 2026-11-02

- Intention dedup is now configurable per client
  (`MemoryConfig.recall.intention_dedup_enabled`, default `true`;
  `recall.intention_dedup_threshold`, default `MEMORY_DEDUP_THRESHOLD` or
  `0.95`). Env defaults: `MEMORY_INTENTION_DEDUP_ENABLED`,
  `MEMORY_INTENTION_DEDUP_THRESHOLD`. When on, behaviour is unchanged
  (duplicates found at read time are still deleted); when off, no intention
  dedup runs, at read time or within a write batch, and nothing is hidden,
  dropped or deleted. The threshold applies to read-time dedup only. Refs #241.

## MemHub 1.0.6 — 2026-11-02

Build 37, on `hy-memory==2.0.11`. Linux x64/arm64 Runtime and macOS arm64
App and Runtime.

- Interfaces in 11 languages: English, Simplified and Traditional Chinese,
  Japanese, Spanish, German, Portuguese, French, Korean, Russian and
  Indonesian. Choosing any language other than Simplified Chinese also sets
  the SDK prompt language; Simplified Chinese keeps the automatic zh/en
  detection.
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
  separate users. The App keeps using `local`.
- Configurable SDK features through the CLI and API (`memhub settings
  features`): graph rerank, episode recall, session episodes, episode
  summaries, intention and few-shot. Defaults are unchanged.
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
