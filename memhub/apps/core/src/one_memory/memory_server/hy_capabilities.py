"""A0-03: measured answer to "can a HY write be made idempotent?".

The result decides whether the `unknown` job state can ever be retired, so it
is probed against the installed SDK rather than written down from memory.

What is verified against `hy-memory==2.0.5.dev3`:

- `HyMemoryClient.add(..., request_id=...)` accepts a caller-supplied id and
  atomically claims it in the SDK cache before invoking a write. Replays return
  the durable terminal result; running/unknown claims are not invoked again.
- `HyMemoryClient.get_write_outcome(request_id)` reads that durable outcome
  directly; it does not depend on optional pipeline tracing.

A first pass at this probe got both answers wrong, and the failure mode is
worth keeping in mind: searching SDK source for the string "idempotent" hits a
docstring about *episode* nodes ("calls are idempotent") and a "dedup" comment
about search over-sampling. Neither is write idempotency. So the probe checks
the `add` path specifically and names the inspector class explicitly, instead
of grepping for a word that appears in unrelated prose.

Net: MemHub can query and settle an uncertain request without blind replay.
`unknown` remains a legitimate operational state while the SDK claim itself is
non-terminal, but the same request id cannot write twice.
"""

from __future__ import annotations

import importlib
import inspect
from enum import StrEnum
from typing import Any


class HyIdempotencySupport(StrEnum):
    """Whether the SDK can dedupe a replayed write."""

    #: A caller key is honoured and a replay is rejected or folded together.
    KEY = "key"
    #: Same content is folded by the engine, without an explicit key.
    CONTENT_HASH = "content_hash"
    #: A replay writes a second time. Retrying after a crash duplicates.
    NONE = "none"
    #: Never probed. Must not survive A0-03.
    UNKNOWN = "unknown"


class HyResultLookupSupport(StrEnum):
    """Whether the outcome of a past write can be queried afterwards."""

    BY_REQUEST_ID = "by_request_id"
    BY_MEMORY_ID_ONLY = "by_memory_id_only"
    NONE = "none"
    UNKNOWN = "unknown"


def _module(name: str) -> Any | None:
    try:
        return importlib.import_module(name)
    except ImportError:
        return None


def _has_callable(obj: Any, name: str) -> bool:
    return obj is not None and callable(getattr(obj, name, None))


def hy_write_accepts_request_id() -> bool:
    """True when the write path carries a caller-supplied request id."""
    client_mod = _module("hy_memory.client")
    if client_mod is None:
        return False
    client = getattr(client_mod, "HyMemoryClient", None)
    if client is None:
        return False
    add = getattr(client, "add", None)
    if add is None:
        return False
    return "request_id" in inspect.signature(add).parameters


def _add_path_declares_idempotency() -> bool:
    """Whether the *write* path de-duplicates, judged on its own source.

    Deliberately scoped to `add`/`async_add`. A whole-module search matches
    unrelated prose: "calls are idempotent" describes episode-node upserts, and
    a "dedup" comment describes search over-sampling. Both are about other
    features and would make this probe answer `key` for the wrong reason.
    """
    client_mod = _module("hy_memory.client")
    if client_mod is None:
        return False
    client = getattr(client_mod, "HyMemoryClient", None)
    if client is None:
        return False
    sources: list[str] = []
    for name in ("add", "async_add"):
        func = getattr(client, name, None)
        if func is None:
            continue
        try:
            sources.append(inspect.getsource(func).lower())
        except (OSError, TypeError):
            continue
    if not sources:
        return False
    if _has_callable(client, "get_write_outcome"):
        claim = getattr(client, "_claim_write_outcome", None)
        if claim is not None:
            return True
    # Older SDKs may expose an explicit key under one of these names.
    markers = ("idempotency_key", "idempotent_key", "dedupe_key", "dedup_key")
    return any(marker in source for source in sources for marker in markers)


def _inspector_can_lookup_by_request_id() -> bool:
    """True when pipeline logs can be queried by the write's request id."""
    inspector = _module("hy_memory.inspector")
    if inspector is None:
        return False
    for cls_name in ("MemoryInspector", "Inspector", "PipelineInspector"):
        cls = getattr(inspector, cls_name, None)
        if cls is None:
            continue
        for method in ("get_pipeline_logs", "async_get_pipeline_logs"):
            func = getattr(cls, method, None)
            if func is None:
                continue
            # The parameter is `request_id` on `MemoryInspector`; older
            # docstrings call the same thing `n`. Accept either so a rename
            # does not silently downgrade this to "no lookup available".
            if {"request_id", "n"} & set(inspect.signature(func).parameters):
                return True
    return False


def _probe_idempotency() -> HyIdempotencySupport:
    if not hy_write_accepts_request_id():
        # No handle at all: not even a correlation id to investigate later.
        return HyIdempotencySupport.NONE
    if _add_path_declares_idempotency():
        return HyIdempotencySupport.KEY
    # A request_id is present but nothing de-duplicates on it.
    return HyIdempotencySupport.NONE


def _probe_result_lookup() -> HyResultLookupSupport:
    client_mod = _module("hy_memory.client")
    client = getattr(client_mod, "HyMemoryClient", None) if client_mod is not None else None
    if _has_callable(client, "get_write_outcome"):
        return HyResultLookupSupport.BY_REQUEST_ID
    if _inspector_can_lookup_by_request_id():
        return HyResultLookupSupport.BY_REQUEST_ID
    if _has_callable(client, "get"):
        return HyResultLookupSupport.BY_MEMORY_ID_ONLY
    return HyResultLookupSupport.NONE


IDEMPOTENCY_SUPPORT: HyIdempotencySupport = _probe_idempotency()
RESULT_LOOKUP_SUPPORT: HyResultLookupSupport = _probe_result_lookup()


def probe_hy_write_idempotency() -> dict[str, Any]:
    """Full probe report, for STATUS evidence and for debugging a bumped SDK.

    Re-probed on every call so a dependency upgrade that changes the answer is
    visible, rather than being frozen at import time.
    """
    client_mod = _module("hy_memory.client")
    client = getattr(client_mod, "HyMemoryClient", None)
    add = getattr(client, "add", None)
    return {
        "sdk_importable": client_mod is not None,
        "add_signature": str(inspect.signature(add)) if add is not None else "",
        "accepts_request_id": hy_write_accepts_request_id(),
        "add_declares_idempotency": _add_path_declares_idempotency(),
        "durable_write_outcome": (
            client is not None and _has_callable(client, "get_write_outcome")
        ),
        "inspector_lookup_by_request_id": _inspector_can_lookup_by_request_id(),
        "idempotency": _probe_idempotency().value,
        "result_lookup": _probe_result_lookup().value,
    }
