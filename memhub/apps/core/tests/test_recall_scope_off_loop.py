"""The workspace lookup behind an external recall must not run on the loop.

The loop watchdog caught this read on the loop thread in 27 of the 60 stalls it
reported: `known_workspaces` answers from a covering index but still walks
hundreds of thousands of index entries, and every recall from an installed host
paid for it serially. This pins the thread it runs on, because a test that only
asserts the answer would pass either way — the bug was never a wrong answer, it
was an unanswered loop.
"""

import asyncio
import threading
from typing import Any

from fastapi import FastAPI
from starlette.requests import Request

from one_memory.api import _require_external_recall_scope


class _Access:
    def require(self, tool: str, scopes: frozenset[str]) -> None:
        return None


class _FakeEvidenceStore:
    known: "list[int]" = []

    def known_workspaces(self, *, user_id: str) -> list[Any]:
        _FakeEvidenceStore.known.append(threading.get_ident())
        return []


def test_workspace_lookup_for_an_external_recall_runs_off_the_loop(monkeypatch) -> None:
    _FakeEvidenceStore.known = []
    monkeypatch.setattr("one_memory.api.external_access", lambda headers: _Access())
    monkeypatch.setattr("one_memory.api._evidence_store", lambda app: _FakeEvidenceStore())

    async def scenario() -> None:
        loop_thread = threading.get_ident()
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/v1/recall",
                "headers": [],
                "query_string": b"",
            }
        )
        await _require_external_recall_scope(
            FastAPI(),
            request,
            "all-apps",
            None,
            "om_recall",
        )
        assert _FakeEvidenceStore.known, "the workspace lookup was never called"
        assert _FakeEvidenceStore.known[0] != loop_thread, (
            "known_workspaces ran on the event loop thread; it must run in a worker"
        )

    asyncio.run(scenario())
