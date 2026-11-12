# Qdrant Server native retrieval

Native retrieval lets content and tags recall memories outside the dense
candidate pool, in public search and write-time reconcile. Text scoring and
fusion run entirely in Qdrant; the SDK only validates scope, resolves complete
evolution chains and formats results in backend order.

Related to [#432](https://github.com/Tencent-Hunyuan/Hy-Memory/pull/432). This
retains its recall goal and replaces the sidecar approach with Qdrant-native
capabilities. The new path supports **Qdrant Server only**. Local Qdrant,
Chroma, FAISS, MemHub integration and the separate Chroma vector-preservation
fix are outside this feature's scope.

## Install and prepare

Use Python >=3.10, `qdrant-client==1.20.0` and Qdrant Server `1.20.0`.
Other combinations fail with `reason="unsupported"`. Base SDK Python 3.9
support and the existing `qdrant`/`all` extras remain available. The unified
lock also upgrades those extras' client to 1.20.0 on Python >=3.10; Python 3.9
retains 1.16.1. FastEmbed is optional for this new path.

```sh
pip install 'hy-memory[qdrant-native]'
```

Configure the existing Qdrant remote connection and cosine collection:

```python
from hy_memory import HyMemoryClient, NativeScoreThreshold, NativeRetrievalError

client = HyMemoryClient.from_config({
    "vector_store": {
        "provider": "qdrant", "host": "localhost", "port": 6333,
        "collection_name": "memories", "embedding_dims": 1536,
    },
    "recall": {"reconcile_retriever": "native"},  # independent write opt-in
})
prepared = client.prepare_native_retrieval()
# Async: prepared = await client.async_prepare_native_retrieval()
```

Preparation builds scored TEXT indexes on `content` and `tags[]` (multilingual
tokenizer, lowercase, default BM25), plus a KEYWORD index on `tags`. It waits
for completed operations and checks schema and a read-only text/RRF probe.
Existing payloads are indexed without being re-upserted. It never drops an
incompatible index: `schema_conflict` identifies the conflicting field.
Interrupted preparation can be retried against the actual schema. Search
never creates indexes; missing indexes produce `not_ready`. A copied or
re-embedded collection needs the same explicit preparation.

## Search

```python
result = await client.async_search(
    "ECONNRESET src/network/reconnect.py",
    user_ids=["alice"], scene="chat", reader="native",
    native_mode="hybrid", limit=10,
)
# The synchronous equivalent is client.search(...).
```

Modes are `dense`, `keyword` and `hybrid`. Keyword uses the original final
query without embedding. Dense uses the semantic rewrite. Qdrant fuses
content/tags BM25 with native RRF, then fuses the lexical subtree with dense
for hybrid. `queries=[...]` remains context plus a final target, not a batch
search API. `HY_MEMORY_READER=native` selects the same reader by default;
an explicit reader wins without modifying the process environment.

Native defaults: no legacy `min_score`, `schema_limit=0`,
`intention_limit=0`, `rerank=False`. Explicit float `min_score`, positive
schema/intention limits and rerank are rejected. Omitted legacy arguments
keep their original defaults (0.4, 10, 0, True); explicit zero stays zero.

Public limit is 0..100; the candidate budget is `max(30, 3*limit)` (max 300).
Blank queries and zero limit skip retrieval after parameter/scope validation.
Native supports pro/ultra chat L2/L3/L4 only. Lite, L1_RAW, `scene="all"`,
working/coding (including automatic routing) fail before entering an old path.

Each result carries the **unrounded** backend `score`, `score_kind`
(`cosine` for dense, `rrf` otherwise), `mode`, `config_id`, `matched_id`
and representative `memory_id`. Scores are not normalized or reranked.
An optional exclusive raw-score threshold must match all three identifiers:

```python
threshold = NativeScoreThreshold(
    value=0.2, mode="hybrid", score_kind="rrf",
    config_id=prepared["config_id"],
)
result = client.search(
    "ECONNRESET", user_ids=["alice"], scene="chat", reader="native",
    native_mode="hybrid", native_threshold=threshold,
)
```

Only scores strictly **greater than** the threshold pass. Equal scores are
excluded. RRF scores are ranking evidence, not cosine or confidence.

User scope intersects agent/session/isolation conditions before every branch's
top-B. Source exclusion wins, including the existing mixed-source rule.
Evolution may reach a newer head in another agent/session of the same user,
but every node is checked for user/source/layer visibility. Complete components
must have a unique confirmed head, no directed cycle, at most 32 hops and 1024
unique nodes. Reciprocal references and multi-ancestor merges are supported.
Time filtering applies to the final representative. Missing required nodes,
short batches and query failures fail closed; healthy no-hit queries succeed.
Native public searches schedule no dedup, SHADOW or access-count writes.

## Reconcile and failures

`recall.reconcile_retriever="native"` is independent of read selection;
the default `"default"` keeps existing retrieval. Unknown values are rejected
both on config loading and before candidate preparation.

One native nested plan fuses up to 16 new memories and, when the existing
expansion switch is enabled, at most 4 extra queries. Branch/unified budget is
60; at most 20 resolved representatives enter the existing prompt in backend
order. Reconcile retains L2/L4 by default and performs no candidate-phase
storage dedup. Necessary failures return `success=False, ops=[]`; cancellation
propagates. A healthy empty candidate pool still permits the existing ADD path.

`candidate_scores` contains backend **cosine** evidence restricted to the
resolved candidate IDs. RRF never enters semantic-neighbor scoring. Failure of
only this optional evidence empties that input's semantic neighbors and emits
a diagnostic; normal writes and LLM typed relations remain available.

`NativeRetrievalError.reason` values: `unsupported`, `invalid_request`,
`not_ready`, `schema_conflict`, `backend`, `integrity`, `scope`, `cycle`, `budget`.
There is no automatic fallback, parallel lexical database or Python-side fusion.
Disabling the read/write opt-ins restores the existing paths; existing
`search_text`/sparse writes continue. Rollback does not drop/rebuild indexes.

## Validation

Use a dedicated test Server, never an application database. Fixtures create
unique collections and delete only their own collection. Missing URL,
unreachable Server or wrong version fails selected integration tests.

```sh
uv sync --locked --python 3.12 --extra dev --extra qdrant-native
docker run --rm -d --name hy-memory-native-test -p 127.0.0.1:16333:6333 qdrant/qdrant:v1.20.0
uv run --no-sync pytest tests/native_retrieval -o addopts= -m 'not integration' -q
HY_MEMORY_TEST_QDRANT_URL=http://127.0.0.1:16333 uv run --no-sync pytest tests/native_retrieval -o addopts= -m integration -q
```

PowerShell: set `$env:HY_MEMORY_TEST_QDRANT_URL='http://127.0.0.1:16333'`
before the integration command. CI separately tests native without FastEmbed,
then restores full extras for the existing SDK suite.

The new dependency is Apache-2.0 qdrant-client. Fixed capability sources:
[Server 1.20.0](https://github.com/qdrant/qdrant/releases/tag/v1.20.0),
[client 1.20.0](https://github.com/qdrant/qdrant-client/releases/tag/v1.20.0).
Version documentation describes available APIs; it does not certify this SDK.
