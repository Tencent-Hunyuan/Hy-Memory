"""DeepSeek Harness writes one session file per session, named after its format.

The version segment is part of the published file name: the first format wrote
`session.jsonl` / `session.jsonl.zstd`, v3 and v4 write
`session.v3.jsonl.zstd` / `session.v4.jsonl.zstd` and a desktop profile carries
nothing else. The importer therefore has to read both shapes, while still
leaving a browser-side export that was merely dropped into `sessions/` alone:
that one has no `type=session` envelope, so the record parser rejects it.

The name is discovery only; the header's `version` decides whether this parser
is allowed to read the file, so a future generation is skipped instead of being
interpreted with today's rules.
"""

import asyncio
import json
from pathlib import Path

import zstandard

from one_memory.imports.grants import SourceGrantStore
from one_memory.imports.jobs import ImportJobStore, ImportStatus
from one_memory.imports.sources.registry import RootCandidateKind, build_source_catalog
from one_memory.imports.storage import EvidenceStore
from one_memory.imports.supervisor import ImportSupervisor


def v4_records(identity: str) -> list[dict]:
    """One v4 session: the header carries id/cwd once, records carry `data`."""
    return [
        {
            "type": "session",
            "version": 4,
            "id": identity,
            "createdAt": 1792229393257,
            "cwd": "/private/project",
            "delegationDepth": 0,
            "agentPreset": "standard",
        },
        {
            "type": "user/message",
            "seq": 1,
            "time": 1792229393257,
            "data": {
                "id": f"{identity}-a",
                "role": "user",
                "source": {"kind": "user"},
                "content": [{"type": "text", "text": "v4 person turn"}],
            },
        },
    ]


HEADLESS_RECORDS = [
    {"type": "session", "id": "s", "cwd": "/private/project"},
    {
        "type": "user/message",
        "seq": 1,
        "data": {
            "id": "a",
            "role": "user",
            "source": {"kind": "user"},
            "content": [{"type": "text", "text": "headless person turn"}],
        },
    },
]

# The shape a browser-side export has: a `role`-keyed transcript, no Harness
# `type=session` envelope, and not named `session.jsonl`.
WEB_EXPORT_RECORDS = [
    {"role": "user", "content": "web export person turn", "time": None},
    {"role": "assistant", "content": "web export agent turn", "time": None},
]


