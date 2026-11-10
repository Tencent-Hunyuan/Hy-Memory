"""The frozen Core ships every driver a selectable storage backend needs.

PyInstaller cannot see the drivers: the SDK imports them inside a function,
only when `memhub settings backends` selects their backend. A driver missing
from the spec builds fine and fails the first time someone picks the backend
(a colleague had to drop `neo4j` and `qdrant_client` into `_internal` by hand).
These tests keep three lists in step: the backends MemHub offers
(`storage_backends`), the drivers pinned in `pyproject.toml`, and the
packages the spec collects. FAISS is the one supported-by-the-SDK backend
MemHub refuses, so its ~18 MB driver must not be shipped either.

`ONE_MEMORY_FROZEN_CORE=<path to one-memory>` additionally asks a built Core
which drivers it can import (`--storage-drivers`), for a release check.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import tomllib
from pathlib import Path

import pytest

from one_memory.storage_backend_probe import DRIVERS, driver_availability
from one_memory.storage_backends import FAISS_UNSUPPORTED, GRAPH_PROVIDERS, VECTOR_PROVIDERS

CORE = Path(__file__).resolve().parents[1]
SPEC = CORE / "one-memory.spec"
#: Backends MemHub refuses whatever the driver, and does not ship.
REFUSED = {"faiss": FAISS_UNSUPPORTED}
#: The import name each shipped driver is collected under, and its distribution.
DISTRIBUTIONS = {"neo4j": "neo4j", "qdrant_client": "qdrant-client", "chromadb": "chromadb"}


def _shipped_providers() -> list[str]:
    return [name for name in (*GRAPH_PROVIDERS, *VECTOR_PROVIDERS) if name not in REFUSED]


def _collected() -> list[str]:
    """Every package name the spec hands to `collect_all`, literal or looped."""
    tree = ast.parse(SPEC.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "collect_all"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            names.append(str(node.args[0].value))
        if isinstance(node, ast.For) and isinstance(node.iter, ast.Tuple):
            names.extend(
                str(item.value) for item in node.iter.elts if isinstance(item, ast.Constant)
            )
    return names


def _excluded() -> list[str]:
    match = re.search(r"excludes=\[([^\]]*)\]", SPEC.read_text(encoding="utf-8"))
    assert match is not None
    return [item.strip().strip("'\"") for item in match.group(1).split(",") if item.strip()]


def _pinned() -> dict[str, str]:
    project = tomllib.loads((CORE / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    pins: dict[str, str] = {}
    for requirement in project["dependencies"]:
        name, _, version = requirement.partition("==")
        pins[name.strip().lower()] = version.strip()
    return pins


def test_every_selectable_backend_has_its_driver_collected_and_pinned() -> None:
    collected = set(_collected())
    pins = _pinned()
    for provider in _shipped_providers():
        module = DRIVERS[provider]
        assert module in collected, f"{provider}: the spec does not collect {module}"
        # Kuzu arrives with the pinned SDK (its own exact pin); the rest are ours.
        distribution = DISTRIBUTIONS.get(module)
        if distribution is not None:
            assert pins.get(distribution), f"{provider}: {distribution} is not pinned exactly"


def test_qdrant_ships_its_lock_dependency_and_not_its_model_downloader() -> None:
    # qdrant-client imports portalocker for its local storage at import time.
    assert "portalocker" in _collected()
    # fastembed would download a BM25 model at runtime; the SDK runs without it.
    assert "fastembed" not in _pinned() and "fastembed" not in _collected()


def test_a_refused_backend_is_neither_pinned_nor_shipped() -> None:
    assert "faiss-cpu" not in _pinned()
    assert "faiss" not in _collected()
    assert "faiss" in _excluded()


def test_the_sdk_modules_ship_once_inside_the_archive() -> None:
    # `.py` copies of the SDK beside the frozen modules are never imported.
    text = SPEC.read_text(encoding="utf-8")
    assert "if not entry[0].endswith('.py')" in text


def test_this_core_can_import_every_shipped_driver() -> None:
    available = driver_availability(import_them=True)
    assert {provider: available[provider] for provider in _shipped_providers()} == {
        provider: True for provider in _shipped_providers()
    }


@pytest.mark.skipif(
    not os.environ.get("ONE_MEMORY_FROZEN_CORE"),
    reason="set ONE_MEMORY_FROZEN_CORE to a built one-memory binary",
)
def test_a_frozen_core_reports_every_shipped_driver() -> None:
    binary = Path(os.environ["ONE_MEMORY_FROZEN_CORE"])
    completed = subprocess.run(
        [str(binary), "--storage-drivers"],
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    available = json.loads(completed.stdout.strip().splitlines()[-1])
    for provider in _shipped_providers():
        assert available[provider] is True, provider
    assert available["faiss"] is False


@pytest.mark.skipif(
    not os.environ.get("ONE_MEMORY_FROZEN_CORE"),
    reason="set ONE_MEMORY_FROZEN_CORE to a built one-memory binary",
)
def test_a_frozen_core_loads_the_sdk_without_its_source_copies(tmp_path: Path) -> None:
    """The SDK's `.py` copies are no longer shipped; its frozen modules still load."""
    from one_memory.memory_server.config_state import ConfigStore
    from one_memory.storage_backends import StorageBackends, VectorBackend
    from one_memory.target_schema import bootstrap_fresh_management_schema

    binary = Path(os.environ["ONE_MEMORY_FROZEN_CORE"])
    assert not (binary.parent / "_internal" / "hy_memory").exists()
    assert bootstrap_fresh_management_schema(tmp_path / "one-memory.sqlite3")
    environment = {**os.environ, "ONE_MEMORY_DATA_DIR": str(tmp_path)}
    completed = subprocess.run(
        [str(binary), "sdk-migration-status", "--json"],
        capture_output=True,
        text=True,
        timeout=180,
        check=True,
        env=environment,
    )
    document = json.loads(completed.stdout.strip().splitlines()[-1])
    assert document["state"] in {"required", "not_required"}
    # And a custom-backend home is skipped by the frozen build too.
    ConfigStore(tmp_path).set_storage_backends(
        StorageBackends(vector=VectorBackend(provider="qdrant", host="v")).with_namespace()
    )
    completed = subprocess.run(
        [str(binary), "sdk-migration-status", "--json"],
        capture_output=True,
        text=True,
        timeout=180,
        check=True,
        env=environment,
    )
    document = json.loads(completed.stdout.strip().splitlines()[-1])
    assert document["skipped"] == "upgrade_migration_skipped_custom_backend"
