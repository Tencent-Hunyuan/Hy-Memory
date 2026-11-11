"""Reproduce local Chroma lexical recall and maintenance measurements.

The labeled corpus and all vectors are synthetic. Dense-missed targets are
deliberate mechanism controls, not evidence of a production embedding model's
quality. Dense and hybrid use the actual shared read pipeline; lexical reports
the backend's ranked candidates. No model service or user data is accessed.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import importlib.metadata
from itertools import islice
import json
import logging
import math
import os
from pathlib import Path
import platform
import random
import sqlite3
import statistics
import sys
import tempfile
import threading
import time
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from hy_memory.models.memory import MemoryNode


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FIXTURE = ROOT / "tests" / "fixtures" / "chroma_lexical_retrieval.json"
DIMS = 16
RESULT_K = 10
DENSE_BUDGET = 30
LEXICAL_BUDGET = 80
MIN_SCORE = 0.4
USER = "benchmark-user"
AGENT = "benchmark-agent"
SOURCE = "benchmark"
GROUPS = ("error_code", "identifier", "path", "version", "chinese", "natural")


class CountingCollection:
    """Benchmark-only wrapper counting payload transfer at the Chroma seam."""

    def __init__(self, collection: Any) -> None:
        self.collection = collection
        self.counts = defaultdict(int)
        self.lock = threading.Lock()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.collection, name)

    def snapshot(self) -> Dict[str, int]:
        with self.lock:
            return dict(self.counts)

    def _record(
        self, method: str, result: Mapping[str, Any], include: Optional[Sequence[str]],
    ) -> None:
        ids = result.get("ids") or []
        count = sum(map(len, ids)) if ids and isinstance(ids[0], list) else len(ids)
        with self.lock:
            self.counts[f"{method}_calls"] += 1
            self.counts[f"{method}_records"] += count
            if "embeddings" in (include or []):
                self.counts[f"{method}_embedding_records"] += count

    def get(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        result = self.collection.get(*args, **kwargs)
        self._record("get", result, kwargs.get("include"))
        return result

    def query(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        result = self.collection.query(*args, **kwargs)
        self._record("query", result, kwargs.get("include"))
        return result


class SyntheticEmbeddings:
    """No-network fallback for optional entity-tag embedding in the real reader."""

    async def embed_batch(self, texts: Sequence[str]) -> List[List[float]]:
        vectors = []
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            values = [(value - 127.5) / 127.5 for value in digest[:DIMS]]
            norm = math.sqrt(sum(value * value for value in values))
            vectors.append([value / norm for value in values])
        return vectors


def percentile(values: Sequence[float], quantile: float) -> Optional[float]:
    ordered = sorted(values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def latency_summary(values: Sequence[float]) -> Dict[str, Any]:
    return {
        "samples": len(values),
        "p50_ms": percentile(values, 0.5),
        "p95_ms": percentile(values, 0.95),
        "max_ms": max(values) if values else None,
    }


def vector(group: int, cosine: float, component: int = 0) -> List[float]:
    result = [0.0] * DIMS
    result[group] = cosine
    result[len(GROUPS) + component % (DIMS - len(GROUPS))] = math.sqrt(1 - cosine ** 2)
    return result


def query_vector(case: Mapping[str, Any]) -> List[float]:
    group = GROUPS.index(case["category"]) if case["category"] in GROUPS else 0
    result = [0.0] * DIMS
    result[group] = 1.0
    return result


def corpus(size: int, fixture: Mapping[str, Any], seed: int) -> Iterator[MemoryNode]:
    from hy_memory.models.memory import MemoryLayer, MemoryNode

    rng = random.Random(seed)
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    ordinal = 0
    for case in fixture["cases"]:
        group = GROUPS.index(case["category"])
        for kind, user, source in (
            ("target", USER, SOURCE),
            ("other-user", "other-user", SOURCE),
            ("excluded-source", USER, "excluded"),
        ):
            node = MemoryNode(
                node_id=f"{kind}-{case['id']}", user_id=user, agent_id=AGENT,
                session_id="benchmark-session", source=source,
                content=case["content"], tags=case.get("tags", []),
                layer=MemoryLayer.L2_FACT, gmt_created=timestamp,
            )
            node.embedding = vector(group, 0.995 if case["dense_supported"] else 0.02, ordinal)
            ordinal += 1
            yield node
    for number in range(size - ordinal):
        group = number % len(GROUPS)
        text = (
            f"Routine connection configuration note {number}: "
            "storage monitoring retry worker settings are reviewed by the service team."
        )
        if number % 100 == 0:
            text += " Long document control with routine configuration details." * 40
        node = MemoryNode(
            node_id=f"filler-{number}", user_id=USER if number % 5 else "other-user",
            agent_id=AGENT, session_id="benchmark-session",
            source=SOURCE if number % 10 else "excluded", content=text,
            layer=MemoryLayer.L2_FACT, gmt_created=timestamp,
        )
        node.embedding = vector(group, rng.uniform(0.7, 0.95), number)
        yield node


def batches(items: Iterable[Any], size: int) -> Iterator[List[Any]]:
    pending = []
    for item in items:
        pending.append(item)
        if len(pending) == size:
            yield pending
            pending = []
    if pending:
        yield pending


def evaluate(ids: Sequence[str], relevant: Sequence[str]) -> Dict[str, Any]:
    wanted = set(relevant)
    if not wanted:
        return {"negative_returned": len(ids), "negative_false_positive": bool(ids)}
    return {
        "recall_at_5": len(set(ids[:5]) & wanted) / len(wanted),
        "recall_at_10": len(set(ids[:10]) & wanted) / len(wanted),
        "reciprocal_rank_at_10": next(
            (1 / rank for rank, node_id in enumerate(ids[:10], 1) if node_id in wanted), 0.0,
        ),
        "nonrelevant_fraction_at_10": sum(node_id not in wanted for node_id in ids[:10]) / max(1, len(ids[:10])),
    }


def quality_summary(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    summaries = {}
    for dimension in ("all", "split", "category"):
        groups = defaultdict(list)
        for row in rows:
            groups["all" if dimension == "all" else row[dimension]].append(row)
        summaries[dimension] = {}
        for group, values in groups.items():
            metrics = {key for row in values for key in row["metrics"]}
            summaries[dimension][group] = {
                "queries": len(values),
                **{
                    metric: statistics.mean(
                        float(row["metrics"][metric]) for row in values if metric in row["metrics"]
                    )
                    for metric in sorted(metrics)
                },
            }
    return summaries


def count_delta(before: Mapping[str, int], after: Mapping[str, int]) -> Dict[str, int]:
    return {key: after.get(key, 0) - before.get(key, 0) for key in sorted(set(before) | set(after))}


async def measure_size(
    size: int, fixture: Mapping[str, Any], args: argparse.Namespace, directory: Path,
) -> Dict[str, Any]:
    from hy_memory.config import MemoryConfig
    from hy_memory.data.vector_store_chroma import ChromaVectorStore
    from hy_memory.models.memory import MemoryLayer, MemoryStatus
    from hy_memory.pipelines.base import ReadRequest
    from hy_memory.pipelines.reader_hybrid import HybridReadPipeline
    from hy_memory.pipelines.reader_legacy import LegacyReadPipeline

    config = MemoryConfig.from_dict({
        "vector_store": {
            "provider": "chroma", "collection_name": f"lexical_benchmark_{size}",
            "persist_directory": str(directory), "embedding_dims": DIMS,
            "chroma_lexical_enabled": True,
        },
        "enable_graph": False,
        "recall": {"dynamic_entity_tag_embed_enabled": False},
    })
    store = ChromaVectorStore(config)
    audit_store = None
    await store.initialize()
    try:
        batch_times = []
        loaded = 0
        started = time.perf_counter()
        for batch in batches(corpus(size, fixture, args.seed), args.batch_size):
            batch_start = time.perf_counter()
            written = await store.upsert_batch(batch)
            if len(written) != len(batch):
                raise RuntimeError("Benchmark corpus batch was not fully persisted")
            batch_times.append((time.perf_counter() - batch_start) * 1000)
            loaded += len(batch)
            if loaded % 10000 == 0 or loaded == size:
                print(json.dumps({"size": size, "phase": "load", "records": loaded}), flush=True)
        load_ms = (time.perf_counter() - started) * 1000
        counted = CountingCollection(store._collection)
        store._collection = counted
        readers = {
            "dense": LegacyReadPipeline(config, embed_service=SyntheticEmbeddings(), vector_store=store),
            "hybrid": HybridReadPipeline(config, embed_service=SyntheticEmbeddings(), vector_store=store),
        }
        for reader in readers.values():
            await reader.initialize()
        provenance: Dict[Tuple[str, str], Dict[str, str]] = {}

        async def retrieve(mode: str, case: Mapping[str, Any]) -> List[str]:
            if mode == "lexical":
                hits = await store.keyword_search(
                    query=case["query"], raw_query=case["query"], top_k=LEXICAL_BUDGET,
                    user_ids=[USER], agent_ids=[AGENT], include_sources=[SOURCE],
                    layers=[MemoryLayer.L2_FACT],
                    status_filter=[MemoryStatus.ACTIVE, MemoryStatus.SUPERSEDED],
                    only_latest=False,
                )
                ids = [hit["node_id"] for hit in hits[:RESULT_K]]
                provenance[(mode, case["id"])] = {node_id: "lexical_candidate" for node_id in ids}
                return ids
            response = await readers[mode].read(ReadRequest(
                query=case["query"], query_embedding=query_vector(case),
                user_ids=[USER], agent_ids=[AGENT], include_sources=[SOURCE],
                layers=[MemoryLayer.L2_FACT.value], limit=RESULT_K, min_score=MIN_SCORE,
                schema_limit=0, intention_limit=0, rerank=False,
            ))
            if not response.success:
                raise RuntimeError(f"{mode} read failed: {response.error_code}")
            provenance[(mode, case["id"])] = {
                memory["memory_id"]: memory.get("retrieval_source", "")
                for memory in response.memories
            }
            return [memory["memory_id"] for memory in response.memories]

        modes = {}
        labeled = fixture["cases"] + fixture["negative_queries"]
        workload = labeled + fixture.get("latency_queries", [])
        for mode in ("dense", "lexical", "hybrid"):
            before = counted.snapshot()
            start = time.perf_counter()
            await retrieve(mode, labeled[0])
            first_ms = (time.perf_counter() - start) * 1000
            rows = []
            for case in labeled:
                ids = await retrieve(mode, case)
                rows.append({
                    "id": case["id"], "category": case["category"], "split": case["split"],
                    "returned_ids": ids, "metrics": evaluate(ids, case["relevant_ids"]),
                    "retrieval_sources": provenance[(mode, case["id"])],
                })
            for repeat in range(args.warmup):
                await retrieve(mode, workload[repeat % len(workload)])
            durations = []
            by_workload = defaultdict(list)
            for repeat in range(args.repeats):
                case = workload[repeat % len(workload)]
                start = time.perf_counter()
                await retrieve(mode, case)
                elapsed = (time.perf_counter() - start) * 1000
                durations.append(elapsed)
                by_workload[case["category"]].append(elapsed)
            modes[mode] = {
                "first_call_ms": first_ms,
                "warm": latency_summary(durations),
                "warm_by_category": {key: latency_summary(values) for key, values in by_workload.items()},
                "quality": quality_summary(rows), "queries": rows,
                "chroma_transfer": count_delta(before, counted.snapshot()),
            }
            print(json.dumps({"size": size, "mode": mode, "warm_p95_ms": modes[mode]["warm"]["p95_ms"]}), flush=True)

        dense_by_query = {row["id"]: set(row["returned_ids"]) for row in modes["dense"]["queries"]}
        labels_by_query = {case["id"]: set(case["relevant_ids"]) for case in labeled}
        added_count = added_nonrelevant = keyword_only_count = keyword_only_nonrelevant = 0
        for row in modes["hybrid"]["queries"]:
            added = set(row["returned_ids"]) - dense_by_query[row["id"]]
            nonrelevant = added - labels_by_query[row["id"]]
            row["added_over_dense_ids"] = sorted(added)
            row["added_over_dense_nonrelevant_ids"] = sorted(nonrelevant)
            added_count += len(added)
            added_nonrelevant += len(nonrelevant)
            keyword_only = {
                node_id for node_id, source in row["retrieval_sources"].items()
                if source == "vdb_keyword_only"
            }
            row["keyword_only_ids"] = sorted(keyword_only)
            row["keyword_only_nonrelevant_ids"] = sorted(keyword_only - labels_by_query[row["id"]])
            keyword_only_count += len(keyword_only)
            keyword_only_nonrelevant += len(keyword_only - labels_by_query[row["id"]])
        modes["hybrid"]["added_over_dense"] = {
            "results": added_count, "nonrelevant_results": added_nonrelevant,
            "nonrelevant_fraction": added_nonrelevant / max(1, added_count),
        }
        modes["hybrid"]["keyword_only"] = {
            "results": keyword_only_count, "nonrelevant_results": keyword_only_nonrelevant,
            "nonrelevant_fraction": keyword_only_nonrelevant / max(1, keyword_only_count),
        }

        single_write_times = []
        for case in islice(corpus(size, fixture, args.seed), args.write_samples):
            start = time.perf_counter()
            await store.upsert(case)
            single_write_times.append((time.perf_counter() - start) * 1000)
        write_control = {"status": "not_run", "reason": "write controls run at sizes up to 10k"}
        if size <= 10000:
            control_config = MemoryConfig.from_dict({
                "vector_store": {
                    "provider": "chroma", "collection_name": f"lexical_write_control_{size}",
                    "persist_directory": str(directory), "embedding_dims": DIMS,
                    "chroma_lexical_enabled": False,
                },
            })
            control = ChromaVectorStore(control_config)
            await control.initialize()
            try:
                control_batch_times = []
                start = time.perf_counter()
                for batch in batches(corpus(size, fixture, args.seed), args.batch_size):
                    batch_start = time.perf_counter()
                    written = await control.upsert_batch(batch)
                    if len(written) != len(batch):
                        raise RuntimeError("Write control batch was not fully persisted")
                    control_batch_times.append((time.perf_counter() - batch_start) * 1000)
                control_load_ms = (time.perf_counter() - start) * 1000
                control_single_times = []
                for case in islice(corpus(size, fixture, args.seed), args.write_samples):
                    start = time.perf_counter()
                    await control.upsert(case)
                    control_single_times.append((time.perf_counter() - start) * 1000)
                write_control = {
                    "status": "measured", "lexical_enabled": False,
                    "records": size, "initial_load_ms": control_load_ms,
                    "initial_load_records_per_second": size / (control_load_ms / 1000),
                    "batch_write": latency_summary(control_batch_times),
                    "single_upsert": latency_summary(control_single_times),
                    "order": "enabled corpus and reads before disabled write control",
                }
                if single_write_times and control_single_times:
                    write_control["enabled_to_disabled_single_p95_ratio"] = (
                        percentile(single_write_times, 0.95) / percentile(control_single_times, 0.95)
                    )
            finally:
                await control.close()
        start = time.perf_counter()
        audit_store = ChromaVectorStore(config)
        await audit_store.initialize()
        audit_ms = (time.perf_counter() - start) * 1000
        await audit_store.close()
        audit_store = None
        start = time.perf_counter()
        rebuild_status = await store.rebuild_lexical_index()
        rebuild_ms = (time.perf_counter() - start) * 1000
        if rebuild_status.get("state") != "ready":
            raise RuntimeError(f"Lexical rebuild did not become ready: {rebuild_status.get('reason')}")
        projection_bytes = sum(
            path.stat().st_size for path in directory.rglob("*")
            if path.is_file() and "lexical" in "/".join(path.relative_to(directory).parts).lower()
        )
        return {
            "records": size, "modes": modes,
            "maintenance": {
                "initial_load_ms": load_ms, "initial_load_records_per_second": size / (load_ms / 1000),
                "batch_write": latency_summary(batch_times), "batch_size": args.batch_size,
                "single_upsert": latency_summary(single_write_times),
                "disabled_write_control": write_control,
                "unchanged_initialize_audit_ms": audit_ms, "forced_rebuild_ms": rebuild_ms,
                "projection_bytes": projection_bytes, "rebuild_status": rebuild_status,
            },
        }
    finally:
        if audit_store is not None:
            await audit_store.close()
        await store.close()


def benchmark_environment() -> Dict[str, str]:
    # Freeze the existing fusion settings; do not tune them to this fixture.
    settings = {
        "MEMORY_HYBRID_V2_VDB_WEIGHT_BM25": "0.35",
        "MEMORY_HYBRID_V2_VDB_WEIGHT_TAG": "0.3",
        "MEMORY_HYBRID_V2_KW_ONLY_SEM_FLOOR": "0.4",
        "MEMORY_HYBRID_V2_RRF_BLEND": "0.4",
        "MEMORY_HYBRID_V2_DIVERSITY": "1",
        "MEMORY_HYBRID_V2_FILL_UNDERFLOW": "0",
        "ANONYMIZED_TELEMETRY": "False",
    }
    for name in tuple(os.environ):
        if name.startswith("MEMORY_HYBRID_V2_"):
            del os.environ[name]
    os.environ.update(settings)
    return settings


async def run_benchmark(args: argparse.Namespace) -> Dict[str, Any]:
    settings = benchmark_environment()
    fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
    minimum_size = len(fixture["cases"]) * 3 + 1
    if any(size < minimum_size for size in args.sizes):
        raise ValueError(f"Each --sizes entry must be at least {minimum_size}")
    sqlite3.connect(":memory:").execute("CREATE VIRTUAL TABLE f USING fts5(t)")
    report: Dict[str, Any] = {
        "schema_version": 1, "recorded_at": datetime.now(timezone.utc).isoformat(),
        "scope": "synthetic mechanism benchmark; no production recall or embedding-model claim",
        "fixture_sha256": hashlib.sha256(args.fixture.read_bytes()).hexdigest(),
        "seed": args.seed, "platform": platform.platform(), "machine": platform.machine(),
        "processor": platform.processor(), "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
        "packages": {name: importlib.metadata.version(name) for name in ("chromadb", "filelock", "numpy")},
        "protocol": {
            "embedding": "deterministic synthetic controls, 16 dimensions; no network",
            "dense": "actual LegacyReadPipeline, shared post-processing",
            "lexical": "ranked keyword_search candidates without a reader score gate",
            "hybrid": "actual HybridReadPipeline, unchanged fusion",
            "dense_budget": DENSE_BUDGET, "lexical_budget": LEXICAL_BUDGET,
            "result_k": RESULT_K, "reader_min_score": MIN_SCORE,
            "warmup_per_mode": args.warmup, "latency_samples_per_mode": args.repeats,
            "first_call": "first invocation after load; not OS-cache-cold",
            "fusion_environment": settings,
            "contention_note": args.contention_note,
        },
        "sizes": [],
    }
    temporary = tempfile.TemporaryDirectory(prefix="hy-chroma-lexical-")
    try:
        for size in args.sizes:
            result = await measure_size(size, fixture, args, Path(temporary.name) / str(size))
            report["sizes"].append(result)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    finally:
        try:
            temporary.cleanup()
        except PermissionError:
            logging.warning("Chroma still holds a temporary benchmark file; cleanup can finish after process exit")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", nargs="+", type=int, default=[1000, 10000, 100000])
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--warmup", type=int, default=10)
    parser.add_argument("--repeats", type=int, default=100)
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--write-samples", type=int, default=20)
    parser.add_argument("--fixture", type=Path, default=FIXTURE)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--contention-note", default="", help="Concurrent workload or other measurement context to record")
    args = parser.parse_args()
    if args.warmup < 0 or args.repeats < 1 or args.write_samples < 0 or args.batch_size < 1:
        parser.error("warmup/write-samples must be nonnegative; repeats/batch-size must be positive")
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(run_benchmark(args))


if __name__ == "__main__":
    main()
