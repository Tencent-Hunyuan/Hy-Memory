# Qdrant native retrieval — local execution record

Date: 2026-11-12, Asia/Shanghai. Local implementation and applicable validation
are complete. The complete SDK suite is **not green on this Windows host**;
baseline comparisons and installation limitations are recorded below. No
Issue, PR, draft, remote comment, push, or reopening of #432 was performed.

## Branch, scope and material preservation

- Branch: `codex/qdrant-native-retrieval`.
- Fixed upstream base: `01ccf50f72694390e5979065b5396ed4835cccd0` (`origin/main`
  fetched at the start). The previous Chroma branch is an ancestor of this
  upstream base through merged #438. No private Chroma history is added to the
  new branch's diff.
- Implementation commits: `9e91c33`, `fbd616a`; subsequent local validation
  commits preserve the resolved dependency versions and add this record.
- The three original acceptance/server/client materials remain untracked and
  unchanged. SHA-256 comparisons against backups in the handoff directory's
  `preserved-before-implementation/` all returned true. The supplied handoff
  directory and ZIP were not changed.
- Only two production modules were added: `data/qdrant_native.py` and
  `pipelines/reader_native.py`. No MemHub, Chroma adapter, writer, graph
  projector or re-embed implementation was changed.
- One evidence-driven scope addition: `utils/model_call_context.py` receives
  only `from __future__ import annotations`. The upstream file evaluates
  `str | None` during import and fails on real Python 3.9.12. The declaration
  restores import so the legacy route works and native can explicitly reject
  Python 3.9. `test_reader_selection.py` also recognizes Python 3.13 class
  metadata and registers the new reader.

## Executed environment

Windows, PowerShell, `uv 0.11.15`. Successfully tested:

| Environment | Actual result |
|---|---|
| Python 3.13.13 + client 1.20.0 + Server 1.20.0 | Native contracts and real Server tests pass |
| Same versions, native-only extra | `find_spec('fastembed') is None`; native integration passes |
| Python 3.13.13, dev/faiss/qdrant/postgres extras | 221 focused legacy/graph/reconcile regressions pass; full SDK suite executed |
| Real Python 3.9.12, minimal import/test dependencies | SDK import and explicit native `unsupported`; 7 focused legacy/default/version tests pass |
| Planned full Python 3.12 native install | Not completed: network/DNS/download failures, then disk-full extraction; no Python 3.12 test pass claimed |
| Full Python 3.9 extra installation | Not completed: required wheels absent from offline cache; minimal tests do not certify the complete extra |

Server image: `qdrant/qdrant:v1.20.0`, digest
`sha256:9bfc89f9914fc14876a88c05270685921a55b8877b254a98bcc1cc2f0a424cd2`.
The actual Server reported commit
`222b2e44ab1c3f17e8246396ead6558e45041df7`.
Only the dedicated `http://127.0.0.1:16333` endpoint was used. Every fixture
owns a random `hy_native_test_<uuid>_2` collection and cleans only that name.
The test Server is disposable; no application database was selected.

The lock resolves client 1.16.1 for Python 3.9 and 1.20.0 for Python >=3.10,
including the existing `qdrant` and `all` extras. No other dependency version
was upgraded. The project lock metadata also catches up from 2.0.13 to the
checkout's existing 2.0.16. Unrelated lock-format churn from the older local
uv writer was removed while retaining its resolved project/client blocks;
`uv lock --check --offline` and locked native sync both pass. Relative to the
base, the final lock diff is 18 additions and 14 deletions.

## Commands and results

The successful native environments were created with:

```powershell
$env:UV_PROJECT_ENVIRONMENT="$env:TEMP\hy-memory-native-py313-pure"
uv sync --offline --locked --python 3.13 --extra dev --extra qdrant-native
& "$env:UV_PROJECT_ENVIRONMENT\Scripts\python.exe" -c "import importlib.util; assert importlib.util.find_spec('fastembed') is None"
$env:HY_MEMORY_TEST_QDRANT_URL='http://127.0.0.1:16333'
$env:PYTHONUTF8='1'
& "$env:UV_PROJECT_ENVIRONMENT\Scripts\python.exe" -m pytest tests/native_retrieval -o addopts= -q -o log_cli=false --tb=short
```

