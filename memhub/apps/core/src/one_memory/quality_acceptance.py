from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from one_memory.memory_server.store_binding import store_for

Disposition = Literal["personal", "workspace", "noop"]


@dataclass(frozen=True)
class AcceptanceRow:
    row_id: str
    evidence_id: str
    source_id: str
    written_by: str
    character_count: int
    content_hash: str
    strata: tuple[str, ...]
    memory_ids: tuple[str, ...]
    memory_statuses: tuple[str, ...]
    memory_scopes: tuple[str, ...]
    verbatim: str | None = None


@dataclass(frozen=True)
class AcceptanceScore:
    sample_size: int
    labeled: int
    true_positive: int
    false_positive: int
    false_negative: int
    true_negative: int
    precision: float
    false_positive_rate: float
    omission_rate: float
    provenance_accuracy: float
    author_accuracy: float
    passed: bool
    failures: tuple[str, ...]


def build_acceptance_pack(
    database_path: Path,
    *,
    sample_size: int = 50,
    seed: str = "one-memory-personal-quality-v1",
    include_verbatim: bool = False,
) -> list[AcceptanceRow]:
    if not 1 <= sample_size <= 500:
        raise ValueError("sample_size must be between 1 and 500")
    with _read_only(database_path) as connection:
        evidence = {
            row["evidence_id"]: row
            for row in connection.execute(
                """
                SELECT evidence_id, source_id, role, written_by, verbatim, content_hash
                FROM evidence
                """
            )
        }
        memory_links: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for row in connection.execute(
            """
            SELECT ge.evidence_id, gm.memory_id, gm.status, gm.scopes_json
            FROM governed_memory_evidence ge
            JOIN governed_memories gm ON gm.memory_id = ge.memory_id
            WHERE ge.active = 1
            """
        ):
            memory_links[row["evidence_id"]].append(row)

        strata: dict[str, set[str]] = defaultdict(set)
        for evidence_id, links in memory_links.items():
            statuses = {row["status"] for row in links}
            bucket = "active_governed" if "confirmed" in statuses else "closed_governed"
            strata[bucket].add(evidence_id)

        for row in connection.execute(
            """
            SELECT DISTINCT e.evidence_id
            FROM evidence e
            WHERE e.role = 'user'
              AND LENGTH(e.verbatim) <= 300
              AND (
                e.verbatim LIKE '%我是一名%' OR e.verbatim LIKE '%我是一个%'
                OR e.verbatim LIKE '%我偏好%' OR e.verbatim LIKE '%我喜欢%'
                OR e.verbatim LIKE '%请记住%' OR e.verbatim LIKE '%以后给我%'
              )
            """
        ):
            strata["explicit_self_statement"].add(row["evidence_id"])

        for row in connection.execute(
            """
            SELECT DISTINCT mt.input_ref AS evidence_id
            FROM memory_tasks mt
            JOIN evidence e ON e.evidence_id = mt.input_ref
            WHERE mt.status = 'completed'
              AND NOT EXISTS (
                SELECT 1 FROM governed_memory_evidence ge
                JOIN governed_memories gm ON gm.memory_id = ge.memory_id
                WHERE ge.evidence_id = mt.input_ref
                  AND ge.active = 1
                  AND gm.status = 'confirmed'
              )
            """
        ):
            strata["completed_no_active_memory"].add(row["evidence_id"])

        for row in connection.execute(
            """
            SELECT evidence_id FROM evidence
            WHERE role != 'user'
            """
        ):
            strata["attribution_edge"].add(row["evidence_id"])

    selected: list[str] = []
    quotas = (
        ("active_governed", 10),
        ("closed_governed", 10),
        ("explicit_self_statement", 10),
        ("completed_no_active_memory", 15),
        ("attribution_edge", 5),
    )
    for name, quota in quotas:
        _extend_unique(
            selected,
            _stable_order(strata[name], seed=f"{seed}:{name}"),
            quota,
        )
    if len(selected) < sample_size:
        eligible = (evidence_id for evidence_id, row in evidence.items() if row["role"] == "user")
        _extend_unique(selected, _stable_order(eligible, seed=f"{seed}:fill"), sample_size)
    selected = selected[:sample_size]
    if len(selected) < sample_size:
        raise ValueError(f"only {len(selected)} eligible records for sample_size={sample_size}")

    rows: list[AcceptanceRow] = []
    for index, evidence_id in enumerate(selected, start=1):
        source = evidence[evidence_id]
        links = memory_links.get(evidence_id, [])
        scopes = {
            scope
            for link in links
            for scope in json.loads(link["scopes_json"])
            if isinstance(scope, str)
        }
        rows.append(
            AcceptanceRow(
                row_id=f"{index:03d}",
                evidence_id=evidence_id,
                source_id=source["source_id"],
                written_by=(
                    "person"
                    if source["role"] == "user"
                    else ("agent" if source["role"] == "assistant" else source["role"])
                ),
                character_count=len(source["verbatim"]),
                content_hash=source["content_hash"],
                strata=tuple(
                    name for name, evidence_ids in strata.items() if evidence_id in evidence_ids
                ),
                memory_ids=tuple(sorted(link["memory_id"] for link in links)),
                memory_statuses=tuple(sorted({link["status"] for link in links})),
                memory_scopes=tuple(sorted(scopes)),
                verbatim=source["verbatim"] if include_verbatim else None,
            )
        )
    return rows


