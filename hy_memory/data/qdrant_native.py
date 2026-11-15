"""Qdrant Server native text scoring and query trees (no local retrieval state)."""

import sys
from importlib.metadata import version
from typing import Any, Dict

from ..retrieval import NativeRetrievalError
from ..utils.source_filter import normalize_source_filters
from .vector_store_base import NativeQuery

VERSION = "1.20.0"


def check_version(client: Any, is_local: bool) -> None:
    if sys.version_info < (3, 10) or is_local or version("qdrant-client") != VERSION:
        raise NativeRetrievalError(
            "unsupported",
            "native retrieval requires Python >=3.10 and qdrant-client 1.20.0 with Server",
        )
    if client.info().version != VERSION:
        raise NativeRetrievalError(
            "unsupported", "native retrieval requires Qdrant Server 1.20.0"
        )


def schemas() -> Dict[str, Any]:
    from qdrant_client import models as m

    return {
        "content": m.TextIndexParams(
            type="text",
            tokenizer=m.TokenizerType.MULTILINGUAL,
            lowercase=True,
            scoring=True,
        ),
        "tags[]": m.TextIndexParams(
            type="text",
            tokenizer=m.TokenizerType.MULTILINGUAL,
            lowercase=True,
            scoring=True,
        ),
        "tags": m.PayloadSchemaType.KEYWORD,
    }


def check_schema(client: Any, collection: str, prepare: bool = False) -> None:
    expected = schemas()
    info = client.get_collection(collection)
    check_vectors(info)
    actual = info.payload_schema or {}
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
                and all(
                    value is None
                    for key, value in scoring.model_dump().items()
                    if key != "type"
                )
            )
            valid = (
                valid
                and params is not None
                and params.tokenizer == wanted.tokenizer
                and params.lowercase is not False
                and bm25
            )
            valid = (
                valid and params.min_token_len is None and params.max_token_len is None
            )
            # Non-default text analysis changes the score/query contract.
            if params is not None:
                for key in (
                    "stopwords",
                    "stemmer",
                    "phrase_matching",
                    "ascii_folding",
                    "nested",
                ):
                    valid = valid and not getattr(params, key, None)
        if not valid:
            raise NativeRetrievalError(
                "schema_conflict", f"incompatible native index: {field}"
            )
    if missing and not prepare:
        raise NativeRetrievalError(
            "not_ready",
            f"missing native indexes: {', '.join(missing)}; call prepare_native_retrieval()",
        )
    for field in missing:
        result = client.create_payload_index(
            collection, field, field_schema=expected[field], wait=True
        )
        if str(result.status) != "completed":
            raise NativeRetrievalError(
                "not_ready", f"native index operation incomplete: {field}"
            )
    if missing:
        check_schema(client, collection)


def check_vectors(info: Any) -> None:
    """The adapter's dense query uses the default (possibly named-empty) vector."""
    from qdrant_client import models as m

    vectors = info.config.params.vectors
    params = vectors.get("") if isinstance(vectors, dict) else vectors
    if params is None or params.distance != m.Distance.COSINE:
        raise NativeRetrievalError(
            "unsupported", "native retrieval requires a default cosine vector"
        )


def scope_filter(request: NativeQuery, seed: bool = True) -> Any:
    from qdrant_client import models as m

    include, exclude = normalize_source_filters(
        request.include_sources, request.exclude_sources
    )
    must = [
        m.FieldCondition(key="user_id", match=m.MatchAny(any=request.user_ids)),
        m.FieldCondition(
            key="layer", match=m.MatchAny(any=[layer.value for layer in request.layers])
        ),
        m.FieldCondition(key="status", match=m.MatchAny(any=["active", "superseded"])),
    ]
    for key, values in (
        ("agent_id", request.agent_ids),
        ("session_id", request.session_ids),
        ("isolation_key", request.isolation_keys),
    ):
        if seed and values:
            must.append(m.FieldCondition(key=key, match=m.MatchAny(any=values)))
    if include:
        must.append(m.FieldCondition(key="source", match=m.MatchAny(any=include)))
    return m.Filter(
        must=must,
        must_not=[m.FieldCondition(key="source", match=m.MatchAny(any=exclude))]
        if exclude
        else None,
    )


def lexical_tree(text: str, query_filter: Any, limit: int) -> Any:
    from qdrant_client import models as m

    return m.Prefetch(
        prefetch=[
            m.Prefetch(
                query=m.TextQuery(text=text),
                using=field,
                filter=query_filter,
                limit=limit,
            )
            for field in ("content", "tags[]")
        ],
        query=m.FusionQuery(fusion=m.Fusion.RRF),
        filter=query_filter,
        limit=limit,
    )


def query_tree(request: NativeQuery) -> Dict[str, Any]:
    from qdrant_client import models as m

    filt = scope_filter(request)
    branches = []
    for i, text in enumerate(request.texts):
        lex = (
            lexical_tree(text, filt, request.limit) if request.mode != "dense" else None
        )
        dense = (
            m.Prefetch(query=request.embeddings[i], filter=filt, limit=request.limit)
            if request.mode != "keyword"
            else None
        )
        if request.mode == "hybrid":
            branch = m.Prefetch(
                prefetch=[dense, lex],
                query=m.FusionQuery(fusion=m.Fusion.RRF),
                filter=filt,
                limit=request.limit,
            )
        else:
            branch = lex or dense
        branches.append(branch)
    if len(branches) == 1:
        branch = branches[0]
        return dict(
            query=branch.query,
            using=branch.using,
            prefetch=branch.prefetch,
            query_filter=filt,
            limit=request.limit,
        )
    return dict(
        prefetch=branches,
        query=m.FusionQuery(fusion=m.Fusion.RRF),
        query_filter=filt,
        limit=request.limit,
    )


def probe(client: Any, collection: str) -> None:

    # Always exercise text scoring and fusion, including on an empty collection.
    tree = lexical_tree("hy_memory_native_capability_probe", None, 1)
    client.query_points(
        collection,
        query=tree.query,
        prefetch=tree.prefetch,
        limit=1,
        with_payload=False,
    )
