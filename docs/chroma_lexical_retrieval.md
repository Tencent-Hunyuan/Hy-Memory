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

Execution results and exact operational instructions will be appended after the
corresponding checks actually run.
