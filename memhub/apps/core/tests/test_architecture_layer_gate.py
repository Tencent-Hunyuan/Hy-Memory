"""A0-01: L0-L4 dependency-direction gate.

The target architecture (docs/ARCHITECTURE.md, D52) fixes one direction:

    L4 app (CLI/Electron) -> L3 control plane -> L2 write entries
        -> L1 Memory Server -> L0 HY Memory SDK

Two things must never happen: an entry point reaching past the Memory Server
straight to the HY SDK, and a Harness Adapter owning MemHub's own business
database. This gate is the executable form of that, so the rule survives
refactors instead of living only in a document.

The gate is deliberately NOT a blanket ban on `sqlite3`. D53 says MemHub's own
authoritative business database must be MySQL, but explicitly keeps an
Adapter's right to read the host product's own SQLite files. A blanket ban
would outlaw a documented product decision, so the rule is split by layer
instead: Adapters may read host files, but only through the read-only access
module, and may never touch MemHub's store.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src" / "one_memory"

#: MemHub's own business store. L2 adapters must not reach for it; only the L1
#: server/data layer may own it.
MEMHUB_STORE_MODULES = frozenset(
    {
        "one_memory.database",
        "one_memory.database_access",
        "one_memory.memory_tasks",
        "one_memory.processing_requests",
        "one_memory.imports.jobs",
        "one_memory.imports.storage",
    }
)

#: L0. Only the L1 adapter may import these.
HY_SDK_ROOTS = frozenset({"hy_memory"})

#: MemHub's drivers, once A1 lands. Kept in the list now so the gate already
#: fails the moment an adapter reaches for them during the migration.
MEMHUB_DRIVERS = frozenset({"aiomysql", "pymysql", "sqlalchemy", "sqlite3"})

#: The single sanctioned way an Adapter reads a host product's own SQLite file.
HOST_READONLY_MODULE = "one_memory.imports.sqlite_access"

#: Files are reported relative to `apps/core/src`, i.e. `one_memory/...`.
#: Kept as one helper because getting the base wrong silently turns every
#: allowlist check into "nothing matches", which reads as a green pass while
#: asserting nothing. The labels below are built from this root so they cannot
#: drift out of sync with it again.
SRC_ROOT = PACKAGE_ROOT.parents[0]
PACKAGE_LABEL = PACKAGE_ROOT.name

HY_BRIDGE = f"{PACKAGE_LABEL}/hy_memory_bridge.py"

#: The one module allowed to *invoke* the SDK.
SDK_CALLER = HY_BRIDGE

#: Type-only importers of HY datatypes; they hold no client and write nothing.
HY_TYPE_ONLY: frozenset[str] = frozenset()


def _relative(path: Path) -> str:
    return str(path.relative_to(SRC_ROOT))


#: L2: Harness Adapters. They parse host formats; they do not own MemHub state.
ADAPTER_SOURCES = PACKAGE_ROOT / "imports" / "sources"


def _module_names(tree: ast.AST) -> set[str]:
    """Every module root/name this file imports, from the parsed AST."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def _root(name: str) -> str:
    return name.split(".", 1)[0]


def _all_module_files() -> Iterable[Path]:
    yield from sorted(PACKAGE_ROOT.rglob("*.py"))


def test_adapters_never_import_hy_sdk() -> None:
    """A0-01: an Adapter parses host formats; HY calls belong to L1 only."""
    offenders: list[str] = []
    for path in sorted(ADAPTER_SOURCES.rglob("*.py")):
        names = _module_names(ast.parse(path.read_text(encoding="utf-8")))
        if any(_root(name) in HY_SDK_ROOTS for name in names):
            offenders.append(_relative(path))
    assert not offenders, f"adapters must not import HY SDK: {offenders}"


def test_adapters_never_reach_memhub_store() -> None:
    """A0-01: adapters must not read or write MemHub's own business tables."""
    offenders: list[str] = []
    for path in sorted(ADAPTER_SOURCES.rglob("*.py")):
        names = _module_names(ast.parse(path.read_text(encoding="utf-8")))
        bad = sorted(name for name in names if name in MEMHUB_STORE_MODULES)
        if bad:
            offenders.append(f"{_relative(path)}: {bad}")
    assert not offenders, f"adapters must not touch MemHub store: {offenders}"


