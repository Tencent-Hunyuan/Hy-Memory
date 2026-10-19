# -*- coding: utf-8 -*-
"""Public Abstractor result types must remain constructible dataclasses."""

from hy_memory.agent.abstractor import SessionSummaryResult


def test_session_summary_result_accepts_fields():
    result = SessionSummaryResult(success=True, summary="session summary")

    assert result.success is True
    assert result.summary == "session summary"
    assert result.key_topics == []
    assert result.error is None
