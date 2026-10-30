"""Nothing in the Core opens the main database except through its access layer.

Every `sqlite3.connect` in the source is found by parsing it, and each must be
one the list below names, with the reason it is not the main database. A new
one fails until someone decides which it is.
"""

import ast
import asyncio
import json
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

import one_memory
from one_memory.api import create_app
from one_memory.config import Settings
from one_memory.database_access import close_main_databases
from one_memory.imports.sources.registry import build_source_catalog

SOURCE = Path(one_memory.__file__).parent


def test_the_database_file_keeps_sqlite_portable_defaults(tmp_path) -> None:
    """Cross-platform compatibility: do not customise the on-disk format.

    SQLite files are portable across OS/CPU by default. A non-default
    `page_size`, a non-UTF-8 `encoding` or `auto_vacuum` would make the file
    readable only by a build that set the same values, which breaks moving a
    data directory between platforms. Only `journal_mode` is switched (to
    WAL), and that does not change the file format.
    """
    from one_memory.database_access import main_database

    path = tmp_path / "one-memory.sqlite3"
    with main_database(path).write(autocommit=True) as connection, connection:
        connection.execute("CREATE TABLE probe (id INTEGER)")

    with main_database(path).read() as connection:
        page_size = connection.execute("PRAGMA page_size").fetchone()[0]
        encoding = connection.execute("PRAGMA encoding").fetchone()[0]
        auto_vacuum = connection.execute("PRAGMA auto_vacuum").fetchone()[0]

    assert path.read_bytes()[:16] == b"SQLite format 3\x00"
    assert page_size == 4096
    assert encoding == "UTF-8"
    assert auto_vacuum == 0


@dataclass(frozen=True)
class Allowed:
    reason: str
    # How the file is opened, read off the call itself: the mode this place
    # promises, so a read-only opener turned writable fails here.
    opened_with: str | tuple[str, ...] | None = None
    calls: int = 1


ALLOWED = {
    ("database_access.py", "MainDatabase._open_writer"): Allowed(
        "the access layer's write connection"
    ),
    ("database_access.py", "MainDatabase._open_reader"): Allowed(
        "the access layer's read pool", opened_with="?mode=ro"
    ),
    ("database_access.py", "MainDatabase.back_up"): Allowed(
        "the owner-only upgrade backup copy the backup API writes into, not the main database"
    ),
    ("database_access.py", "MainDatabase.restore_from"): Allowed(
        "the verified backup source and private staged restore copy, "
        "not an independent store; the source is read through SQLite's "
        "normal WAL-aware read-only view",
        calls=2,
    ),
    ("sdk_migration_log.py", "migration_rows"): Allowed(
        "a read-only snapshot of migration attempts for the migration log, taken "
        "before the access layer exists (Core refusing a legacy home, sdk-migrate)",
        opened_with="?mode=ro",
    ),
    ("library_upgrade.py", "_backup_problem"): Allowed(
        "the finished upgrade backup, checked read-only before migration relies on it",
        opened_with="?mode=ro&immutable=1",
    ),
    ("database_access.py", "_default_settings"): Allowed(
        "SQLite's own defaults, read once from a connection that opens no file",
        opened_with='":memory:"',
    ),
    ("sdk_migration_verify.py", "legacy_memory_ids_from_bundle"): Allowed(
        "the pre-cutover bundle's copy of the legacy database, read-only, to list "
        "the memory ids a migrated home must still read back",
        opened_with="?mode=ro",
    ),
    ("hy_sdk_migration.py", "_legacy_pools"): Allowed(
        "each pre-cutover bundle's copy of the legacy database, read-only, to "
        "recover the source and times historical cutovers dropped",
        opened_with="?mode=ro",
    ),
    ("hy_sdk_migration.py", "_repair_coding"): Allowed(
        "the SDK's own Coding/Working stores, not the main database: the one-time "
        "metadata repair writes them at startup, before the SDK client opens"
    ),
    ("sdk_vector_reattach.py", "_stored_names"): Allowed(
        "Chroma's own chroma.sqlite3, read-only, to list collection names before "
        "the offline migration opens Chroma",
        opened_with="?mode=ro",
    ),
    ("imports/sqlite_access.py", "open_sqlite_readonly"): Allowed(
        "a host app's own SQLite (Cursor state.vscdb / store.db): one direct "
        "read-only open when there is no WAL, and one read-only open of the "
        "private main+WAL mirror when the host has a live WAL",
        opened_with=("mirror", "?mode=ro"),
        calls=2,
    ),
    ("memory_server/baseline_probe.py", "open_readonly"): Allowed(
        "the A0-04 baseline probe: counts and timings read from the user's database "
        "before migration, read-only and never written to",
        opened_with="?mode=ro",
    ),
    ("imports/snapshots.py", "_backup_sqlite"): Allowed(
        "the private snapshot copy the backup API writes into, not the main database",
        opened_with="?mode=rw",
    ),
    ("imports/snapshots.py", "_open_closed_wal_snapshot_source"): Allowed(
        "a private staged copy of a host app's SQLite, verified by hash, immutable and read-only",
        opened_with="?mode=ro&immutable=1",
    ),
    ("imports/snapshots.py", "_open_sqlite_snapshot_source"): Allowed(
        "the host app's SQLite being snapshotted, read-only with readonly_shm",
        opened_with="?mode=ro&readonly_shm=1",
    ),
}