class Source:
    def __init__(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        home.mkdir(parents=True, exist_ok=True)
        self.registration = build_source_catalog(home=home, environment={})["deepseek-harness"]
        self.root = self.registration.candidate.path
        self.root.mkdir(parents=True, exist_ok=True)
        database = tmp_path / "data" / "core.sqlite3"
        database.parent.mkdir(parents=True, exist_ok=True)
        self.jobs = ImportJobStore(database)
        self.evidence = EvidenceStore(database)
        self.grants = SourceGrantStore(database, home=home)
        self.grants.grant(
            user_id="local",
            source_id="deepseek-harness",
            root=self.root,
            candidate_kind=RootCandidateKind.DEFAULT,
        )

    def write(self, relative: str, records: list[dict]) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as stream:
            for record in records:
                stream.write(json.dumps(record) + "\n")
        return path

    def write_zstd(self, relative: str, records: list[dict]) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        body = "".join(json.dumps(record) + "\n" for record in records)
        path.write_bytes(zstandard.ZstdCompressor().compress(body.encode("utf-8")))
        return path

    def run(self):
        async def scenario():
            supervisor = ImportSupervisor(
                adapters={
                    "deepseek-harness": self.registration.create_adapter(
                        self.grants.authorized_root(user_id="local", source_id="deepseek-harness")
                    )
                },
                evidence_store=self.evidence,
                job_store=self.jobs,
            )
            job = supervisor.create_job("deepseek-harness")
            supervisor.enqueue(job.job_id)
            return await supervisor.wait(job.job_id)

        return asyncio.run(scenario())


def test_only_the_headless_session_form_is_importable(tmp_path):
    fixture = Source(tmp_path)
    fixture.write("headless-run/session.jsonl", HEADLESS_RECORDS)
    # A web export dropped into the same tree: different name, different shape.
    fixture.write("web-export/transcript.jsonl", WEB_EXPORT_RECORDS)

    result = fixture.run()
    assert result.status is ImportStatus.COMPLETED
    assert result.stored == 1, (
        "only the headless session.jsonl may import; the web export must be "
        f"skipped, got stored={result.stored}"
    )
    stored = [row.verbatim for row in fixture.evidence.list_extractable(limit=50)]
    assert "headless person turn" in stored
    assert not any("web export" in text for text in stored), (
        "a web-style export must not be imported through the Harness profile"
    )


def test_a_web_export_alone_imports_nothing(tmp_path):
    fixture = Source(tmp_path)
    fixture.write("web-export/transcript.jsonl", WEB_EXPORT_RECORDS)

    result = fixture.run()
    assert result.status is ImportStatus.COMPLETED
    assert result.stored == 0
    assert fixture.evidence.count() == 0


def test_the_adapter_has_two_independent_layers(tmp_path):
    """File-name gating and record-shape gating are separate defences.

    Mutation M28 (relax `file_predicate` to accept any `.jsonl`) did NOT turn the
    tests above red, and that is honest rather than a weak assertion: the parser
    rejects a record with no `data` envelope as UNKNOWN_RECORD, so the web export
    still imports nothing. This test states both layers explicitly so neither is
    mistaken for the other, and so a future change that removes one is visible.
    """
    fixture = Source(tmp_path)
    adapter = fixture.registration.create_adapter(
        fixture.grants.authorized_root(user_id="local", source_id="deepseek-harness")
    )
    headless = fixture.write("headless-run/session.jsonl", HEADLESS_RECORDS)
    web = fixture.write("web-export/transcript.jsonl", WEB_EXPORT_RECORDS)

    # Layer 1: the file predicate accepts the session file names and rejects a
    # web-style export by name.
    predicate = adapter.file_predicate
    listed_headless = adapter.grant.listed_file(headless)
    listed_web = adapter.grant.listed_file(web)
    assert predicate(listed_headless) is True
    versioned = fixture.write_zstd("headless-run/session.v4.jsonl.zstd", v4_records("v4"))
    assert predicate(adapter.grant.listed_file(versioned)) is True
    assert (
        predicate(
            adapter.grant.listed_file(fixture.write("x/session.v4.jsonl", v4_records("named")))
        )
        is True
    )
    assert predicate(listed_web) is False, (
        "the file predicate must exclude a web-style export by name"
    )

    # Layer 2: even if a web record reached the parser, it is skipped, not stored
    # as a person's words.
    from one_memory.imports.sources.common import SourceRecordContext
    from one_memory.imports.sources.deepseek_harness import parse_deepseek_harness_record

    context = SourceRecordContext(
        file_key="k",
        relative_path="web-export/transcript.jsonl",
        ordinal="k:1",
        header=None,
    )
    parsed = parse_deepseek_harness_record(WEB_EXPORT_RECORDS[0], context)
    assert not hasattr(parsed, "verbatim"), (
        "a record without the Harness data envelope must not become Evidence"
    )


def test_versioned_session_files_import_with_the_same_parser(tmp_path):
    """A v4 desktop session imports; the version is in the file name only."""
    fixture = Source(tmp_path)
    fixture.write_zstd("desktop-run/session.v4.jsonl.zstd", v4_records("v4"))

    result = fixture.run()
    assert result.status is ImportStatus.COMPLETED
    assert result.stored == 1
    assert fixture.evidence.count() == 1
    stored = [row.verbatim for row in fixture.evidence.list_extractable(limit=50)]
    assert "v4 person turn" in stored


def test_a_mixed_sessions_tree_imports_every_version_and_nothing_else(tmp_path):
    """Old and new session files in one root both import; exports still do not."""
    fixture = Source(tmp_path)
    fixture.write("first/session.jsonl", HEADLESS_RECORDS)
    fixture.write_zstd("second/session.v3.jsonl.zstd", v4_records("v3"))
    fixture.write_zstd("third/session.v4.jsonl.zstd", v4_records("v4"))
    fixture.write("fourth/transcript.jsonl", WEB_EXPORT_RECORDS)

    result = fixture.run()
    assert result.status is ImportStatus.COMPLETED
    assert result.stored == 3
    stored = [row.verbatim for row in fixture.evidence.list_extractable(limit=50)]
    assert "headless person turn" in stored
    assert sum("v4 person turn" in text for text in stored) == 2
    assert not any("web export" in text for text in stored)


def test_a_rescan_after_upgrade_picks_up_unmatched_versions_without_duplicates(tmp_path):
    """The upgrade path: old evidence stays, versioned files arrive later.

    Before this fix the predicate matched only the first format, so a real
    `$DSH_HOME/sessions` tree imported almost nothing. A scan after the fix has
    to discover the versioned files that were never imported and leave the
    evidence already stored alone.
    """
    fixture = Source(tmp_path)
    fixture.write("old-run/session.jsonl", HEADLESS_RECORDS)
    first = fixture.run()
    assert first.status is ImportStatus.COMPLETED
    assert first.stored == 1

    fixture.write_zstd("new-run/session.v3.jsonl.zstd", v4_records("v3"))
    fixture.write_zstd("newer-run/session.v4.jsonl.zstd", v4_records("v4"))

    second = fixture.run()
    assert second.status is ImportStatus.COMPLETED
    stored = [row.verbatim for row in fixture.evidence.list_extractable(limit=50)]
    assert "headless person turn" in stored
    assert sum("v4 person turn" in text for text in stored) == 2


def test_an_unknown_session_version_imports_nothing(tmp_path):
    """A v5 file is discovered and refused, not read as v4."""
    future = [
        {"type": "session", "version": 5, "id": "future", "cwd": "/private/project"},
        {
            "type": "user/message",
            "seq": 1,
            "data": {
                "id": "future-a",
                "role": "user",
                "source": {"kind": "user"},
                "content": [{"type": "text", "text": "future person turn"}],
            },
        },
    ]
    fixture = Source(tmp_path)
    fixture.write("future-run/session.v5.jsonl", future)

    result = fixture.run()
    assert result.status is ImportStatus.COMPLETED
    assert result.stored == 0
    assert fixture.evidence.count() == 0
