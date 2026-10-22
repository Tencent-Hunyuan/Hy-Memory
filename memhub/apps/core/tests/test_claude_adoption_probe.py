"""The external-use verifier must distinguish a read from merely mentioning it."""

import importlib.util
import json
from pathlib import Path

import pytest

PROBE = Path(__file__).resolve().parents[3] / "scripts" / "claude_adoption_probe.py"
if not PROBE.is_file():
    pytest.skip("claude adoption probe is not part of this checkout", allow_module_level=True)


def verifier():
    spec = importlib.util.spec_from_file_location("claude_adoption_probe", PROBE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.verify_events


def event(role, *blocks):
    return {"type": role, "message": {"role": role, "content": list(blocks)}}


def transcript(*, hits=None):
    return [
        event(
            "assistant",
            {
                "type": "tool_use",
                "id": "call-1",
                "name": "mcp__one-memory__om_recall",
                "input": {"query": "project meeting"},
            },
        ),
        event(
            "user",
            {
                "type": "tool_result",
                "tool_use_id": "call-1",
                "is_error": False,
                "content": json.dumps(
                    {"state": "grounded" if hits else "empty", "hits": hits or []}
                ),
            },
        ),
        event("assistant", {"type": "text", "text": "The meeting avoids Friday."}),
    ]


def test_correlated_positive_result_and_answer_are_both_required():
    events = transcript(hits=[{"memory_id": "m1", "content": "Avoid Friday"}])
    result = verifier()(events, memory_id="m1", expected_text="Friday")
    assert result["passed"]
    assert result["matched_memory_ids"] == ["m1"]


@pytest.mark.parametrize(
    "mutation",
    [
        "other_call",
        "other_tool",
        "tool_error",
        "malformed",
        "duplicate_call",
        "early_answer",
        "no_answer",
        "no_calls",
    ],
)
def test_false_positive_adoption_is_rejected(mutation):
    events = transcript(hits=[{"memory_id": "m1", "content": "Avoid Friday"}])
    if mutation == "other_call":
        events[1]["message"]["content"][0]["tool_use_id"] = "another-call"
    elif mutation == "other_tool":
        events[0]["message"]["content"][0]["name"] = "read_file"
    elif mutation == "tool_error":
        events[1]["message"]["content"][0]["is_error"] = True
    elif mutation == "malformed":
        events[1]["message"]["content"][0]["content"] = "m1 Friday not a JSON result"
    elif mutation == "duplicate_call":
        events.insert(1, events[0])
    elif mutation == "early_answer":
        events.insert(0, events.pop())
    elif mutation == "no_answer":
        events.pop()
    else:
        events = [events[-1]]
    assert not verifier()(events, memory_id="m1", expected_text="Friday")["passed"]


def test_absence_needs_a_successful_empty_recall_and_never_error_or_missing_call():
    verify = verifier()
    assert verify(transcript(), memory_id="m1", expect_absent=True)["passed"]
    assert not verify([], memory_id="m1", expect_absent=True)["passed"]
    assert not verify(
        transcript(hits=[{"memory_id": "m1", "content": "Friday"}]),
        memory_id="m1",
        expect_absent=True,
    )["passed"]
    failed = transcript()
    failed[1]["message"]["content"][0]["is_error"] = True
    assert not verify(failed, memory_id="m1", expect_absent=True)["passed"]


@pytest.mark.parametrize("fault", ["truncated", "unexamined", "inconsistent_state"])
def test_incomplete_search_is_not_verified_absence(fault):
    events = transcript()
    result = {"state": "empty", "hits": [], "truncated": False, "unexamined_count": 0}
    if fault == "truncated":
        result["truncated"] = True
    elif fault == "unexamined":
        result["unexamined_count"] = 10
    else:
        result["state"] = "grounded"
    events[1]["message"]["content"][0]["content"] = json.dumps(result)
    assert not verifier()(events, memory_id="m1", expect_absent=True)["passed"]


def test_empty_recall_cannot_ground_an_answer_reusing_the_unavailable_value():
    assert not verifier()(transcript(), memory_id="m1", expected_text="Friday", expect_absent=True)[
        "passed"
    ]