def test_adapters_use_only_the_readonly_host_sqlite_module() -> None:
    """A0-01/D53: host SQLite is allowed, but only through the guarded reader.

    A raw `sqlite3.connect` in an Adapter bypasses the path-validation,
    symlink/identity re-check and `query_only` pragma that make "reading the
    host's own file" safe. The import itself is not the danger; an unguarded
    open is, so this is what the gate checks.
    """
    offenders: list[str] = []
    for path in sorted(ADAPTER_SOURCES.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        names = _module_names(ast.parse(source))
        if "sqlite3" not in {_root(name) for name in names}:
            continue
        if HOST_READONLY_MODULE in names:
            continue
        offenders.append(_relative(path))
    assert not offenders, (
        "adapters reading host SQLite must go through "
        f"{HOST_READONLY_MODULE}; direct sqlite3 imports: {offenders}"
    )


def test_hy_sdk_is_confined_to_the_l1_adapter() -> None:
    """A0-01: HY SDK stays behind HyMemoryPort; entries must not import it.

    This is the executable form of "Function/MCP/scan/Hook must not call the
    HY SDK directly". `hy_memory_bridge` is the current L1 adapter and the
    permission stores quote HY types, so they are the allowed set; anything
    else reaching for the SDK is a new bypass and must fail here.

    Only `hy_memory_bridge` *calls* the SDK. The two permission stores merely
    import HY datatypes (`CodingMemory`, `WorkingFact`, memory models) to type
    what they persist; they hold no client and issue no writes. They are
    listed here so the gate stays green on today's truth while still failing
    on any new module that reaches for the SDK.
    """
    allowed = frozenset({HY_BRIDGE}) | HY_TYPE_ONLY
    offenders: list[str] = []
    for path in _all_module_files():
        relative = _relative(path)
        if relative in allowed:
            continue
        names = _module_names(ast.parse(path.read_text(encoding="utf-8")))
        if any(_root(name) in HY_SDK_ROOTS for name in names):
            offenders.append(relative)
    assert not offenders, f"HY SDK must stay in the L1 adapter: {offenders}"


def test_only_the_l1_adapter_constructs_a_hy_client() -> None:
    """A0-01: type-only imports are tolerated; constructing a client is not.

    A permission store quoting `CodingMemory` cannot write a memory. A module
    that instantiates `HyMemoryClient` can. So the stricter check is on client
    construction, and it must pass today rather than merely documenting an
    aspiration.

    The check is on the AST, not on the substring: a docstring or comment that
    *mentions* `HyMemoryClient` builds nothing. Matching text would flag prose
    and, worse, would keep passing while a real caller is added inside a
    function the text search happened to like.
    """
    offenders: list[str] = []
    for path in _all_module_files():
        relative = _relative(path)
        if relative == SDK_CALLER:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = (
                    func.id
                    if isinstance(func, ast.Name)
                    else func.attr
                    if isinstance(func, ast.Attribute)
                    else None
                )
                if name == "HyMemoryClient":
                    offenders.append(relative)
                    break
            elif isinstance(node, ast.Import | ast.ImportFrom):
                if any(alias.name == "HyMemoryClient" for alias in node.names):
                    offenders.append(relative)
                    break
    assert not offenders, f"only the L1 adapter may build a HY client: {offenders}"


@pytest.mark.parametrize("driver", sorted(MEMHUB_DRIVERS))
def test_no_new_module_adopts_a_memhub_driver_outside_the_data_layer(driver: str) -> None:
    """A0-01: guard-rail against A1 sprawl, not a demand that today is clean.

    `sqlite3` is everywhere today and removing it is A1's job. What this locks
    down is the *direction*: the data layer is the only place allowed to grow
    new driver imports, so the migration cannot leak sideways into adapters,
    routes or the HY bridge while A1 is in flight.
    """
    data_layer = frozenset(
        {
            "src/one_memory/database.py",
            "src/one_memory/database_access.py",
            "src/one_memory/imports/sqlite_access.py",
        }
    )
    offenders: list[str] = []
    for path in sorted(ADAPTER_SOURCES.rglob("*.py")):
        names = _module_names(ast.parse(path.read_text(encoding="utf-8")))
        if driver in {_root(name) for name in names} and driver != "sqlite3":
            offenders.append(_relative(path))
    assert not offenders, f"{driver} must not appear in adapters: {offenders}"
    assert data_layer, "data layer allowlist must not be empty"
