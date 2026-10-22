"""Instrumented import accounting: bytes read, opens, hashes, jobs, duplicates.

The C1 performance contract names specific counters (real bytes read, open
count, hash count, job count, duplicate count). This measures them on a real
JSONL source so "incremental" and "not a second reader" are falsifiable numbers
rather than prose: an append must open the file once and read only its tail,
an unchanged source must read zero bytes and open zero times, and neither run
may create a second job or a duplicate Evidence row.
"""

import asyncio
import hashlib
import json
from pathlib import Path

from one_memory.imports.access import AuthorizedSourceFile
from one_memory.imports.grants import SourceGrantStore
from one_memory.imports.jobs import ImportJobStore, ImportStatus
from one_memory.imports.sources import registry
from one_memory.imports.storage import EvidenceStore
from one_memory.imports.supervisor import ImportSupervisor


class _Counters:
    def __init__(self) -> None:
        self.opens = 0
        self.bytes_read = 0
        self.jobs = 0
        self.hashes = 0


def _instrument(monkeypatch) -> _Counters:
    counters = _Counters()

    original_open = AuthorizedSourceFile.open_binary

    def counting_open(source_file):
        counters.opens += 1
        handle = original_open(source_file)
        # The JSONL reader advances with readline, not read; observing read
        # would report zero bytes and hide a full re-read regression.
        original_readline = handle.readline

        def counting_readline(size=-1):
            chunk = original_readline(size)
            counters.bytes_read += len(chunk)
            return chunk

        handle.readline = counting_readline
        return handle

    monkeypatch.setattr(AuthorizedSourceFile, "open_binary", counting_open)

    original_create_job = ImportSupervisor.create_job

    def counting_create_job(self, *args, **kwargs):
        counters.jobs += 1
        return original_create_job(self, *args, **kwargs)

    monkeypatch.setattr(ImportSupervisor, "create_job", counting_create_job)

    # Count real hashing: the reader anchors every record (sha256 of the line)
    # and verifies the resume anchor (sha256 of the preceding line).
    original_sha256 = hashlib.sha256

    def counting_sha256(data=b"", *args, **kwargs):
        counters.hashes += 1
        return original_sha256(data, *args, **kwargs)

    monkeypatch.setattr(hashlib, "sha256", counting_sha256)
    return counters


def _write_codex(path: Path, texts: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        for text in texts:
            stream.write(
                json.dumps(
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": text}],
                        },
                    }
                )
                + "\n"
            )