The full-directory run at `fbd616a` plus the version-identical lock cleanup
passed **66 tests**, zero failed/skipped, in 226.44 seconds. The subsequently
added real Server concurrent native/legacy test passed separately, giving
**67 distinct native tests** (41 local contracts, 26 integration tests).
An earlier no-FastEmbed full-directory run passed 62 tests before the final
budget/error/real-chain cases were added. These are separate runs, not a
claim that 67 ran in one invocation.

Focused regression command (same file set as the acceptance plan):

```powershell
& "$env:TEMP\hy-memory-native-py313\Scripts\python.exe" -m pytest `
  tests/test_reader_selection.py tests/test_hybrid_min_score_gate.py `
  tests/test_search_channel_bucketing.py tests/test_search_layer_filter.py `
  tests/test_search_rerank_mmr_gates.py tests/test_source_filtering.py `
  tests/test_evolution_chain_shadow_filter.py tests/coding/test_search_scene_routing.py `
  tests/test_reconcile_failure.py tests/test_reconcile_candidate_user_scope.py `
  tests/test_reconcile_chain_fold.py tests/test_reconcile_chain_merge.py `
  tests/test_reconcile_time_order.py tests/test_reconcile_hybrid_v2_align.py `
  tests/test_memory_graph.py tests/test_qdrant_batch_search.py -q -o log_cli=false
```

Result: **221 passed, 4 skipped** (unconfigured Neo4j), exit 0, 18.68 seconds.
The earlier run had the Python 3.13 metadata assertion failure; it was fixed
and is not hidden in the final count.

Real Python 3.9.12 ran `test_reader_selection.py` plus
`test_T18_python39_and_version_rejection` and
`test_T18_legacy_omitted_and_explicit_zero`: **7 passed**, exit 0. This used a
minimal environment installed with `uv pip`, not a successful full SDK-extra
sync. The actual import/native rejection script also passed.

## Acceptance mapping

All rows below have executed passing coverage. Coverage can span the native
suite and focused compatibility tests; it is not a claim that every possible
combination of failure modes was exercised against a live Server.

| ID | Passing evidence |
|---|---|
| T01 | 35 dense distractors; explicit dense top-30 excludes target; public keyword/hybrid include it; actual reconcile prompt includes it |
| T02 | Tags-only target in public results and actual reconcile prompt |
| T03 | Real scored `tags[]` TEXT plus `tags` KEYWORD under strict mode; exact `foo` excludes `foo bar`, text retrieves both |
| T04 | Agentic rewrite removes error code; keyword still uses original text, with an embedding stub that raises if invoked |
| T05 | User union, pre-limit exclusion, mixed-source rule, isolation/user intersection and include/exclude conflict |
| T06 | Historical match resolves a newer same-user head in another agent; distinct matched/display IDs |
| T07 | Real Server missing-head/cycle/multi-ancestor cases; reciprocal links, final-head time boundaries, scope and budget contracts |
| T08 | Raw unrounded score, exclusive equality, mismatched typed threshold rejection; real red/green mutation below |
| T09 | Content/tags update and clear; single and metadata-range deletion |
| T10 | Existing data indexed without upsert; repeated/interrupted prepare; missing schema and preserved incompatible index |
| T11 | Payloads/vectors and task set unchanged; dedup scheduling forbidden; strength writes enabled in config but bypassed |
| T12 | Necessary timeout/auth/error contracts, short embeddings, ID/nonfinite/incomplete response handling and cancellation; reconcile has no operations |
| T13 | Actual prompt follows backend order despite opposite timestamps |
| T14 | Healthy empty Server candidate pool allows existing ADD behavior |
| T15 | Real cosine 0 vs 1 in both ranking directions; writer stores main memory and projects only the true cosine neighbor |
| T16 | Optional cosine failure preserves stored main write and typed `causes` edge; semantic neighbors absent |
| T17 | Concurrent native/legacy actual Server public requests keep mode/user scope; no environment mutation; invalid reconcile configuration rejected |
| T18 | Legacy omitted defaults and explicit zero, actual Python 3.9 import/rejection, old reader execution with upgraded client; full installation gaps above remain open |

LLM and embeddings are deterministic stubs. Writer graph projection uses a
recording graph sink. Qdrant is real in every integration case; no Local or
mock Qdrant result is counted as Server certification.

## Failures found and corrected

