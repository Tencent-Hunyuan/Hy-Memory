"""Diagnostic: why does an all-imported preview see zero Evidence?

The C0 integration red asserted a preview snapshot had two Evidence rows after
a real WorkBuddy import. This keeps the useful import-path diagnostic after
the retired model-content preview layer was removed.
"""

import asyncio
import sqlite3
from pathlib import Path

from one_memory.imports.grants import SourceGrantStore
from one_memory.imports.jobs import ImportJobStore
from one_memory.imports.sources.registry import RootCandidateKind, build_source_catalog
from one_memory.imports.storage import EvidenceStore
from one_memory.imports.supervisor import ImportSupervisor


def _write_workbuddy(home: Path) -> Path:
    # BUG-01: the import root is the WorkBuddy home. `~/.codebuddy` is the
    # CLI's own config directory and is no longer a candidate.
    root = home / ".workbuddy" / "projects"
    root.mkdir(parents=True, exist_ok=True)
    file = root / "conversation.jsonl"
    lines = [
        '{"type":"user","timestamp":"2026-09-28T10:00:00+08:00","message":{"content":"first"}}',
        '{"type":"assistant","timestamp":"2026-09-28T10:00:01+08:00",'
        '"message":{"content":"second"}}',
    ]
    file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return root


def test_all_imported_preview_query_matches_imported_evidence(tmp_path):
    home = tmp_path / "home"
    root = _write_workbuddy(home)
    catalog = build_source_catalog(home=home, environment={})
    registration = catalog["workbuddy"]
    assert registration.candidate.path.resolve() == root.resolve()

    database = tmp_path / "data/core.sqlite3"
    database.parent.mkdir(parents=True, exist_ok=True)
    jobs = ImportJobStore(database)
    evidence = EvidenceStore(database)
    grants = SourceGrantStore(database, home=home)
    grants.grant(
        user_id="local",
        source_id="workbuddy",
        root=root,
        candidate_kind=RootCandidateKind.DEFAULT,
    )

    async def scenario():
        supervisor = ImportSupervisor(
            adapters={
                "workbuddy": registration.create_adapter(
                    grants.authorized_root(user_id="local", source_id="workbuddy")
                )
            },
            evidence_store=evidence,
            job_store=jobs,
        )
        job = supervisor.create_job("workbuddy")
        supervisor.enqueue(job.job_id)
        return await supervisor.wait(job.job_id)

    result = asyncio.run(scenario())
    assert result.stored == 2, result

    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT user_id, source_id, count(*) FROM evidence GROUP BY user_id, source_id"
        ).fetchall()
        scope = connection.execute(
            "SELECT evidence_id FROM evidence WHERE user_id=? AND source_id IN (?) "
            "ORDER BY evidence_id LIMIT 100001",
            ("local", "workbuddy"),
        ).fetchall()
    assert rows == [("local", "workbuddy", 2)], rows
    assert len(scope) == 2, scope
