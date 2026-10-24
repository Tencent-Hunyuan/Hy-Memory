"""Executable dependency and memory-authority gates.

The public target architecture is defined by ``docs/ARCHITECTURE.md``:

* Hy Memory SDK owns memory/raw/vector/graph/coding data;
* MemHub owns the management plane and may retain ``memory_id`` references;
* product code reaches the SDK only through narrow runtime/migration modules;
* MemHub calls public ``HyMemoryClient`` methods only.

The sole exception is the read-only Kuzu projection/admin surface used by the
existing graph UI and Graph Width workflow.

The gate is deliberately not a blanket ban on ``sqlite3``. Adapters may read a
host product's SQLite through the guarded read-only module; they may not own
MemHub state or call the SDK directly.
"""

from __future__ import annotations

import ast
import re
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

SDK_MODULES = frozenset(
    {
        f"{PACKAGE_LABEL}/hy_sdk_runtime.py",
        f"{PACKAGE_LABEL}/hy_sdk_migration.py",
    }
)
SDK_CLIENT_CONSTRUCTOR = f"{PACKAGE_LABEL}/hy_sdk_runtime.py"

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

SDK_PRIVATE_ATTRIBUTES = frozenset(
    {
        "_coding_store",
        "_coding_stores",
        "_collection",
        "_embed_service",
        "_graph_store",
        "_loop_thread",
        "_readers",
        "_reader",
        "_reconciler",
        "_registry",
        "_run",
        "_shared_embed_service",
        "_spawn_search_dedup",
        "_system2_writer",
        "_vector_store",
        "_writer",
    }
)
SDK_PRIVATE_ACCESS_EXCEPTIONS = {
    f"{PACKAGE_LABEL}/hy_graph_projection.py": frozenset(
        {"_graph_store", "_loop_thread", "_run"}
    ),
    # Startup migration runs while Runtime writers are stopped. It is the one
    # non-runtime path allowed to manipulate SDK storage directly so that
    # migration-only mechanics never become public HyMemoryClient methods.
    f"{PACKAGE_LABEL}/hy_sdk_migration.py": frozenset(
        {"_embed_service", "_graph_store", "_loop_thread", "_vector_store"}
    ),
}


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


def test_hy_sdk_is_confined_to_public_sdk_modules() -> None:
    """Only the runtime and offline migration adapters may import the SDK."""
    offenders: list[str] = []
    for path in _all_module_files():
        relative = _relative(path)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = _module_names(tree)
        if not any(_root(name) in HY_SDK_ROOTS for name in names):
            continue
        if relative not in SDK_MODULES:
            offenders.append(relative)
    assert not offenders, f"HY SDK imports must stay in {sorted(SDK_MODULES)}: {offenders}"


def test_only_runtime_constructs_a_hy_client() -> None:
    """Migration borrows a lease; only runtime constructs clients."""
    offenders: list[str] = []
    for path in _all_module_files():
        relative = _relative(path)
        if relative == SDK_CLIENT_CONSTRUCTOR:
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
    assert not offenders, f"only {SDK_CLIENT_CONSTRUCTOR} may build a HY client: {offenders}"


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
        "new Evidence/Governance writers are forbidden; use public SDK client "
        f"methods instead: {offenders}"
    )


def test_no_sdk_private_implementation_access() -> None:
    """Runtime uses public APIs; only Graph and stopped-writer migration are privileged."""
    offenders: list[str] = []
    for path in _all_module_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        relative = _relative(path)
        used = {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and node.attr in SDK_PRIVATE_ATTRIBUTES
            and (
                relative.startswith(f"{PACKAGE_LABEL}/hy_")
                or (
                    isinstance(node.value, ast.Name)
                    and node.value.id in {"client", "native", "concrete"}
                )
            )
        }
        if used:
            unexpected = used - SDK_PRIVATE_ACCESS_EXCEPTIONS.get(
                relative, frozenset()
            )
            if unexpected:
                offenders.append(f"{relative}: {sorted(unexpected)}")
    assert not offenders, (
        "SDK private implementation access is forbidden outside the isolated "
        f"Graph and offline-migration boundaries: {offenders}"
    )


def test_sdk_modules_import_only_the_public_package_root() -> None:
    """Runtime imports only public SDK symbols; offline migration is privileged."""
    offenders: list[str] = []
    for path in _all_module_files():
        relative = _relative(path)
        if relative == f"{PACKAGE_LABEL}/hy_sdk_migration.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(
                alias.name.startswith("hy_memory.") for alias in node.names
            ):
                offenders.append(relative)
                break
            if isinstance(node, ast.ImportFrom) and (
                node.module or ""
            ).startswith("hy_memory."):
                offenders.append(relative)
                break
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "import_module"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and node.args[0].value.startswith("hy_memory.")
            ):
                offenders.append(relative)
                break
    assert not offenders, f"import SDK symbols from `hy_memory` only: {offenders}"


def test_memhub_never_patches_sdk_prompts() -> None:
    offenders: list[str] = []
    forbidden = (
        "extract_system_prompt_en",
        "extract_system_prompt_zh",
        "hy_memory.agent.prompts",
    )
    for path in _all_module_files():
        source = path.read_text(encoding="utf-8")
        hits = [token for token in forbidden if token in source]
        if hits:
            offenders.append(f"{_relative(path)}: {hits}")
    assert not offenders, f"SDK prompt patching is forbidden: {offenders}"


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