- The native text probe showed WORD does not match `烤鸭` inside
  `用户喜欢北京烤鸭`; MULTILINGUAL does, for both content and tags. The native
  schema/config identity and tests now use MULTILINGUAL.
- Server schema normalizes `scoring=true` into `{type: bm25}`; checks accept
  that exact supported shape. A default cosine vector is checked before
  preparation/query and optional cosine retrieval.
- Qdrant returns canonical hyphenated UUIDs even for hex input IDs. Integrity
  checks now compare UUID values; initial false failures were not accepted.
- Empty/zero requests now skip before scene LLM/reader initialization. Directed
  hop budget is checked before fetching another frontier. Pipeline validation
  depends only on shared contracts. Standards/spec review findings were fixed;
  the spec reviewer found no remaining material gap on re-review.
- A first mutation attempt had an absent test container, so both outcomes were
  environment errors and were discarded. The Server was recreated at the same
  image digest. Valid mutation: replacing `>` with `>=` made T08 fail its actual
  equality assertion (exit 1); restoring `>` passed (exit 0). Source restored.
- A supplementary concurrency test first exposed an incomplete embedding stub;
  `embed_with_meta` was added to the fixture. One later run passed the assertion
  but failed writing pytest cache because the disk was full. The final repeat
  with available disk and cache disabled exited 0. It is the counted result.

## Full SDK suite: failed, baseline reproduced

`python -m pytest tests/ -q -o log_cli=false --tb=short` was run with complete
dev/faiss/qdrant/postgres extras on Python 3.13.13:

- Feature working tree: **1947 passed, 74 failed, 18 errors, 42 skipped,
  69 deselected**; exit 1, 405.44 seconds.
- Isolated archive of upstream `01ccf50`, same interpreter/dependencies:
  **1910 passed, 73 failed, 18 errors, 42 skipped, 45 deselected**; exit 1,
  280.46 seconds. Import path was explicitly verified to use the archive.
- 90 failed/error test cases overlap exactly. They include Windows database
  file locks, native FAISS file-handle exhaustion, encoding, signals and an
  existing LLM pool failure. The upstream-only failure is the Python 3.13
  reader class metadata assertion fixed in this branch.
- The two feature-only full-suite failures are Chroma re-embed HNSW
  `Nothing found on disk` cases. Both were subsequently reproduced on the
  unchanged archive: 512→512 in a focused three-case run, and 512→1024 on the
  third isolated repeat (first two passed). Their shared failure is not
  attributed to native retrieval or silently removed from the full result.
- Skips include absent PostgreSQL/Neo4j services and spaCy models, existing
  Chroma keyword exclusions, platform symlinks and an optional sparse test.
  No native integration test skipped for missing environment/version.

No unrelated backend/test-lifecycle overhaul is included to make this host's
entire suite green. CI was edited to run native without FastEmbed, explicitly
select real Server integration and then retain the full existing SDK suite.
Remote CI was not triggered or claimed to pass.

## Evidence, privacy, dependency and rollback

Raw local evidence is retained under `$env:TEMP`:
`hy-memory-native-final.{log,xml}`, `hy-memory-native-concurrent.xml`,
`hy-memory-native-contracts.{log,xml}`, `hy-memory-native-regression.{log,xml}`,
`hy-memory-native-full-sdk.{log,xml}`, `hy-memory-native-baseline-sdk.{log,xml}`
and `hy-memory-native-mutation.log`. Synthetic inputs only; no production
connection string, customer data or credential is included in this record.
Original material hashes were checked; no telemetry or external application
write was added. Ruff checks on new/affected retrieval paths, compileall and
`git diff --check` passed. Existing unrelated lint findings were not changed.

The only changed third-party version is Apache-2.0 `qdrant-client` 1.20.0;
its existing transitive dependency versions remain unchanged. Support facts
come from fixed official release/source evidence; execution results above
are separate from those documents.

Rollback selects `reader="legacy"`/`"hybrid"` and
`recall.reconcile_retriever="default"`. Existing search_text/sparse maintenance
continues. Rollback never automatically drops/rebuilds native indexes. No
rollback on a production deployment was performed.

Future PR wording remains **Related to #432**, retaining its dense-pool escape
goal through Qdrant-native capabilities with a Server-only boundary. This
record is local evidence, not maintainer approval or a merge/CI certification.
