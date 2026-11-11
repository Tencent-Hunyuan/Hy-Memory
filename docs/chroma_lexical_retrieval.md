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
