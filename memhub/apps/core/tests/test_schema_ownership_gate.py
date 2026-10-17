"""A1-03/A1-04: schema ownership, and the hard part of telling DDL from reads.

A1-03 says business modules must not create or alter their own tables at
startup. Today 32 Core modules do exactly that, so a blanket ban would fail
immediately and uselessly. What this gate does instead is draw the line the
architecture actually draws:

- **MemHub's own schema** must be declared in `memory_server.migrations` only.
- **An Adapter reading the host's own SQLite** is allowed and must keep working
  (D53). Its SQL is `SELECT` against tables the *host* owns.

The distinction that matters is therefore not "does this file mention SQL" but
"does this file define tables MemHub owns". `CREATE TABLE` is the signal for
the latter; `SELECT` and `PRAGMA query_only` are the adapter's own business.

This gate is deliberately written as a *ratchet*: it pins the current set of
offending files so the number can only go down. It does not pretend today is
clean, which would be a lie that also fails the moment anyone adds a file.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SRC_ROOT = Path(__file__).resolve().parents[1] / "src" / "one_memory"

#: Adapters may open the host's own SQLite. Their SQL is not MemHub's schema.
ADAPTER_PATHS = (
    "imports/sources/cursor.py",
    "imports/sources/cursor_unified.py",
    "imports/sources/opencode.py",
    "imports/sources/openclaw.py",
    "imports/sources/hermes.py",
    "imports/sqlite_access.py",
    "imports/snapshots.py",
)

#: The one place allowed to declare MemHub's schema.
#:
#: `migration_runner.py` is listed because it applies migrations, but it holds
#: no DDL of its own — which is the point: the runner runs statements, it does
#: not define them.
MIGRATION_PATHS = (
    "memory_server/migrations.py",
    "memory_server/migration_runner.py",
)

#: Matches an actual CREATE TABLE statement. Deliberately not a bare
#: `CREATE TABLE` substring search: a module that *selects* a statement out of
#: the shared migration (by matching on that text) would trip the bare form
#: while owning no schema at all — a false positive that would push people to
#: copy the DDL back inline, which is the thing this gate exists to prevent.
#: A real definition is `CREATE TABLE [IF NOT EXISTS] <name> (`. Requiring the
#: name-and-paren means a module that merely *selects* a statement out of the
#: shared migration by matching on its text is not counted as owning it.
CREATE_TABLE_RE = re.compile(
    r"\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\w`\[\]]+\s*\(",
    re.IGNORECASE,
)


def _relative(path: Path) -> str:
    return path.relative_to(SRC_ROOT).as_posix()


def _files_creating_tables() -> list[str]:
    found: list[str] = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        relative = _relative(path)
        if relative in MIGRATION_PATHS:
            continue
        if CREATE_TABLE_RE.search(path.read_text(encoding="utf-8")):
            found.append(relative)
    return found


#: Ratchet: the set measured on 2026-09-26. Only shrink, never grow.
KNOWN_INLINE_DDL = frozenset(
    {
        # Deliberate exception, not an oversight: `call_event_store` compares
        # `sqlite_master.sql` against its own DDL text to detect tampering, so
        # that text is part of an integrity check rather than just a create
        # statement. Routing it through the migration would reformat it and make
        # every existing database look corrupt. The canonical copy also lives in
        # migration 29, and `test_call_event_ddl_matches_the_store_that_compares_it`
        # pins the two together.
        "call_event_store.py",
        # Deliberate exception, not an oversight (D93 A). These two tables are
        # a derived cache of a decision the read path already makes, and they
        # must NOT go through a migration: `schema_gate` derives the build's
        # version as the highest migration version, so any migration raises
        # it, and a library written by this build would then refuse to open on
        # an older package. Keeping the derivation unversioned is what leaves
        # a way back. The tables are created idempotently, hold no data that
        # cannot be recomputed, and deleting both returns the library to
        # today's behaviour — which `test_starting_the_app_creates_the_derived_tables`
        # and the visibility tests pin.
        "memory_visibility.py",
    }
)


def test_no_new_module_may_create_tables() -> None:
    """The ratchet: today's offenders are listed, but no new one may appear.

    Reporting the known set as a failure would be theatre - it would fail
    forever and be ignored. Pinning it means any *new* violation is caught
    immediately, and each removal shrinks the list.
    """
    current = set(_files_creating_tables())
    new_offenders = sorted(current - KNOWN_INLINE_DDL)
    assert not new_offenders, (
        "new modules create tables outside memory_server.migrations: "
        f"{new_offenders}. Move the DDL into a migration instead."
    )


def test_the_known_set_only_shrinks() -> None:
    """Documents the direction of travel, and fails loudly when it reverses."""
    current = set(_files_creating_tables())
    assert current <= KNOWN_INLINE_DDL, "a module outside the recorded set now creates tables"


def test_adapter_host_reads_are_not_counted_as_schema() -> None:
    """D53 keeps an Adapter's right to read the host's own SQLite.

    If these were counted as MemHub schema, the gate would demand the host's
    tables be migrated into MemHub's database - which is exactly what D53
    forbids.
    """
    offenders = sorted(set(_files_creating_tables()) & set(ADAPTER_PATHS))
    assert not offenders, f"adapters must not define MemHub schema: {offenders}"


@pytest.mark.parametrize("relative", sorted(MIGRATION_PATHS))
def test_migrations_may_declare_schema(relative: str) -> None:
    """The one allowed place, so the exemption is explicit not accidental."""
    path = SRC_ROOT / relative
    assert path.exists()
    if relative == "memory_server/migration_runner.py":
        # Exempt from the *ratchet*, but it must not gain DDL of its own: the
        # runner applies statements, it does not define them.
        assert not CREATE_TABLE_RE.search(path.read_text(encoding="utf-8"))
    else:
        assert CREATE_TABLE_RE.search(path.read_text(encoding="utf-8"))
