"""Qdrant Server native text scoring and query trees (no local retrieval state)."""

import math
import sys
from importlib.metadata import version

from ..retrieval import NativeRetrievalError, NativeScoreThreshold
from ..utils.source_filter import normalize_source_filters

CONFIG_ID = "qdrant-native-v1-word-lowercase-rrf2"
VERSION = "1.20.0"


def validate_query(request):
    """Reject incomplete plans before issuing any RPC."""
    if sys.version_info < (3, 10):
        raise NativeRetrievalError("unsupported", "native retrieval requires Python >=3.10")
    if request.mode not in ("dense", "keyword", "hybrid"):
        raise NativeRetrievalError("invalid_request", "unknown native mode")
    if not request.user_ids or any(not isinstance(u, str) or not u.strip() for u in request.user_ids):
        raise NativeRetrievalError("invalid_request", "native retrieval requires nonempty user IDs")
    if not request.layers or any(layer.value not in ("l2_fact", "l3_summary", "l4_identity") for layer in request.layers):
        raise NativeRetrievalError("invalid_request", "native retrieval supports L2/L3/L4 only")
    if type(request.limit) is not int or not 1 <= request.limit <= 300:
        raise NativeRetrievalError("invalid_request", "native pool limit must be 1..300")
    if not request.texts or len(request.texts) > 20 or any(not isinstance(t, str) or not t.strip() for t in request.texts):
        raise NativeRetrievalError("invalid_request", "native queries must contain 1..20 nonblank texts")
    if request.mode != "keyword":
        if len(request.embeddings) != len(request.texts):
            raise NativeRetrievalError("integrity", "embedding count does not match query count")
        for emb in request.embeddings:
            if not emb or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in emb):
                raise NativeRetrievalError("invalid_request", "embeddings must be finite nonempty vectors")


def score_kind(mode):
    return "cosine" if mode == "dense" else "rrf"


def validate_threshold(threshold, mode):
    if threshold is None:
        return
    if not isinstance(threshold, NativeScoreThreshold) or (threshold.mode, threshold.score_kind, threshold.config_id) != (mode, score_kind(mode), CONFIG_ID):
        raise NativeRetrievalError("invalid_request", "native threshold mode, score kind or config mismatch")


def check_version(client, is_local):
    if sys.version_info < (3, 10) or is_local or version("qdrant-client") != VERSION:
        raise NativeRetrievalError("unsupported", "native retrieval requires Python >=3.10 and qdrant-client 1.20.0 with Server")
    if client.info().version != VERSION:
        raise NativeRetrievalError("unsupported", "native retrieval requires Qdrant Server 1.20.0")


def schemas():
    from qdrant_client import models as m
    return {
        "content": m.TextIndexParams(type="text", tokenizer=m.TokenizerType.WORD, lowercase=True, scoring=True),
        "tags[]": m.TextIndexParams(type="text", tokenizer=m.TokenizerType.WORD, lowercase=True, scoring=True),
        "tags": m.PayloadSchemaType.KEYWORD,
    }


def check_schema(client, collection, prepare=False):
    expected = schemas()
    actual = client.get_collection(collection).payload_schema or {}
    missing = []
    for field, wanted in expected.items():
        entry = actual.get(field)
        if entry is None:
            missing.append(field)
            continue
        valid = entry.data_type == ("keyword" if field == "tags" else "text")
        if field != "tags":
            params = entry.params
            scoring = getattr(params, "scoring", None)
            bm25 = scoring is True or (
                getattr(scoring, "type", None) == "bm25"
                and all(value is None for key, value in scoring.model_dump().items() if key != "type")
            )
            valid = valid and params is not None and params.tokenizer == wanted.tokenizer and params.lowercase is not False and bm25
            valid = valid and params.min_token_len is None and params.max_token_len is None
            # Non-default text analysis changes the score/query contract.
            if params is not None:
                for key in ("stopwords", "stemmer", "phrase_matching", "ascii_folding", "nested"):
                    valid = valid and not getattr(params, key, None)
        if not valid:
            raise NativeRetrievalError("schema_conflict", f"incompatible native index: {field}")
    if missing and not prepare:
        raise NativeRetrievalError("not_ready", f"missing native indexes: {', '.join(missing)}; call prepare_native_retrieval()")
    for field in missing:
        result = client.create_payload_index(collection, field, field_schema=expected[field], wait=True)
        if str(result.status) != "completed":
            raise NativeRetrievalError("not_ready", f"native index operation incomplete: {field}")
    if missing:
        check_schema(client, collection)


def scope_filter(request, seed=True):
    from qdrant_client import models as m
    include, exclude = normalize_source_filters(request.include_sources, request.exclude_sources)
    must = [
        m.FieldCondition(key="user_id", match=m.MatchAny(any=request.user_ids)),
        m.FieldCondition(key="layer", match=m.MatchAny(any=[layer.value for layer in request.layers])),
        m.FieldCondition(key="status", match=m.MatchAny(any=["active", "superseded"])),
    ]
    for key, values in (("agent_id", request.agent_ids), ("session_id", request.session_ids), ("isolation_key", request.isolation_keys)):
        if seed and values:
            must.append(m.FieldCondition(key=key, match=m.MatchAny(any=values)))
    if include:
        must.append(m.FieldCondition(key="source", match=m.MatchAny(any=include)))
    return m.Filter(must=must, must_not=[m.FieldCondition(key="source", match=m.MatchAny(any=exclude))] if exclude else None)


def lexical_tree(text, query_filter, limit):
    from qdrant_client import models as m
    return m.Prefetch(
        prefetch=[m.Prefetch(query=m.TextQuery(text=text), using=field, filter=query_filter, limit=limit) for field in ("content", "tags[]")],
        query=m.FusionQuery(fusion=m.Fusion.RRF), filter=query_filter, limit=limit,
    )


def query_tree(request):
    from qdrant_client import models as m
    filt = scope_filter(request)
    branches = []
    for i, text in enumerate(request.texts):
        lex = lexical_tree(text, filt, request.limit) if request.mode != "dense" else None
        dense = m.Prefetch(query=request.embeddings[i], filter=filt, limit=request.limit) if request.mode != "keyword" else None
        if request.mode == "hybrid":
            branch = m.Prefetch(prefetch=[dense, lex], query=m.FusionQuery(fusion=m.Fusion.RRF), filter=filt, limit=request.limit)
        else:
            branch = lex or dense
        branches.append(branch)
    if len(branches) == 1:
        branch = branches[0]
        return dict(query=branch.query, using=branch.using, prefetch=branch.prefetch, query_filter=filt, limit=request.limit)
    return dict(prefetch=branches, query=m.FusionQuery(fusion=m.Fusion.RRF), query_filter=filt, limit=request.limit)


def probe(client, collection):
    from qdrant_client import models as m
    # Always exercise text scoring and fusion, including on an empty collection.
    tree = lexical_tree("hy_memory_native_capability_probe", None, 1)
    client.query_points(collection, query=tree.query, prefetch=tree.prefetch, limit=1, with_payload=False)
