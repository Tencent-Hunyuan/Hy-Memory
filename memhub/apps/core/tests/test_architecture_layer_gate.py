"""Executable dependency and memory-authority gates.

The current target architecture is defined by ``HANDSOFF.md`` and
``docs/ARCHITECTURE.md``:

* Hy Memory SDK owns memory/raw/vector/graph/coding data;
* MemHub owns the management plane and may retain ``memory_id`` references;
* product code reaches the SDK through ``hy_memory_bridge`` only.

The legacy implementation still contains Evidence/Governance writers and a
small number of private SDK-store accesses.  Those are migration debt, not an
allowance for new code.  The explicit debt lists below may shrink during the
refactor; new entries must not be added.

The gate is deliberately not a blanket ban on ``sqlite3``. Adapters may read a
host product's SQLite through the guarded read-only module; they may not own
MemHub state or call the SDK directly.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path
import re

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

# Frozen migration debt: modules that still write the legacy memory/raw
# authority.  The target count is zero.  A new module cannot join this set.
LEGACY_AUTHORITY_WRITERS = frozenset(
    {
        f"{PACKAGE_LABEL}/imports/jobs.py",
        f"{PACKAGE_LABEL}/imports/storage.py",
        f"{PACKAGE_LABEL}/ingest_journal.py",
        f"{PACKAGE_LABEL}/memory_governance.py",
        f"{PACKAGE_LABEL}/memory_server/migrations.py",
    }
)

LEGACY_AUTHORITY_WRITE = re.compile(
    r"\b(?:INSERT\s+(?:OR\s+\w+\s+)?INTO|UPDATE|DELETE\s+FROM)\s+"
    r"(?:governed_[a-z_]+|evidence(?:_[a-z_]+)?)\b",
    re.IGNORECASE,
)

# Existing accesses to SDK implementation details.  These are exact file /
# attribute debts so deleting one is always green, while adding one fails.
SDK_PRIVATE_ACCESS_DEBT: dict[str, frozenset[str]] = {
    f"{PACKAGE_LABEL}/hy_lifecycle.py": frozenset({"_collection", "_vector_store"}),
    f"{PACKAGE_LABEL}/hy_memory_bridge.py": frozenset(
        {"_coding_store", "_graph_store", "_loop_thread", "_vector_store"}
    ),
    f"{PACKAGE_LABEL}/hy_response_guard.py": frozenset({"_loop_thread", "_registry"}),
}

SDK_PRIVATE_ATTRIBUTES = frozenset(
    attribute
    for attributes in SDK_PRIVATE_ACCESS_DEBT.values()
    for attribute in attributes
)


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


def test_no_new_module_writes_legacy_memory_authority() -> None:
    """Freeze Evidence/Governance writers while the SDK cutover removes them."""
    offenders: list[str] = []
    for path in _all_module_files():
        source = path.read_text(encoding="utf-8")
        if not LEGACY_AUTHORITY_WRITE.search(source):
            continue
        relative = _relative(path)
        if relative not in LEGACY_AUTHORITY_WRITERS:
            offenders.append(relative)
    assert not offenders, (
        "new Evidence/Governance writers are forbidden; write memory/raw via "
        f"the SDK bridge instead: {offenders}"
    )


def test_no_new_sdk_private_store_access() -> None:
    """Freeze direct SDK-store access until public client APIs replace it."""
    offenders: list[str] = []
    for path in _all_module_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        relative = _relative(path)
        names = _module_names(tree)
        if (
            relative not in SDK_PRIVATE_ACCESS_DEBT
            and not any(_root(name) in HY_SDK_ROOTS for name in names)
        ):
            # Names such as `_registry` and `_collection` are common for
            # MemHub-owned objects too.  They become SDK-private access only
            # in a module that imports the SDK (or is frozen debt already).
            continue
        used = {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr in SDK_PRIVATE_ATTRIBUTES
        }
        if not used:
            continue
        unexpected = sorted(used - SDK_PRIVATE_ACCESS_DEBT.get(relative, frozenset()))
        if unexpected:
            offenders.append(f"{relative}: {unexpected}")
    assert not offenders, (
        "new SDK private-store access is forbidden; add a public HyMemoryClient "
        f"API and call it through hy_memory_bridge: {offenders}"
    )


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
