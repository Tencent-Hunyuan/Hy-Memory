"""Correlated citation checks must look links up by memory, not scan by `active`."""

import re
import sqlite3
from pathlib import Path

import pytest

from one_memory import context_memory, home_summary, memory_library, source_read_access
from one_memory.imports.grants import GRANT_SCHEMA
from one_memory.imports.storage import EvidenceStore
from one_memory.memory_evidence_freshness import register_memory_freshness
from one_memory.memory_governance import MemoryGovernanceStore


def _source(module) -> str:
    return Path(module.__file__).read_text(encoding="utf-8")


def _predicates():
    home = re.search(r'readable = """(.*?)"""', _source(home_summary), re.S).group(1)
    library = _source(memory_library)
    library_clauses = re.findall(r'clauses\.append\("""(NOT EXISTS.*?)"""\)', library, re.S)
    return {
        "home": (home, ("local",)),
        "library": ("m.user_id = ? AND " + " AND ".join(library_clauses), ("local",)),
        "context": (context_memory._READABLE, ()),
        # Built rather than lifted out of the module's source: the policy is a
        # function now, so what has to be planned is what it returns.
        "source_read_access": (source_read_access._MEMORY_READABLE, ()),
    }


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "plan.sqlite3"
    EvidenceStore(path)
    MemoryGovernanceStore(path)
    with sqlite3.connect(path) as conn:
        conn.executescript(GRANT_SCHEMA)
    conn = sqlite3.connect(path)
    register_memory_freshness(conn)
    yield conn
    conn.close()


@pytest.mark.parametrize("name", ["home", "library", "context", "source_read_access"])
def test_citation_subqueries_use_the_memory_primary_key(db, name):
    predicate, args = _predicates()[name]
    statement = f"SELECT COUNT(*) FROM governed_memories m WHERE {predicate}"
    if name == "source_read_access":
        # This predicate is a bare row check, not a table filter: it takes the
        # memory's own ids, so it is planned on its own.
        statement = f"SELECT {predicate}"
        args = ("memory", 1, "local", "memory", "local")
    plan = " | ".join(row[3] for row in db.execute(f"EXPLAIN QUERY PLAN {statement}", args))
    assert "SEARCH link USING" in plan
    assert "governed_memory_evidence_active_evidence" not in plan, plan
    assert "(memory_id=?)" in plan, plan
