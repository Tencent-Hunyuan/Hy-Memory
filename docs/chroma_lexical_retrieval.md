# Local lexical retrieval for Chroma

## Acceptance recorded before implementation

Baseline: `b90d1303f0de3084c512902e96113d8956da7074`, 2026-11-11.
Scope: opt-in local PersistentClient lexical retrieval; hybrid read and reconcile;
no change to default reader, global fusion, Working/Coding, or MemHub release pins.

- Real Chroma + SQLite FTS5 must recall an error code/path outside dense top-30
  through the public SDK search interface, respecting its final score threshold.
- Document and raw-query analysis must agree for Chinese, mixed text, code names,
  paths, filenames and versions; query syntax is compiled, never passed through.
- User/isolation/source/layer/status/latest filters apply before lexical top-k.
  Hybrid retains superseded candidates, excludes shadow, expands evolution before
  applying the existing event-time cutoff.
- Content/tags changes remove old terms and add new terms; filter-only changes
  update the projection without analyzing text. Deletes remove projection rows.
  Embedding/access-count updates never reanalyze lexical text.
- Persist dirty state before authoritative writes. An index failure after a known
  successful Chroma write does not turn that write into a false failure. Dirty or
  building indexes never serve stale hits. Cancellation cannot release a lock
  while a synchronous authoritative operation is still running.
- Cross-client/process writes use one collection lock, including lexical-disabled
  clients invalidating an existing projection. Closing one client cannot close
  another client's shared Chroma system.
- Initialize audits the complete authoritative projection under the lock (not just
  count), repairs changes, and detects offline restores/renames and reused names.
  Rebuild supports more than one page and publishes ready only after validation.
- Reembed's direct target writes and collection deletion use the same coordination.
  FTS5 absence/query lock contention degrades lexical only; mutation cannot proceed
  if an existing usable projection cannot first be invalidated safely.
- Scalar/batch keyword contracts carry raw_query and user_ids; Qdrant remains
  compatible and does not silently fall back because new arguments are unconsumed.

## Planned validation (not yet results)

Run focused analyzer/index/store/recovery/public-search/reconcile/Qdrant/reembed
tests, then `uv run pytest tests/ -q -o log_cli=false`. Test seams are the analyzer,
index operation interface, Chroma store, reconcile and public search described
above. No model service or real user memory is required for mechanism tests.

Mutation validation must make targeted tests fail when dirty protection or raw
query forwarding is deliberately broken, then restore code and rerun successfully.
Do not record planned checks as passing; keep baseline failures separate.

Benchmark dense/lexical/hybrid using fixed synthetic labeled fixtures, seed 42,
1k/10k/100k records, fixed dense budget and K. Report Recall@5/10, MRR@10, false
positives, cold/warm P50/P95, writes, rebuild/audit time, projection size and Chroma
materialization count. Proposed warm lexical budgets: <=100ms at 10k and <=500ms
at 100k on declared hardware. Synthetic evidence is not a business recall claim.

## Rollback and dependency evidence

Disable `vector_store.chroma_lexical_enabled` (environment:
`MEMORY_CHROMA_LEXICAL_ENABLED=false`) to stop lexical querying. Same-version
writers still invalidate existing projections. Before running an older SDK, stop
all clients and remove only the derived lexical sidecars; keep Chroma data. Do not
delete live lock files. Re-enable with this SDK to audit/rebuild from Chroma.

Declare filelock directly, record the installed/locked version and its upstream
license before submission. No third-party source code is copied.

