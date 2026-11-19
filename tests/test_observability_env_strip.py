# -*- coding: utf-8 -*-
"""Regression: boolean observability env values should tolerate whitespace.

is_pipeline_trace_enabled and is_memory_operations_enabled call .lower()
without .strip(), while sibling functions in the same module use
.strip().lower(). A value like " true " was therefore wrongly read as False.
"""

from hy_memory.utils import pipeline_observability as po


def test_trace_enabled_with_whitespace(monkeypatch):
    monkeypatch.setenv("MEMORY_PIPELINE_TRACE_ENABLED", " true ")
    assert po.is_pipeline_trace_enabled() is True


def test_memory_operations_enabled_with_whitespace(monkeypatch):
    monkeypatch.setenv("MEMORY_MEMORY_OPERATIONS_ENABLED", " yes ")
    assert po.is_memory_operations_enabled() is True


def test_plain_values_still_work(monkeypatch):
    monkeypatch.setenv("MEMORY_PIPELINE_TRACE_ENABLED", "false")
    assert po.is_pipeline_trace_enabled() is False
