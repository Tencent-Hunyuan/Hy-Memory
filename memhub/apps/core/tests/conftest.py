import os
import pwd
from pathlib import Path

import pytest

from one_memory.database_access import main_database_stats
from tests.core_test_classification import classify_items

pytest_plugins = ("tests.core_test_reporting",)

REFUSES_NESTED = "refuses_nested_checkout"
RESETS_DATABASE_STATS = "resets_main_database_stats"
FRESH_SDK = "fresh_sdk"
RESERVED_PORTS = {8765, 18765}
PORT_KEYS = (
    "ONE_MEMORY_PORT",
    "ONE_MEMORY_CORE_PORT",
    "ONE_MEMORY_RUNTIME_PORT",
    "ONE_MEMORY_BROKER_PORT",
)


def _assert_isolated_test_environment() -> None:
    if os.environ.get("ONE_MEMORY_TEST_ISOLATED") != "1":
        raise pytest.UsageError("run tests through scripts/run-tests-isolated.sh")
    real_home = Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
    home = Path(os.environ.get("HOME", "")).resolve()
    data_dir = Path(os.environ.get("ONE_MEMORY_DATA_DIR", "")).resolve()
    if home == real_home:
        raise pytest.UsageError("test HOME is the real user directory")
    if data_dir == real_home or real_home in data_dir.parents:
        raise pytest.UsageError("test data directory is inside the real user directory")
    for key in PORT_KEYS:
        raw = os.environ.get(key)
        if raw is None or not raw.isdecimal() or int(raw) in RESERVED_PORTS:
            raise pytest.UsageError(f"{key} must be an explicit non-reserved test port")


def pytest_configure(config):
    _assert_isolated_test_environment()
    config.addinivalue_line(
        "markers", f"{REFUSES_NESTED}: the test makes a nested main-database checkout on purpose"
    )
    config.addinivalue_line(
        "markers", f"{FRESH_SDK}: an empty app home boots directly into SDK authority"
    )


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    classify_items(config, items)
    config.addinivalue_line(
        "markers",
        f"{RESETS_DATABASE_STATS}: the test reloads the database metrics module; serial only",
    )


def _nested() -> int:
    stats = main_database_stats()
    return int(stats.get("nested_connections", 0)) + int(stats.get("nested_refusals", 0))


@pytest.fixture(autouse=True)
def _explicit_app_schema_generation(request, monkeypatch):
    """Keep historical app tests legacy; fresh-schema tests opt in explicitly."""
    if request.node.get_closest_marker(FRESH_SDK) is not None:
        yield
        return
    from one_memory import api
    from one_memory.imports.storage import EvidenceStore

    bootstrap = api.bootstrap_fresh_management_schema

    def bootstrap_legacy_home(database_path: Path) -> bool:
        if not database_path.exists():
            EvidenceStore(database_path)
        return bootstrap(database_path)

    monkeypatch.setattr(
        api,
        "bootstrap_fresh_management_schema",
        bootstrap_legacy_home,
    )
    yield


@pytest.fixture(autouse=True)
def _no_nested_main_database_checkout(request):
    """A checkout inside another on the same thread is a caller that should pass its connection.

    Counted even when production code swallows the refusal, so a broad
    `except` cannot hide one.
    """
    before = _nested()
    yield
    if (
        request.node.get_closest_marker(REFUSES_NESTED) is None
        and request.node.get_closest_marker(RESETS_DATABASE_STATS) is None
    ):
        assert _nested() == before, "nested main-database checkout on one thread"