# Where SQLite's opener lives: the module, its implementation, and the
# module's own name for it.
_SQLITE_MODULES = {"sqlite3", "sqlite3.dbapi2", "_sqlite3"}
_OPENERS = {"connect", "Connection"}


@dataclass(frozen=True)
class Opening:
    scope: str
    line: int
    source: str


class _Scanner(ast.NodeVisitor):
    """Every reach for SQLite's opener, called or not.

    Holding `sqlite3.connect` in a variable, a partial or a getattr opens a
    file as surely as calling it, so any reference counts. `Connection` may
    only be named as a type; the module itself may not be handed around.
    """

    def __init__(self, text: str) -> None:
        self.text = text
        self.modules: set[str] = set()
        self.openers: set[str] = set()
        self.found: list[Opening] = []
        self._scope: list[str] = []
        self._annotations: set[int] = set()

    def _note(self, node: ast.AST) -> None:
        self.found.append(
            Opening(
                ".".join(self._scope) or "<module>",
                node.lineno,
                ast.get_source_segment(self.text, node) or "",
            )
        )

    def _enter(self, node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        if not isinstance(node, ast.ClassDef):
            for arg in [*node.args.args, *node.args.kwonlyargs, *node.args.posonlyargs]:
                if arg.annotation is not None:
                    self._mark_annotation(arg.annotation)
            for arg in (node.args.vararg, node.args.kwarg):
                if arg is not None and arg.annotation is not None:
                    self._mark_annotation(arg.annotation)
            if node.returns is not None:
                self._mark_annotation(node.returns)
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    visit_ClassDef = visit_FunctionDef = visit_AsyncFunctionDef = _enter

    def _mark_annotation(self, node: ast.AST) -> None:
        self._annotations.update(id(child) for child in ast.walk(node))

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._mark_annotation(node.annotation)
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name in _SQLITE_MODULES:
                self.modules.add(alias.asname or alias.name.split(".")[0])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            name = alias.asname or alias.name
            if node.module in _SQLITE_MODULES and alias.name in _OPENERS:
                self.openers.add(name)
                if alias.name == "connect":
                    self._note(node)
            elif alias.name in {"sqlite3", "dbapi2", "_sqlite3"}:
                self.modules.add(name)

    def _is_module(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            return node.id in self.modules
        return (
            isinstance(node, ast.Attribute)
            and node.attr == "dbapi2"
            and self._is_module(node.value)
        )

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if self._is_module(node.value):
            if node.attr == "connect" or (
                node.attr == "Connection" and id(node) not in self._annotations
            ):
                self._note(node)
            return
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if id(node) in self._annotations:
            return
        if node.id in self.openers or node.id in self.modules:
            self._note(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        imports = (isinstance(func, ast.Name) and func.id in {"__import__", "import_module"}) or (
            isinstance(func, ast.Attribute) and func.attr == "import_module"
        )
        first = node.args[0] if imports and node.args else None
        if (
            isinstance(first, ast.Constant)
            and isinstance(first.value, str)
            and first.value.split(".")[0] in {"sqlite3", "_sqlite3"}
        ):
            self._note(node)
        self.generic_visit(node)


def _scan_openings(text: str) -> list[Opening]:
    scanner = _Scanner(text)
    tree = ast.parse(text)
    # Aliases first, wherever they are imported, then every use.
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            scanner.visit_Import(node)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if node.module in _SQLITE_MODULES and alias.name in _OPENERS:
                    scanner.openers.add(alias.asname or alias.name)
                elif alias.name in {"sqlite3", "dbapi2", "_sqlite3"}:
                    scanner.modules.add(alias.asname or alias.name)
    scanner.visit(tree)
    return scanner.found


def _scan(tree_or_text: str) -> list[tuple[str, int]]:
    return [(found.scope, found.line) for found in _scan_openings(tree_or_text)]


def _openings() -> list[tuple[str, Opening]]:
    """(file, opening) for every reach for SQLite's opener in the Core."""
    return [
        (path.relative_to(SOURCE).as_posix(), found)
        for path in sorted(SOURCE.rglob("*.py"))
        for found in _scan_openings(path.read_text(encoding="utf-8"))
    ]


def _connect_calls() -> list[tuple[str, str, int]]:
    return [(file, found.scope, found.line) for file, found in _openings()]


def test_the_scan_sees_every_way_of_calling_connect():
    source = (
        "import sqlite3\nimport sqlite3 as db\nfrom sqlite3 import connect as open_it\n"
        "def plain(): sqlite3.connect('a')\n"
        "class Store:\n    def renamed(self): db.connect('b')\n"
        "def imported(): open_it('c')\n"
        "def constructed(): sqlite3.Connection('d')\n"
    )
    assert [scope for scope, _ in _scan(source)] == [
        "<module>",
        "plain",
        "Store.renamed",
        "imported",
        "constructed",
    ]
    calls = _connect_calls()
    assert ("database_access.py", "MainDatabase._open_writer") in {(f, s) for f, s, _ in calls}
    assert len(calls) >= len(ALLOWED)


BYPASSES = {
    "getattr": "import sqlite3\ndef f(): getattr(sqlite3, 'connect')('a')\n",
    "getattr by name": "import sqlite3\ndef f(name): getattr(sqlite3, name)('a')\n",
    "vars": "import sqlite3\ndef f(): vars(sqlite3)['connect']('a')\n",
    "module in a variable": "import sqlite3\ndef f():\n    m = sqlite3\n    m.connect('a')\n",
    "dbapi2 attribute": "import sqlite3\ndef f(): sqlite3.dbapi2.connect('a')\n",
    "dbapi2 import": "import sqlite3.dbapi2 as d\ndef f(): d.connect('a')\n",
    "dbapi2 from": "from sqlite3 import dbapi2\ndef f(): dbapi2.connect('a')\n",
    "dbapi2 connect": "from sqlite3.dbapi2 import connect as c\ndef f(): c('a')\n",
    "_sqlite3": "import _sqlite3\ndef f(): _sqlite3.connect('a')\n",
    "importlib": "import importlib\ndef f(): importlib.import_module('sqlite3').connect('a')\n",
    "import_module": "from importlib import import_module\ndef f(): import_module('sqlite3')\n",
    "__import__": "def f(): __import__('sqlite3').connect('a')\n",
    "stored alias": "import sqlite3\nopener = sqlite3.connect\ndef f(): opener('a')\n",
    "partial": "import functools, sqlite3\ndef f(): functools.partial(sqlite3.connect, 'a')()\n",
    "re-exported module": (
        "from one_memory.database import sqlite3\ndef f(): sqlite3.connect('a')\n"
    ),
    "Connection as a base": "import sqlite3\nclass Mine(sqlite3.Connection):\n    pass\n",
}


@pytest.mark.parametrize("bypass", sorted(BYPASSES))
def test_the_scan_sees_every_way_around_calling_connect(bypass):
    assert _scan(BYPASSES[bypass]) != []


def test_naming_a_connection_type_is_not_opening_one():
    source = (
        "import sqlite3\nfrom sqlite3 import Connection\n"
        "def f(db: sqlite3.Connection, other: 'sqlite3.Connection') -> sqlite3.Connection:\n"
        "    held: Connection | None = None\n"
        "    return db\n"
        "def g() -> None:\n"
        "    try:\n        pass\n    except sqlite3.Error:\n        sqlite3.Row\n"
    )
    assert _scan(source) == []


def test_no_module_connects_to_the_main_database_itself():
    offenders = [
        f"{file}:{line} in {scope}"
        for file, scope, line in _connect_calls()
        if (file, scope) not in ALLOWED
    ]
    assert offenders == []


def test_every_allowed_connect_still_exists():
    present = {(file, scope) for file, scope, _ in _connect_calls()}
    assert sorted(set(ALLOWED) - present) == []


def test_each_allowed_place_opens_as_many_files_as_it_says_and_how():
    found: dict[tuple[str, str], list[Opening]] = {}
    for file, opening in _openings():
        found.setdefault((file, opening.scope), []).append(opening)
    departures = []
    for key, allowed in ALLOWED.items():
        openings = found.get(key, [])
        if len(openings) != allowed.calls:
            departures.append(f"{key}: {len(openings)} openings, allowed {allowed.calls}")
        expected_openers = (
            (allowed.opened_with,) * len(openings)
            if isinstance(allowed.opened_with, str) or allowed.opened_with is None
            else allowed.opened_with
        )
        for opening, expected_opener in zip(openings, expected_openers, strict=True):
            statement = _statement_at(key[0], opening.line)
            if expected_opener is not None and expected_opener not in statement:
                departures.append(f"{key}: no longer opened with {expected_opener}")
    assert departures == []


def _statement_at(file: str, line: int) -> str:
    """The whole statement the opening is part of, where its arguments are."""
    text = (SOURCE / file).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(text)):
        if (
            isinstance(node, ast.stmt)
            and not isinstance(node, _COMPOUND)
            and node.lineno <= line <= (node.end_lineno or node.lineno)
        ):
            return ast.get_source_segment(text, node) or ""
        if isinstance(node, ast.With | ast.AsyncWith) and node.lineno == line:
            return "".join(
                ast.get_source_segment(text, item.context_expr) or "" for item in node.items
            )
    return ""


_COMPOUND = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.With,
    ast.AsyncWith,
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.Try,
)


# What the stores call to check a connection out, directly or through their own
# wrapper around the access layer.
CHECKOUTS = {
    "write",
    "read",
    "main_database_transaction",
    "_connect",
    "_connection",
    "_transaction",
    "_db",
    "_database",
    "_read",
    "_write",
}


def _awaits_in(nodes: list[ast.stmt]) -> bool:
    pending: list[ast.AST] = list(nodes)
    while pending:
        node = pending.pop()
        if isinstance(node, ast.Await | ast.AsyncFor | ast.AsyncWith):
            return True
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            pending.extend(ast.iter_child_nodes(node))
    return False


def _held_across_await(text: str) -> list[int]:
    """Lines where a coroutine keeps a checkout while it awaits."""
    found = []
    for function in ast.walk(ast.parse(text)):
        if not isinstance(function, ast.AsyncFunctionDef):
            continue
        pending: list[ast.AST] = list(function.body)
        while pending:
            node = pending.pop()
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
                continue
            checks_out = isinstance(node, ast.With) and any(
                isinstance(item.context_expr, ast.Call)
                and _called_name(item.context_expr) in CHECKOUTS
                for item in node.items
            )
            if checks_out and _awaits_in(node.body):
                found.append(node.lineno)
            pending.extend(ast.iter_child_nodes(node))
    return found


def _called_name(call: ast.Call) -> str:
    func = call.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def test_the_await_scan_sees_a_checkout_held_across_an_await():
    source = (
        "async def held(self):\n"
        "    with self._connect() as db:\n"
        "        await something()\n"
        "async def released(self):\n"
        "    with self._connect() as db:\n"
        "        rows = db.execute('x')\n"
        "    await something(rows)\n"
        "async def nested(self):\n"
        "    with main_database(p).write() as db, db:\n"
        "        if db:\n"
        "            async for _ in stream():\n"
        "                pass\n"
    )
    assert _held_across_await(source) == [2, 9]


def test_no_coroutine_keeps_the_main_database_across_an_await():
    # Coroutines share the loop's thread: one that awaits inside a checkout
    # makes the next on that thread look nested, and is refused.
    assert [
        f"{path.relative_to(SOURCE)}:{line}"
        for path in sorted(SOURCE.rglob("*.py"))
        for line in _held_across_await(path.read_text(encoding="utf-8"))
    ] == []


ATTACH = re.compile(r"\b(ATTACH|DETACH)\b", re.IGNORECASE)


def _attaching(text: str) -> list[int]:
    return [
        node.lineno
        for node in ast.walk(ast.parse(text))
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and ATTACH.search(node.value)
        and "DATABASE" in node.value.upper()
    ]


def test_the_attach_scan_sees_an_attach_statement():
    assert _attaching("db.execute(\"attach database 'x' as y\")\n") == [1]


def test_no_module_attaches_another_database():
    assert [
        f"{path.relative_to(SOURCE)}:{line}"
        for path in sorted(SOURCE.rglob("*.py"))
        for line in _attaching(path.read_text(encoding="utf-8"))
    ] == []


def _observe_main_connects(monkeypatch, main: Path) -> list[str]:
    """Modules that call sqlite3.connect on the main database themselves."""
    original = sqlite3.connect
    direct: list[str] = []

    def observed(database, *args, **kwargs):
        target = str(database)
        if target.startswith("file:"):
            target = target.split("?", 1)[0].removeprefix("file://")
        if Path(target).resolve() == main.resolve():
            caller = sys._getframe(1).f_globals.get("__name__", "?")
            if caller != "one_memory.database_access":
                direct.append(caller)
        return original(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", observed)
    return direct


def _core(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("one_memory.model_presets._preset_data", lambda: None)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return create_app(
        Settings(
            _env_file=None,
            data_dir=tmp_path / "data",
            source_home=home,
            api_token=SecretStr("test"),
            automatic_memory_processing=False,
            live_sync_enabled=False,
            live_sync_watch_enabled=False,
        ),
        source_catalog=build_source_catalog(home=home, environment={}),
    )


def _client(app) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": "Bearer test"},
    )


def test_serving_the_interface_opens_the_main_database_only_through_the_access_layer(
    tmp_path, monkeypatch
):
    direct = _observe_main_connects(monkeypatch, tmp_path / "data" / "one-memory.sqlite3")
    app = _core(tmp_path, monkeypatch)

    async def scenario():
        async with app.router.lifespan_context(app), _client(app) as client:
            paths = sorted(
                route.path
                for route in app.routes
                if "GET" in getattr(route, "methods", ()) and "{" not in route.path
            )
            for path in paths:
                await client.get(path)

    try:
        asyncio.run(scenario())
    finally:
        close_main_databases()
    assert sorted(set(direct)) == []


def test_a_history_import_opens_the_main_database_only_through_the_access_layer(
    tmp_path, monkeypatch
):
    direct = _observe_main_connects(monkeypatch, tmp_path / "data" / "one-memory.sqlite3")
    app = _core(tmp_path, monkeypatch)
    session = tmp_path / "home/.codex/sessions/2026/09/24/rollout-a.jsonl"
    session.parent.mkdir(parents=True)
    session.write_text(
        "".join(
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": f"line {n}"}],
                    },
                }
            )
            + "\n"
            for n in range(3)
        )
    )

    async def scenario():
        async with app.router.lifespan_context(app), _client(app) as client:
            found = await client.post("/api/v1/sources/codex/location")
            assert found.status_code == 200, found.text
            connected = await client.post(
                "/api/v1/sources/connect",
                json={
                    "source_ids": ["codex"],
                    "source_roots": {"codex": found.json()["root_id"]},
                    "request_id": str(uuid4()),
                    "enable_sync": True,
                },
            )
            assert connected.status_code == 200, connected.text
            job = await app.state.import_supervisor.wait(connected.json()["jobs"][0]["job_id"])
            assert job.stored == 3, job

    try:
        asyncio.run(scenario())
    finally:
        close_main_databases()
    assert sorted(set(direct)) == []


