"""D56: prevent SQLite implementation details spreading outside the data layer.

A scan that reported every SQLite primitive as a failure would contradict the
selected engine. Pinning the remaining business-module uses means new ad-hoc
SQL is caught while the shared SQLite implementation can evolve.

The separation that matters is adapter vs own code — see the module docstring.
"""

from __future__ import annotations

import pytest

from one_memory.memory_server.sqlite_semantics import (
    ADAPTER_PATHS,
    EXEMPT_PATHS,
    Hit,
    count_by_construct,
    count_by_path,
    scan,
)

#: Ratchet baseline refreshed for the D56 release tree (2026-09-30), then
#: lowered by the prose-exclusion fix (2026-10-03).
#:
#: The 2026-09-26 number was measured while MySQL was still the selected
#: engine. D56 then adopted the already-reviewed C3/C4 recall code, whose
#: context coordinator/raw lanes legitimately contain SQLite SQL. Any later
#: growth in these or any other business module remains a gate failure.
#:
#: The scanner now counts code only: comments and docstrings that merely name a
#: construct are no longer hits (see `test_a_docstring_mention_is_not_a_hit`).
#: That removed 14 phantom hits and is why the baseline moved 157 -> 144. It is
#: a measurement correction, not a raised ceiling; the executed-SQL hits are
#: unchanged. The previous 157 included 14 lines of prose, e.g. a docstring in
#: `memory_governance.py` that says "one `BEGIN IMMEDIATE` ... `COMMIT`".
KNOWN_TOTAL = 144
KNOWN_BY_CONSTRUCT = {
    "pragma": 31,
    "insert_or": 11,
    "sqlite_json": 19,
    "rowid": 36,
    "begin_immediate": 12,
    "on_conflict": 36,
    "fts": 1,
}


def test_the_count_only_shrinks() -> None:
    hits = scan()
    assert len(hits) <= KNOWN_TOTAL, (
        f"SQLite-only SQL grew: {len(hits)} > {KNOWN_TOTAL}. "
        "New business code must use the shared SQLite data layer."
    )


def test_no_construct_grows() -> None:
    """Per construct too: one shrinks while another grows is still a leak."""
    current = count_by_construct(scan())
    unexpected = sorted(set(current) - set(KNOWN_BY_CONSTRUCT))
    assert not unexpected, f"new SQLite construct categories: {unexpected}"
    for name, allowed in KNOWN_BY_CONSTRUCT.items():
        assert current.get(name, 0) <= allowed, f"{name} grew past {allowed}"


def test_adapter_host_reads_are_not_counted() -> None:
    """Adapters may speak SQLite only to the host's own files.

    Counting these would conflate the host's database with One Memory's store.
    """
    paths = count_by_path(scan())
    offenders = sorted(set(paths) & set(ADAPTER_PATHS))
    assert not offenders, f"adapter host reads must not be counted: {offenders}"


def test_the_scanner_does_not_count_itself() -> None:
    """Its own patterns are SQLite by definition; they are not a leak."""
    paths = count_by_path(scan())
    for exempt in EXEMPT_PATHS:
        assert exempt not in paths


@pytest.mark.parametrize(
    ("sql", "construct"),
    [
        ("SELECT rowid FROM evidence", "rowid"),
        ("PRAGMA foreign_keys = ON", "pragma"),
        ("BEGIN IMMEDIATE", "begin_immediate"),
        ("INSERT OR IGNORE INTO t VALUES (1)", "insert_or"),
        ("INSERT INTO t VALUES (1) ON CONFLICT DO NOTHING", "on_conflict"),
        ("SELECT json_extract(body, '$.a') FROM t", "sqlite_json"),
        ("CREATE VIRTUAL TABLE s USING fts5(body)", "fts"),
        ("id INTEGER PRIMARY KEY AUTOINCREMENT", "autoincrement"),
    ],
)
def test_each_construct_is_detected(tmp_path, sql: str, construct: str) -> None:
    """The scanner must actually find each construct.

    A ratchet over a detector that misses things would report progress that
    did not happen, so each pattern is verified against a sample.
    """
    (tmp_path / "mod.py").write_text(sql + "\n", encoding="utf-8")
    hits = scan(tmp_path)
    assert any(h.construct == construct for h in hits), f"{construct} not detected"


def test_a_clean_file_produces_no_hits(tmp_path) -> None:
    (tmp_path / "mod.py").write_text(
        "SELECT evidence_id FROM evidence WHERE user_id = ?\n", encoding="utf-8"
    )
    assert scan(tmp_path) == []


def test_a_docstring_mention_is_not_a_hit(tmp_path) -> None:
    """Documentation is not a SQLite dependency.

    A docstring that says "every batch is one `BEGIN IMMEDIATE` ... `COMMIT`"
    describes behavior; it does not execute SQL. Counting it made a wording
    change look like a new leak and asked the owning module to rephrase a
    sentence to satisfy a gate.
    """
    (tmp_path / "mod.py").write_text(
        '"""A batch is one `BEGIN IMMEDIATE` ... `COMMIT`."""\nVALUE = 1\n',
        encoding="utf-8",
    )
    assert scan(tmp_path) == []


def test_a_comment_mention_is_not_a_hit(tmp_path) -> None:
    (tmp_path / "mod.py").write_text(
        "# The ledger is read with plain rowid ordering.\nVALUE = 1\n",
        encoding="utf-8",
    )
    assert scan(tmp_path) == []


def test_real_sql_next_to_a_docstring_is_still_a_hit(tmp_path) -> None:
    """The fix must not over-suppress: executed SQL still counts.

    A docstring mention and a real statement live in the same file here, so a
    naive "skip any line a docstring touched" fix would drop the real hit.
    """
    (tmp_path / "mod.py").write_text(
        '"""Mentions BEGIN IMMEDIATE in prose only."""\n'
        "def run(connection):\n"
        '    connection.execute("BEGIN IMMEDIATE")\n',
        encoding="utf-8",
    )
    hits = scan(tmp_path)
    assert [hit.construct for hit in hits] == ["begin_immediate"]
    assert hits[0].line == 3


def test_hits_carry_path_and_line() -> None:
    """Reported work must be actionable, not just a count."""
    hits = scan()
    assert hits
    for hit in hits[:5]:
        assert isinstance(hit, Hit)
        assert hit.line >= 1
        assert hit.path