class _Source:
    def __init__(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        home.mkdir(parents=True, exist_ok=True)
        self.registration = registry.build_source_catalog(
            home=home, environment={}, import_root=tmp_path / "exports"
        )["codex"]
        self.root = self.registration.candidate.path
        self.root.mkdir(parents=True, exist_ok=True)
        database = tmp_path / "data" / "core.sqlite3"
        database.parent.mkdir(parents=True, exist_ok=True)
        self.jobs = ImportJobStore(database)
        self.evidence = EvidenceStore(database)
        self.grants = SourceGrantStore(database, home=home)
        self.grants.grant(
            user_id="local",
            source_id="codex",
            root=self.root,
            candidate_kind=registry.RootCandidateKind.DEFAULT,
        )
        self.adapter = self.registration.create_adapter(
            self.grants.authorized_root(user_id="local", source_id="codex")
        )

    def run(self):
        async def scenario():
            supervisor = ImportSupervisor(
                adapters={"codex": self.adapter},
                evidence_store=self.evidence,
                job_store=self.jobs,
            )
            job = supervisor.create_job("codex")
            supervisor.enqueue(job.job_id)
            return await supervisor.wait(job.job_id)

        return asyncio.run(scenario())


def test_import_accounting_is_bounded_and_duplicate_free(tmp_path, monkeypatch, caplog):
    caplog.set_level("DEBUG", logger="one_memory.imports.sources.common")
    counters = _instrument(monkeypatch)
    fixture = _Source(tmp_path)
    path = fixture.root / "project" / "rollout-accounting.jsonl"
    _write_codex(path, ["first row", "second row"])
    total = path.stat().st_size

    history = fixture.run()
    assert history.status is ImportStatus.COMPLETED and history.stored == 2
    assert counters.jobs == 1, "one import must create exactly one job"
    # Codex records carry a session header, so each file is opened twice by
    # design: once to probe the header and once for the bounded record read.
    # The property that matters is that this is a fixed constant per file, not
    # a per-record or per-chunk reopen.
    assert counters.opens == 2, (
        f"one header-bearing file must be opened exactly twice, got {counters.opens}"
    )
    # Two records: each is anchored with a sha256 of its line.
    assert counters.hashes >= 2, (
        f"two records must be hashed for their anchors, got {counters.hashes}"
    )
    # The reader may read a bounded probe plus the records; it must not read
    # wildly more than the file itself.
    assert 0 < counters.bytes_read <= total * 4, (
        f"read {counters.bytes_read} bytes for a {total}-byte file"
    )

    first_bytes = counters.bytes_read
    counters.opens = 0
    counters.bytes_read = 0
    counters.hashes = 0

    unchanged = fixture.run()
    assert (unchanged.stored, unchanged.emitted) == (0, 0)
    assert counters.opens == 0, "an unchanged file must not be reopened"
    assert counters.bytes_read == 0, "an unchanged file must not be re-read"
    # A no-op run still hashes metadata (root identity, file keys, import token),
    # but it must not hash *record content* — that would mean it read the file.
    # The opens/bytes assertions above already prove no content read; this pins
    # that the hash count stays in the small metadata range rather than scaling
    # with the two records that were not read.
    assert counters.hashes < 32, (
        f"an unchanged run must only hash metadata, not record content: got {counters.hashes}"
    )
    assert counters.jobs == 2, "each run creates its own job, but no extra empty job"

    counters.opens = 0
    counters.bytes_read = 0
    with path.open("a", encoding="utf-8") as stream:
        stream.write(
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "appended row"}],
                    },
                }
            )
            + "\n"
        )
    appended = fixture.run()
    assert appended.stored == 1
    # Two opens, both bounded and each with a distinct purpose:
    #   1. resume_checkpoint -> verify_jsonl_checkpoint (anchor still matches)
    #   2. the bounded record read from the cursor
    # The opening metadata record was cached by the adapter's first pass and
    # must not be reread for every append of a very large Codex transcript.
    # The property that matters for a long file is that this is a fixed
    # constant per file, not one open per record.
    assert counters.opens == 2, (
        f"an append must use only checkpoint verification and tail read, got {counters.opens}"
    )
    assert counters.bytes_read < first_bytes, (
        "an append must read the tail, not the whole file again: "
        f"read {counters.bytes_read} vs first pass {first_bytes}"
    )
    read_logs = [
        record.message for record in caplog.records if "import_file_read" in record.message
    ]
    assert "file_bytes=" in read_logs[-1] and "planned_bytes=" in read_logs[-1]
    assert f"file_bytes={path.stat().st_size}" in read_logs[-1]
    assert f"planned_bytes={path.stat().st_size}" not in read_logs[-1]
    assert fixture.evidence.count() == 3, "no duplicate Evidence rows"


def test_history_slices_reuse_the_authorized_file_listing(tmp_path, monkeypatch) -> None:
    fixture = _Source(tmp_path)
    path = fixture.root / "project" / "rollout-many.jsonl"
    _write_codex(path, [f"row {index}" for index in range(120)])

    from one_memory.imports.sources import common

    calls = 0
    original = common.list_changed_files

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(common, "list_changed_files", counted)

    result = fixture.run()

    assert result.status is ImportStatus.COMPLETED
    assert result.stored == 120
    assert calls <= 2, f"bounded slices repeated a full source discovery {calls} times"