def score_acceptance_pack(
    rows: list[AcceptanceRow],
    labels: dict[str, dict[str, object]],
    *,
    minimum_precision: float = 0.95,
    maximum_false_positive_rate: float = 0.05,
    maximum_omission_rate: float = 0.15,
) -> AcceptanceScore:
    missing = [row.evidence_id for row in rows if row.evidence_id not in labels]
    if missing:
        raise ValueError(f"{len(missing)} acceptance rows are unlabeled")

    tp = fp = fn = tn = 0
    provenance: list[bool] = []
    authors: list[bool] = []
    for row in rows:
        label = labels[row.evidence_id]
        disposition = label.get("disposition")
        if disposition not in {"personal", "workspace", "noop"}:
            raise ValueError(f"invalid disposition for {row.evidence_id}")
        predicted_personal = bool(
            "confirmed" in row.memory_statuses and "all-apps" in row.memory_scopes
        )
        expected_personal = disposition == "personal"
        if predicted_personal and expected_personal:
            tp += 1
        elif predicted_personal:
            fp += 1
        elif expected_personal:
            fn += 1
        else:
            tn += 1
        if predicted_personal:
            provenance.append(label.get("provenance_ok") is True)
            authors.append(row.written_by == "person" and label.get("author_ok") is True)

    precision = _ratio(tp, tp + fp, empty=1.0)
    false_positive_rate = _ratio(fp, fp + tn, empty=0.0)
    omission_rate = _ratio(fn, fn + tp, empty=0.0)
    provenance_accuracy = _ratio(sum(provenance), len(provenance), empty=1.0)
    author_accuracy = _ratio(sum(authors), len(authors), empty=1.0)
    failures: list[str] = []
    if precision < minimum_precision:
        failures.append("precision_below_threshold")
    if false_positive_rate > maximum_false_positive_rate:
        failures.append("false_positive_rate_above_threshold")
    if omission_rate > maximum_omission_rate:
        failures.append("omission_rate_above_threshold")
    if provenance_accuracy < 1:
        failures.append("provenance_not_perfect")
    if author_accuracy < 1:
        failures.append("author_attribution_not_perfect")
    return AcceptanceScore(
        sample_size=len(rows),
        labeled=len(rows),
        true_positive=tp,
        false_positive=fp,
        false_negative=fn,
        true_negative=tn,
        precision=precision,
        false_positive_rate=false_positive_rate,
        omission_rate=omission_rate,
        provenance_accuracy=provenance_accuracy,
        author_accuracy=author_accuracy,
        passed=not failures,
        failures=tuple(failures),
    )


def write_pack(path: Path, rows: list[AcceptanceRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(asdict(row), ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


def read_pack(path: Path) -> list[AcceptanceRow]:
    return [
        AcceptanceRow(**json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def read_labels(path: Path) -> dict[str, dict[str, object]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("labels must be a JSON object keyed by evidence_id")
    return {
        str(evidence_id): label for evidence_id, label in payload.items() if isinstance(label, dict)
    }


def _stable_order(evidence_ids: Iterable[str], *, seed: str) -> list[str]:
    return sorted(
        (str(value) for value in evidence_ids),
        key=lambda value: hashlib.sha256(f"{seed}:{value}".encode()).hexdigest(),
    )


def _extend_unique(target: list[str], candidates: list[str], limit: int) -> None:
    added = 0
    for candidate in candidates:
        if candidate in target:
            continue
        target.append(candidate)
        added += 1
        if added >= limit:
            return


def _ratio(numerator: int, denominator: int, *, empty: float) -> float:
    return numerator / denominator if denominator else empty


@contextmanager
def _read_only(database_path: Path) -> Iterator[sqlite3.Connection]:
    """A read checkout on whichever engine is in force.

    `row_factory` is sqlite3-only, so it is set only when the attribute exists
    rather than by branching on the engine: a third engine would not need a
    change here. MySQL rows are read by position by the callers already.
    """
    store: Any = store_for(database_path)
    with store.read(busy_timeout_ms=30_000) as connection:
        if hasattr(connection, "row_factory"):
            connection.row_factory = sqlite3.Row
        yield connection