The validation environment uses filelock 4.2.0, whose installed package metadata
declares MIT. The lock resolves Python-compatible versions (3.19.1 for Python
3.9 and 4.1.0 for Python 3.10). Upstream licenses were checked at the locked tags:
[3.19.1: Unlicense](https://github.com/tox-dev/filelock/blob/3.19.1/LICENSE),
[4.1.0: MIT](https://github.com/tox-dev/filelock/blob/4.1.0/LICENSE), and
[4.2.0: MIT](https://github.com/tox-dev/filelock/blob/4.2.0/LICENSE).
No filelock source is vendored into this project.

Execution results and exact operational instructions will be appended after the
corresponding checks actually run.

## Enable and rebuild

The index is opt-in and only supported by local Chroma PersistentClient. Configure
the same SDK settings used for the authoritative collection:

```python
from hy_memory.config import MemoryConfig
from hy_memory.data.vector_store_chroma import ChromaVectorStore

config = MemoryConfig.from_dict({
    "vector_store": {
        "provider": "chroma",
        "persist_directory": "./memory-data/vector",
        "collection_name": "agent_memories",
        "chroma_lexical_enabled": True,
    },
})

store = ChromaVectorStore(config)
await store.initialize()  # Audits current Chroma data before serving lexical hits.
status = await store.rebuild_lexical_index()  # Explicit forced rebuild, if needed.
print(status["state"])
await store.close()
```

`MEMORY_CHROMA_LEXICAL_ENABLED=true` supplies the environment default. Explicit
configuration takes precedence. Selecting `reader="hybrid"` remains necessary
for read-side keyword recall; the SDK's reader default is unchanged. Reconcile
also uses this keyword interface with its existing scoring policy. The index
alone does not make the public hybrid search independent of query embeddings.

For an already initialized store, inspect `(await store.get_stats())["lexical_index"]` to
distinguish a ready index from a disabled, dirty, building or unavailable one.
The derived files live in `.hy_lexical/` beneath the Chroma persist directory.
Each collection has a name-hashed SQLite sidecar and a persistent lock file.

## Query semantics

Both document content plus tags and the original query use the same versioned,
deterministic analyzer. The Chroma backend prefers `raw_query`; callers that only
provide `query` remain supported. Historical `search_text` is not authoritative.

- English text is normalized and case-folded without a model-dependent lemma
  pipeline. Snake-case and camel-case names also expose component words.
- Chinese runs use adjacent two-character tokens. A multi-character query run
  is compiled as a contiguous phrase of these tokens. Latin and Chinese runs are
  separated even when no whitespace occurs between them.
- Complete code symbols, error codes, filenames, paths and versions have a
  separate encoded representation. It preserves punctuation, so `C++` does not
  become `c`, and `v1.2.3` differs from `v1/2/3` as a complete symbol.
- Paths use forward slashes, preserve case, and also expose the basename. They
  are treated as text: no file access or filesystem resolution occurs.
  Analyzer version 2 retains complete Unicode paths and adds outer CJK/Latin
  boundary aliases, so `配置文件在src/模块.py中` exposes `src/模块.py`.
  Interior Chinese directories remain intact. A wholly Chinese leading
  component is kept literally because its boundary with Chinese prose is
  ambiguous; whitespace or quotes make that boundary explicit. Initialization
  rebuilds projections produced by the previous analyzer version.
- Ordinary words and symbols are encoded before generating MATCH expressions.
  User input cannot supply FTS operators, column selectors or wildcards. Empty
  input, punctuation-only input and nonpositive top-k return no hits.

Ordinary and complete-symbol candidates are filtered before their individual
top-k limits. Ordinary matching uses terms or Chinese phrases; strong symbol
matching requires all complete symbols extracted from the query. The branches
are merged by node ID, retaining the higher score. Scores descend, with stable
node-ID ties. For `b=max(0,-fts_bm25)`, the current mapping is:

```text
ordinary:       0.85 * b / (1 + b)
complete:      0.9 + 0.1 * b / (1 + b)
```

These are bounded relevance signals, not probabilities. The reader consumes the
normalized signal through its existing keyword-only/fusion path and applies its
existing final score floor. Reconcile keeps its own existing weights and gate.
No query-relative normalization promotes an isolated weak hit to score 1.

User, agent/isolation, source, layer, status and latest filters precede candidate
truncation. Multi-key isolation precedes a single key, which precedes user/agent
field filtering; nonempty `user_ids` precedes `user_id`. Include and exclude
sources combine, with exclusion winning. Session scope uses the existing
isolation-key contract. Hybrid can recall superseded nodes and then resolve
their visible evolution head; the existing event-time filter runs after that
expansion. This feature does not add Chroma `tags_match_any` filtering.

## Consistency and recovery

Chroma remains authoritative. SQLite stores only its derived retrieval projection
and the identity/version/state needed to decide whether it can be queried.

The SDK holds a collection file lock across durable invalidation, the Chroma
operation and projection repair. A known successful primary write remains
successful if only projection maintenance fails; the projection stays dirty and
cannot answer queries. Failed or ambiguous primary writes are not replayed during
recovery. Reinitialization reads the actual authoritative state instead.

A content-only Chroma update reads the existing record's embedding while holding
the lock and supplies it with the changed document. This preserves the vector
and avoids invoking Chroma's default document embedding function. Ordinary
lexical queries materialize metadata without requesting embeddings.
Both `update_embedding` and embedding-only `update_payload` take this same lock,
including from lexical-disabled local clients, without invalidating or updating
the lexical projection.

Reads that encounter the collection lock, dirty/building state, missing FTS5 or
another index failure return no lexical hits. The dense channel continues.
Queries never trigger an unbounded rebuild. A write that cannot safely invalidate
an existing projection must fail before changing authoritative data. Cancellation
does not release the synchronous operation's lock while its Chroma work continues.

Every enabled initialization scans the full authoritative collection under the
same lock. It compares record identities and retrieval fingerprints, not only
counts, so offline source/content changes and restored data are detected even
when the number of records is unchanged. Unchanged text is not reanalyzed.
Schema/analyzer/collection identity changes require rebuilding. The index becomes
ready only after the complete scan and validation succeed. A forced rebuild may
reanalyze every record; interrupted builds remain unavailable.

The full audit is intentionally O(N), including unchanged starts. Rebuilds pause
cooperating collection writes; online readers can continue via dense retrieval.
This version does not implement generations, replay queues, a background worker
or zero-downtime lexical rebuilds.

All concurrent writers must use this SDK version's lock/invalidation protocol.
Direct Chroma writes or older SDKs require stopping online access and reopening
with this SDK afterward. Existing offline MemHub migration/restore operations are
covered by initialization's full audit; this change does not enable the feature
or update the pinned SDK in MemHub. SDK reembed target writes and collection drops
use the same invalidation coordination. Closing one store does not close the
Chroma system shared by other live clients.

## Reproduce the benchmark

Run from the repository root in the dependency environment used for validation:

```powershell
uv run python scripts/benchmark_chroma_lexical.py --sizes 1000 10000 100000 --seed 42 --warmup 10 --repeats 100 --output "$env:TEMP/hy-memory-chroma-lexical.json"
```

The script creates temporary Chroma data, synthetic labels and deterministic
16-dimensional vectors. Dense-missed targets deliberately model the candidate
pool failure mechanism. They are not results from a production embedding model.
Development and held-out query labels are fixed in
`tests/fixtures/chroma_lexical_retrieval.json`; held-out rows must not be used to
tune the score mapping. Other-user and excluded-source copies exercise filtering,
and periodic long records plus hot terms exercise different retrieval costs.

Dense uses the actual LegacyReadPipeline and hybrid uses the actual
HybridReadPipeline with shared post-processing, dense budget 30, lexical budget
80, result K=10, and reader min-score 0.4. Lexical is the backend's ranked top-10
candidate baseline, without the reader's final score gate. Graph and rerank are
disabled; the script records the existing fusion parameters it fixes.

Each labeled query is evaluated once per mode and reported individually, by
category and by split. `--repeats` is the total warm latency sample count per
mode, cycling the labeled queries and hot/long workloads; it is not a multiplier
for every query. `first_call_ms` measures the first invocation after loading and
does not claim a flushed OS cache. Embedding vectors are precomputed and no model
service is contacted. The report includes platform/package versions, seed and
fixture digest, warm P50/P95, Recall@5/10, MRR@10, labeled false positives,
the reader's actual keyword-only provenance and its labeled false positives,
materialization/embedding-transfer counts, writes, audit/rebuild time and sidecar
bytes. At 1k and 10k it also loads the same records into a separate collection
with lexical indexing disabled and compares single/batch write costs. The report
records the measurement order. Read-transfer counters include the actual
reader's other read work. Use `--contention-note` to record concurrent workloads;
latency budgets should ultimately be checked without unrelated resource contention.
The separate `added_over_dense` set comparison includes ordinary dense candidates
whose final rank changed. It must not be interpreted as the keyword-only channel's
false-positive rate; use the recorded `vdb_keyword_only` provenance for that.

Use `--sizes 1000 --warmup 1 --repeats 3 --write-samples 2` for a smoke run. The
declared 10k/100k latency budgets above are acceptance targets, not measurements;
results and any failures must be reported on the measured hardware.

## Validation record (2026-11-11)

Acceptance was committed as `7161052` before production implementation. The
immutable pre-change source is `b90d1303f0de3084c512902e96113d8956da7074`.
Baseline execution overlapped development in a separate source snapshot; this
record does not claim that every baseline test finished before implementation.
Local checks used Windows x86-64, Python 3.13.13, Chroma 1.5.9, SQLite FTS5,
pytest 9.1.1 and filelock 4.2.0. Local Python 3.12 dependency preparation did not
finish; no Python 3.12 test result is claimed. The repository CI uses Python 3.12.

| Check | Observed result |
|---|---|
| Analyzer/index/keyword/public-search/recovery/score/reconcile/Qdrant focused suite | 138 passed, 42.55s |
| Reembed lifecycle, existing Chroma filters/payload, source/evolution/layer regression suite | 36 passed, 9.39s |
| Reembed lifecycle after limiting coordination to the memory role | 2 passed, 5.65s |
| Spec review follow-up: missing-source filters and content/vector update | 6 passed, 3.35s |
| New analyzer/index/benchmark and feature test files: Ruff | Passed |
| `git diff --check`; `uv lock --check --offline` | Passed |

Focused command:

```text
python -m pytest tests/test_chroma_lexical_analyzer.py tests/test_chroma_lexical_index.py tests/test_chroma_keyword_search.py tests/test_chroma_lexical_public_search.py tests/test_chroma_lexical_recovery.py tests/test_hybrid_min_score_gate.py tests/test_reconcile_hybrid_v2_align.py tests/test_qdrant_batch_search.py -q -o log_cli=false
```

The full default suite was executed with
`python -u -m pytest tests/ -q -o log_cli=false -o faulthandler_timeout=60`:

| Source | Passed | Failed | Skipped | Errors | Deselected |
|---|---:|---:|---:|---:|---:|
| Immutable baseline | 1772 | 70 | 41 | 21 | 19 |
| Implementation before review follow-up | 1904 | 68 | 29 | 18 | 19 |

The full suite is **not green** in this local environment. JUnit test-ID comparison
found no newly failing test: all 86 current failing/error test IDs also failed in
the baseline. Of these, 85 normalized messages matched; the remaining reader
class-dictionary assertion listed the same Python 3.13 metadata attributes in a
different order. Observed baseline errors include FAISS open-file exhaustion,
Windows database-file cleanup errors, GBK decoding, unavailable `tzset`/symlink
privileges and native Chroma HNSW reads. These observations do not establish one
common root cause. The review follow-up was checked by the focused suites above;
it is not represented as another complete full-suite run.

Two isolated-process mutations failed the intended assertions (exit 1), without
editing tracked implementation files:

- Pretending dirty metadata was ready exposed an unchanged row after another
  record's projection update failed; the recovery test rejected that stale use.
- Dropping reader `raw_query` forwarding lost the `C++` target outside dense
  top-30 in a corpus containing ordinary `C` distractors.

The normal focused suite subsequently passed without either mutation. Standards
review closed the duplicated projection-field ownership and missing type-hint
findings; Spec review closed the confirmed missing-source `$nin` parity defect.
No broad storage or fusion framework was introduced.

### Measured synthetic benchmark

Hardware: Intel Core i9-13900HX, 32 logical processors, approximately 16 GiB RAM,
local Windows 11 filesystem. SQLite 3.50.4, NumPy 2.5.4; other versions are listed
above. Each timing row uses seed 42, 10 warmups and 100 warm samples per mode.
The 1k/10k run overlapped repository tests; the final 100k run started after both
full suites ended. This is a shared development machine, not an isolated lab.

| Records | Dense reader P95 | Lexical P95 | Hybrid reader P95 | Unchanged startup audit | Forced rebuild | Projection bytes |
|---:|---:|---:|---:|---:|---:|---:|
| 1,000 | 21.62ms | 25.91ms | 38.71ms | 0.33s | 0.52s | 1,089,536 |
| 10,000 | 386.33ms | 52.90ms | 261.54ms | 4.76s | 6.26s | 10,407,936 |
| 100,000 | 2,519.72ms | 124.25ms | 2,272.42ms | 50.20s | 60.53s | 105,271,296 |

The proposed warm lexical budgets were met at 10k and 100k. These are separate
runs with different contention; the table does not establish that lexical scales
better than every alternative or that adding it makes the existing reader faster.
The full reader includes other existing retrieval and post-processing work.

At 1k/10k, 20 single-upsert samples compared enabled indexing against a separate
same-version collection with indexing disabled: enabled/disabled P95 was
98.02/54.04ms (1.81x) and 289.36/70.38ms (4.11x), respectively. At 100k the enabled
single-upsert P95 was 101.90ms; no disabled write-control run was made at that size.
Initial loading of 100k records in batches of 1,000 took 334.98s. These costs, and
the approximately 50-second unchanged startup audit, are explicit first-version
tradeoffs of synchronous coordination and full authoritative validation.

On the 24 positive synthetic queries, dense Recall@5/10 was 0.5 and lexical and
hybrid Recall@5/10 were 1.0. The corpus deliberately puts selected targets outside
dense top-30, so this demonstrates a mechanism rather than a production recall
improvement. Exact provenance at both the final 1k reporting smoke and 100k run
recorded 22 keyword-only final results, of which 10 were nonrelevant under the
fixture's labels (45.45%). On the three negative queries the final 1k smoke's
lexical baseline returned no results; at 100k it returned one candidate for each
of two negative queries. Dense and hybrid returned ten synthetic vector
distractors for every negative query at both sizes. Partial ordinary
term matches can still enter the hybrid output; the score mapping is a heuristic,
not a calibrated relevance probability. Real deployment quality requires a real
labeled corpus; the existing global fusion was not tuned to these held-out rows.

Run history is retained rather than hidden: the first combined run stopped on a
native Chroma compaction error during the 10k authoritative load; a fresh 10k run
succeeded. A subsequent combined run completed 1k/10k but stopped recording after
40k of its 100k load without a completion result. That partial attempt is not a
pass. The dedicated final 100k run completed with exit 0 and ready state containing
100,000 indexed records. A final-script 1k smoke with exact provenance reporting
also completed with exit 0. No production retry/fallback was added to hide native
Chroma write failures, and no unsupported root cause is assigned to them.

### PR #432 regression fixes (2026-11-11)

- Serialize content read/restore with both vector-only update entry points using
  the existing collection lock. Real Chroma tests cover both interleavings,
  separate clients and lexical enabled/disabled. An isolated-process mutation
  bypassing only the vector lock makes all eight cases fail on the final vector
  reverting to its old value; tracked implementation files were not modified.
- Restore Qdrant keyword search's independent agent filter, including calls
  without a user and calls with isolation keys. Scalar and remote batch RPC
  filters agree. Dense search retains its prior scope behavior.
- Add boundary aliases while preserving original Unicode path symbols, bump the
  analyzer version, and verify automatic rebuilding of unchanged old documents.
  Real public hybrid search recalls ASCII and Chinese-directory paths touching
  Chinese prose, including `配置文件在src/foo.py中`.

Targeted tests failed before implementation on concurrent vector updates,
missing agent filters and Chinese-adjacent path symbols. Final verification
after consolidating the lock protocol:

```powershell
.venv/Scripts/python.exe -m pytest tests/test_chroma_lexical_analyzer.py tests/test_chroma_lexical_index.py tests/test_chroma_keyword_search.py tests/test_chroma_lexical_recovery.py tests/test_chroma_lexical_public_search.py tests/test_qdrant_batch_search.py -q -o log_cli=false --tb=short --show-capture=no
```

Result: **149 passed**, Python 3.13.13 / Chroma 1.5.9. The initial red run used
the recovery, Qdrant batch, analyzer and public-search files, selected with
`-k 'serialize_across or scope_and_encoded or full_symbols or touching_chinese'`.
Before the final lock-protocol consolidation, the broader 207-test run also
passed, including payload/list filters, Qdrant atomic writes/local thread
ownership, reembed, and reconcile scope/hybrid alignment. No complete SDK-suite
rerun or new benchmark is claimed for this follow-up.

`git diff --check` passed. Ruff reports the same four pre-existing findings as
`0d7d7df` (one unused `asyncio` import and three ambiguous `l` names); comparing
diagnostic codes and source lines found no introduced findings. Full Ruff is
therefore not recorded as passing.
