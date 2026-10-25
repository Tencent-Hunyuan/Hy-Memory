"""D56: keep SQLite-specific SQL inside known data-layer boundaries.

D56 made SQLite the only business database, so these constructs are no longer
migration defects by themselves. The scan remains useful as a boundary guard:
business modules should use the shared stores instead of growing new ad-hoc
SQLite access. The counting distinguishes three cases:

- **MemHub business-module SQL** — existing uses are ratcheted so they do not
  spread further.
- **The shared SQLite data layer** — owns WAL, integrity, backup and migration
  primitives and is therefore exempt from the business-module ratchet.
- **An Adapter reading the host's own SQLite** — allowed and expected (D53).
  Its SQL will always be SQLite; counting it would conflate host data with the
  One Memory business store.

So the scan skips adapter paths. Each reported hit carries the construct, so
the remaining work is a concrete list rather than a number.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from dataclasses import dataclass
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1]

#: Adapters may speak SQLite to the host's own files. Not MemHub's schema.
ADAPTER_PATHS = frozenset(
    {
        "imports/sources/cursor.py",
        "imports/sources/cursor_unified.py",
        "imports/sources/opencode.py",
        "imports/sources/openclaw.py",
        "imports/sources/hermes.py",
        "imports/sqlite_access.py",
        "imports/snapshots.py",
        "imports/sources/exported.py",
    }
)

#: Excluded from the count because they are not MemHub's runtime SQL.
#:
#: - `database.py` / `database_access.py` own SQLite runtime behavior.
#: - `migrations.py` holds the authoritative SQLite schema rendering.
#: - `migration_runner.py` owns the durable SQLite migration ledger and schema
#:   introspection.
#: - `durable_queue.py` / `memory_tasks.py` are the shared persistent queue
#:   stores; their atomic claims and JSON evidence references are data-layer SQL.
#: - `baseline_probe.py` deliberately inspects SQLite in read-only mode.
#: - this module holds the patterns themselves, which are of course SQLite.
#: - tests assert about SQLite, which is the point of them.
EXEMPT_PATHS = frozenset(
    {
        "database.py",
        "database_access.py",
        "memory_server/baseline_probe.py",
        "memory_server/durable_queue.py",
        "memory_server/migration_runner.py",
        "memory_server/migrations.py",
        "memory_server/sqlite_semantics.py",
        "memory_tasks.py",
        "sdk_data_plane_migration.py",
        "target_schema.py",
        # D93 A: the visibility cache is SQLite-only by construction. It is a
        # derived table with triggers on the six tables the read check reads —
        # all of them MemHub's own SQLite — so there is nothing here to push
        # down into a portable layer; the construct is the point. It is
        # unversioned on purpose (see the exemption in
        # `test_schema_ownership_gate`) and everything it writes can be
        # recomputed from the tables it mirrors.
        "memory_visibility.py",
    }
)

#: Known blockers: a hit that cannot be removed by rewriting the SQL alone,
#: because the construct is doing real work with no column to replace it.
#:
#: `matters/selection.py` orders a conversation's rows by SQLite's `rowid`,
#: which is insertion order. Neither `imported_at` (coarse, collides within a
#: bulk import) nor `evidence_id` (a hash, unordered) reproduces it — verified
#: by trying: replacing them broke 9 ordering tests. Removing this `rowid` needs
#: an explicit monotonic ordering column on `evidence` first, which is why
#: migration 34 adds `ingest_seq`.
KNOWN_BLOCKERS = frozenset({"matters/selection.py"})

#: Constructs MySQL does not have under the same name or semantics.
SQLITE_ONLY: dict[str, re.Pattern[str]] = {
    "rowid": re.compile(r"\browid\b", re.IGNORECASE),
    "autoincrement": re.compile(r"\bAUTOINCREMENT\b", re.IGNORECASE),
    "pragma": re.compile(r"\bPRAGMA\b", re.IGNORECASE),
    "begin_immediate": re.compile(r"\bBEGIN\s+IMMEDIATE\b", re.IGNORECASE),
    "sqlite_json": re.compile(r"\bjson_(?:extract|each|array|object)\s*\(", re.IGNORECASE),
    "fts": re.compile(r"\bUSING\s+FTS\d\b", re.IGNORECASE),
    "insert_or": re.compile(r"\bINSERT\s+OR\s+(?:IGNORE|REPLACE|ROLLBACK)\b", re.IGNORECASE),
    "on_conflict": re.compile(r"\bON\s+CONFLICT\b", re.IGNORECASE),
}


@dataclass(frozen=True)
class Hit:
    path: str
    construct: str
    line: int


def _prose_spans(source: str) -> list[tuple[int, int, int, int]]:
    """Character spans that are comments or docstrings, not executable SQL.

    The ratchet counts *code*, but a plain line regex also matches prose that
    merely names a construct. A docstring that says "one `BEGIN IMMEDIATE` ...
    `COMMIT`" is documentation, and counting it turns a wording change into a
    phantom leak that some other module is asked to "fix" by rephrasing a
    sentence. Comments have the same problem.

    Only comments and *docstrings* are excluded. A bare string literal that
    holds real SQL still counts, because that string is what gets executed.

    A file that cannot be tokenized or parsed falls back to an empty span set,
    which reproduces the old count-everything behavior rather than silently
    dropping hits.
    """
    spans: list[tuple[int, int, int, int]] = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.COMMENT:
                spans.append((token.start[0], token.start[1], token.end[0], token.end[1]))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        tree = None
    if tree is not None:
        doc_nodes = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        for node in ast.walk(tree):
            if not isinstance(node, doc_nodes):
                continue
            body = getattr(node, "body", [])
            if not body:
                continue
            first = body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                value = first.value
                spans.append(
                    (
                        value.lineno,
                        value.col_offset,
                        value.end_lineno or value.lineno,
                        value.end_col_offset or 0,
                    )
                )
    return spans


def _in_spans(spans: list[tuple[int, int, int, int]], row: int, column: int) -> bool:
    for start_row, start_column, end_row, end_column in spans:
        if (start_row, start_column) <= (row, column) < (end_row, end_column):
            return True
    return False


def scan(root: Path | None = None) -> list[Hit]:
    """Find SQLite-only constructs outside adapter paths."""
    base = root or SRC_ROOT
    found: list[Hit] = []
    for path in sorted(base.rglob("*.py")):
        try:
            relative = path.relative_to(base).as_posix()
        except ValueError:
            continue
        if relative in ADAPTER_PATHS:
            continue
        if relative in EXEMPT_PATHS:
            continue
        if relative.startswith("tests/") or relative.endswith("_test.py"):
            continue
        source = path.read_text(encoding="utf-8")
        prose = _prose_spans(source)
        for number, line in enumerate(source.splitlines(), start=1):
            for name, pattern in SQLITE_ONLY.items():
                for match in pattern.finditer(line):
                    if _in_spans(prose, number, match.start()):
                        continue
                    found.append(Hit(relative, name, number))
                    break
    return found


def count_by_construct(hits: list[Hit]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for hit in hits:
        counts[hit.construct] = counts.get(hit.construct, 0) + 1
    return counts


def count_by_path(hits: list[Hit]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for hit in hits:
        counts[hit.path] = counts.get(hit.path, 0) + 1
    return counts
