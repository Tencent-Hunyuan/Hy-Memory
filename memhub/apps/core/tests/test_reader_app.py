"""The Reader process stays read-only and never blocks a read on its side effects."""

import asyncio
from sqlite3 import OperationalError

from pydantic import SecretStr

from one_memory.imports.models import (
    EvidenceInput,
    EvidenceOrigin,
    OriginMetadata,
    WrittenBy,
)
from one_memory.imports.storage import EvidenceStore
from one_memory.memory_governance import MemoryDraft, MemoryGovernanceStore, MemoryKind
from one_memory.reader_app import ReaderSettings, SideEffectForwarder, _ReaderGovernance
from one_memory.recall import GovernedRecallTarget, RecallRequest, RecallState
from tests.person_door import as_person


def _settings(tmp_path) -> ReaderSettings:
    return ReaderSettings(
        data_dir=tmp_path,
        api_token=SecretStr("reader-token"),
        core_port=8765,
        core_api_token="core-token",
    )


def test_side_effects_are_queued_not_written(tmp_path) -> None:
    settings = _settings(tmp_path)
    forwarder = SideEffectForwarder(settings)
    for index in range(5):
        forwarder.enqueue({"tool": "om_recall", "index": index})
    # The queue is in memory only: nothing was written to the data directory.
    assert not any(tmp_path.iterdir())
    assert forwarder.queue.qsize() == 5


def test_a_full_queue_drops_instead_of_blocking(tmp_path) -> None:
    settings = _settings(tmp_path)
    forwarder = SideEffectForwarder(settings)
    for index in range(300):
        forwarder.enqueue({"index": index})
    assert forwarder.queue.qsize() == 256
    assert forwarder.dropped == 44


def test_an_unreachable_core_counts_the_drop(tmp_path) -> None:
    settings = _settings(tmp_path).model_copy(update={"core_port": 1})

    async def scenario() -> None:
        forwarder = SideEffectForwarder(settings)
        forwarder.enqueue({"tool": "om_recall"})
        task = asyncio.ensure_future(forwarder._drain())
        # The read answered already; the forward never waits on it.
        deadline = asyncio.get_running_loop().time() + 2.0
        while forwarder.dropped < 1 and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert forwarder.dropped >= 1

    asyncio.run(scenario())


def test_the_reader_connection_refuses_writes(tmp_path) -> None:
    """query_only is what keeps a read from becoming a write.

    Without it, a store that opens DDL would silently take the write lock in a
    process whose whole reason for existing is to stay out of the writer's way.
    """
    from one_memory.database_access import main_database

    database_path = tmp_path / "one-memory.sqlite3"
    with main_database(database_path).write() as connection:
        connection.execute("CREATE TABLE t (a INTEGER)")
    with main_database(database_path).read() as connection:
        try:
            connection.execute("INSERT INTO t VALUES (1)")
        except OperationalError as error:
            assert "readonly" in str(error).lower() or "read-only" in str(error).lower()
        else:
            raise AssertionError("reader connection accepted a write")


def test_reader_governance_supports_non_probe_recall(tmp_path) -> None:
    database = tmp_path / "one-memory.sqlite3"
    evidence = (
        EvidenceStore(database)
        .put(
            EvidenceInput(
                source_id="cursor",
                conversation_id="conversation",
                message_id="message",
                role="user",
                written_by=WrittenBy.PERSON,
                verbatim="用户偏好本地优先架构。",
                origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
            )
        )
        .record
    )
    governance = MemoryGovernanceStore(database)
    memory = as_person(governance).create(
        MemoryDraft(
            user_id="local",
            kind=MemoryKind.CHAT,
            content="用户偏好本地优先架构",
            scopes=("all-apps",),
            evidence_ids=(evidence.evidence_id,),
        ),
        request_id="reader-recall",
    )

    result = GovernedRecallTarget(_ReaderGovernance(database)).recall(
        RecallRequest(query="本地优先架构")
    )

    assert result.state is RecallState.GROUNDED
    assert [hit.memory_id for hit in result.hits] == [memory.memory_id]


def test_importing_the_reader_never_loads_the_write_side() -> None:
    """The Reader must not load keyring, HY, Chroma or ONNX.

    Checked in a child process because `sys.modules` in the test process is
    already polluted by whatever else the suite imported; only a fresh
    interpreter can say what importing `reader_app` drags in. This is the
    check the Reader's own startup guard performs, so a regression here is
    also a Reader that exits immediately on launch.
    """
    import subprocess
    import sys
    from pathlib import Path

    source_root = Path(__file__).resolve().parents[1] / "src"
    script = (
        "import sys\n"
        "import one_memory.reader_app\n"
        "loaded = sorted(\n"
        "    name.split('.')[0]\n"
        "    for name in sys.modules\n"
        "    if name.split('.')[0] in ('keyring', 'chromadb', 'onnxruntime', 'hy_memory')\n"
        ")\n"
        "sys.stdout.write(repr(loaded))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(source_root), "PATH": "/usr/bin:/bin"},
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]", result.stdout


def test_reader_reports_the_port_it_serves_on(tmp_path) -> None:
    """D103: the supervisor can only learn the Reader port from stderr.

    It asks for an ephemeral port (`ONE_MEMORY_READER_PORT=0`), so the Reader
    has to claim a real port first, print `reader_port:<n>` and then serve on
    that same port. Letting uvicorn pick its own port, or printing a different
    one, leaves the supervisor waiting out its 10s timeout and then connecting
    to a port nobody listens on.

    Run in a child process for the same reason as the import guard above:
    `create_reader_app` refuses to start when the suite has already imported
    `hy_memory`, so only a fresh interpreter exercises this path.
    """
    import json
    import subprocess
    import sys
    from pathlib import Path

    from one_memory.database_access import main_database

    source_root = Path(__file__).resolve().parents[1] / "src"
    script = """
import io
import json
import re
import sys
from pathlib import Path

import uvicorn
from pydantic import SecretStr

from one_memory import cli
from one_memory.config import Settings

served_on = []
served_with = []


def fake_run(application, *, host=None, port=None, **_kwargs):
    served_on.append(port)
    served_with.append(_kwargs)


uvicorn.run = fake_run
cli.load_settings = lambda: Settings(
    _env_file=None,
    data_dir=Path(sys.argv[1]),
    api_token=SecretStr("token"),
    port=0,
)

stderr = io.StringIO()
previous = sys.stderr
sys.stderr = stderr
try:
    cli.serve(reader=True)
finally:
    sys.stderr = previous

printed = stderr.getvalue()
match = re.search(r"reader_port:([0-9]+)", printed)
json.dump(
    {
        "printed": printed,
        "served_on": served_on,
        "served_with": served_with,
        "port": int(match.group(1)) if match else None,
    },
    sys.stdout,
)
"""
    database = tmp_path / "one-memory.sqlite3"
    with main_database(database).write() as connection:
        connection.execute("CREATE TABLE t (a INTEGER)")

    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        capture_output=True,
        text=True,
        env={
            "PYTHONPATH": str(source_root),
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
            "ONE_MEMORY_DATA_DIR": str(tmp_path),
            "ONE_MEMORY_READER_PORT": "0",
            "ONE_MEMORY_CORE_PORT": "8765",
            "ONE_MEMORY_CORE_API_TOKEN": "core-token",
        },
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["port"], payload["printed"]
    # Port 0 means "any free port", so the reported value must be a real one.
    assert payload["port"] > 0
    assert payload["served_on"] == [payload["port"]]
    assert payload["served_with"] == [{"reload": False, "access_log": False}]
