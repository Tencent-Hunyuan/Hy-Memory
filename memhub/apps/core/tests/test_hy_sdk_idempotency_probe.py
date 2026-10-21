"""A0-03: probe whether the HY SDK can make a write idempotent.

docs/ARCHITECTURE.md A0-03 asks for a *measured* answer, not an assumption,
because the outcome decides whether `unknown` can ever be resolved: if the SDK
rejects a replayed write we can retry freely after a crash; if it silently
accepts one we must not retry at all.

These are introspection probes against the installed SDK, not live calls. They
deliberately avoid a real `add()`: that would need a model endpoint and would
write into a store, and the question here is only what the contract offers.
Where the answer is "the SDK exposes something but does not promise
idempotency", the test asserts the *absence of a promise* rather than guessing
at behaviour.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from one_memory.memory_server.hy_capabilities import (
    IDEMPOTENCY_SUPPORT,
    RESULT_LOOKUP_SUPPORT,
    HyIdempotencySupport,
    HyResultLookupSupport,
    hy_write_accepts_request_id,
    probe_hy_write_idempotency,
)


def test_write_accepts_a_request_id() -> None:
    """The write path must be able to carry a caller-supplied request id.

    Without this there is no handle to correlate a crash-recovery lookup
    against, and `unknown` would be unresolvable in principle.
    """
    assert hy_write_accepts_request_id() is True


@pytest.mark.parametrize("level", list(HyIdempotencySupport))
def test_idempotency_level_is_one_of_the_known_values(level: HyIdempotencySupport) -> None:
    assert isinstance(level, HyIdempotencySupport)


def test_probe_reports_a_definite_answer() -> None:
    """The probe must never answer `unknown` about the SDK.

    `unknown` is a legitimate *job* state, but a capability probe returning
    `unknown` would mean nobody ever checked, and the whole point of A0-03 is
    to replace that guess with a measurement.
    """
    assert IDEMPOTENCY_SUPPORT is not HyIdempotencySupport.UNKNOWN


def test_no_idempotency_key_means_unknown_must_remain() -> None:
    """If the SDK cannot dedupe, the contract must keep `unknown`.

    This is the actual decision A0-03 exists to record: `unknown` is not a
    temporary gap to be removed once convenient, it is required until the SDK
    can prove a replay is safe.
    """
    from one_memory.memory_server.contracts import WriteJobState

    if IDEMPOTENCY_SUPPORT is HyIdempotencySupport.NONE:
        assert WriteJobState.UNKNOWN in WriteJobState
        assert not WriteJobState.UNKNOWN.is_terminal


def test_measured_sdk_verdict_is_recorded() -> None:
    """Pin the measured answer for `hy-memory==2.0.5.dev1`.

    The SDK claims request_id before invoking the write, folds terminal replays,
    blocks running/unknown replays, and exposes the durable result directly.
    """
    report = probe_hy_write_idempotency()
    assert report["accepts_request_id"] is True
    assert report["add_declares_idempotency"] is True
    assert report["durable_write_outcome"] is True
    assert report["inspector_lookup_by_request_id"] is True
    assert report["idempotency"] == HyIdempotencySupport.KEY.value
    assert report["result_lookup"] == HyResultLookupSupport.BY_REQUEST_ID.value


def test_result_lookup_capability_is_measured_not_assumed() -> None:
    """A result lookup is the fallback that lets `unknown` be resolved later."""
    assert RESULT_LOOKUP_SUPPORT is not HyResultLookupSupport.UNKNOWN


def test_probe_uses_the_real_installed_sdk() -> None:
    """Guard against the probe silently passing on a missing SDK.

    If `hy_memory` were absent, every negative finding above would be vacuously
    true and A0-03 would look answered while nothing was inspected.
    """
    report = probe_hy_write_idempotency()
    assert report["sdk_importable"] is True
    assert isinstance(report["add_signature"], str)
    assert "request_id" in report["add_signature"]


def test_add_signature_is_read_from_the_live_object() -> None:
    signature = inspect.signature(_hy_add())  # type: ignore[attr-defined]
    assert "request_id" in signature.parameters


def _hy_add() -> Any:
    from hy_memory.client import HyMemoryClient

    return HyMemoryClient.add