def test_shutting_the_core_down_closes_the_main_database(tmp_path, monkeypatch):
    from one_memory.database_access import main_database

    monkeypatch.setattr("one_memory.model_presets._preset_data", lambda: None)
    home = tmp_path / "home"
    home.mkdir()
    data = tmp_path / "data"
    app = create_app(
        Settings(
            _env_file=None,
            data_dir=data,
            source_home=home,
            api_token=SecretStr("test"),
            automatic_memory_processing=False,
            live_sync_enabled=False,
            live_sync_watch_enabled=False,
        ),
        source_catalog=build_source_catalog(home=home, environment={}),
    )
    opened: list[int] = []

    async def scenario():
        async with (
            app.router.lifespan_context(app),
            AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer test"},
            ) as client,
        ):
            await client.get("/api/v1/sources")
            opened.append(main_database(data / "one-memory.sqlite3").stats().open_connections)

    asyncio.run(scenario())
    assert opened[0] >= 1, "the Core must have used the access layer to be closed by it"
    assert main_database(data / "one-memory.sqlite3").stats().open_connections == 0


def test_startup_holds_the_content_backfill_until_the_startup_writes_are_done(
    tmp_path: Path, monkeypatch
):
    """The derived-content rebuild must not race the startup store writes.

    `ISSUE-CONTENT-BACKFILL-BLOCKS-STARTUP`: on a large library the rebuild
    holds the single write checkout while `EvidenceStore` and the other stores
    still need it, and startup dies with `database is locked`. The composition
    root therefore starts the rebuild gated and releases it only after the last
    startup write.
    """
    app = _core(tmp_path, monkeypatch)
    calls: list[str] = []
    real_start = app.state.__dict__.get("memory_governance_store")
    assert real_start is None  # the store is built inside the lifespan

    from one_memory import memory_governance

    original_start = memory_governance.MemoryGovernanceStore.start_content_candidate_backfill
    original_release = memory_governance.MemoryGovernanceStore.release_content_backfill_startup_gate

    def traced_start(self, *, hold_until_startup_release: bool = False) -> bool:
        calls.append(f"start(hold={hold_until_startup_release})")
        return original_start(self, hold_until_startup_release=hold_until_startup_release)

    def traced_release(self) -> None:
        calls.append("release")
        return original_release(self)

    monkeypatch.setattr(
        memory_governance.MemoryGovernanceStore,
        "start_content_candidate_backfill",
        traced_start,
    )
    monkeypatch.setattr(
        memory_governance.MemoryGovernanceStore,
        "release_content_backfill_startup_gate",
        traced_release,
    )

    async def scenario():
        async with app.router.lifespan_context(app):
            pass

    asyncio.run(scenario())
    # Gated start, and the release happens after EvidenceStore is constructed.
    assert calls[0] == "start(hold=True)", calls
    assert "release" in calls, calls
    assert calls.index("release") > calls.index("start(hold=True)"), calls
