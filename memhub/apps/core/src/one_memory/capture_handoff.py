"""The hook side of live capture: hand the turn to the running Core, nothing more.

A host runs `one-memory capture <source>` as a short-lived process on every
hook, often several at once. That process used to open the main database
itself and write Evidence and the write queue from outside Core's single
writer. It now never opens the database: it reads the hook's JSON, posts it to
the running Core over loopback with the runtime token, and exits. Core applies
every gate and stages the turn in-process (`live_capture.capture_hook`).

Anything that goes wrong — Core not running, a stale receipt, a slow answer —
drops the turn silently. There is no local fallback write: file-backed sources
are read again by live sync from their persisted cursor once Core is back.
"""

import json
import os
from pathlib import Path
from typing import TextIO

CAPTURE_SOURCES = frozenset(
    {
        "workbuddy",
        "claude-code",
        "codex",
        "cursor",
        "pi",
        "qwen-code",
        "qwenwork",
        "hermes",
        "deepseek-harness",
        "opencode",
        "openclaw",
    }
)
SDK_MIGRATION_CAPTURE_FENCE = ".sdk-migration-capture-fence.json"
#: Core's in-process intake for a hook's payload; see `live_capture`.
CAPTURE_ROUTE = "/api/v1/live-capture/{source_id}"
#: Whole request, connect to answer. Pi and OpenCode wait for the hook
#: synchronously (3 s, interpreter start included), so this stays well under.
HANDOFF_TIMEOUT_SECONDS = 1.5
#: A hook carries one turn, or one session's visible text at most.
MAX_PAYLOAD_BYTES = 8 * 1024 * 1024


def data_directory() -> Path:
    from one_memory.config import load_settings

    return load_settings().data_dir


def capture_stdio(source_id: str, stdin: TextIO, output: TextIO) -> int:
    """Host hooks must stay silent. A bad payload or an absent Core does not fail the turn."""

    del output
    if source_id not in CAPTURE_SOURCES:
        return 2
    try:
        data_dir = data_directory()
        if sdk_migration_capture_fenced(data_dir):
            return 0
        payload = json.loads(stdin.read() or "")
    except Exception:
        return 0
    if not isinstance(payload, dict):
        return 0
    try:
        hand_to_core(source_id, payload, data_dir)
    except Exception:
        return 0
    return 0


def hand_to_core(
    source_id: str,
    payload: dict[str, object],
    data_dir: Path,
    *,
    timeout: float = HANDOFF_TIMEOUT_SECONDS,
) -> bool:
    """Post the payload to the Core named by this home's receipt. True when Core took it.

    Only the receipt, the runtime token and a loopback socket are touched; the
    main database is never opened from here.
    """
    from one_memory.runtime_client import core_endpoint

    endpoint = core_endpoint(data_dir)
    if endpoint is None:
        return False
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(body) > MAX_PAYLOAD_BYTES:
        return False
    from one_memory.http_client import create_client

    with create_client(timeout=timeout) as client:
        response = client.post(
            f"http://{endpoint.host}:{endpoint.port}{CAPTURE_ROUTE.format(source_id=source_id)}",
            content=body,
            headers={
                "Authorization": f"Bearer {endpoint.token}",
                "Content-Type": "application/json",
            },
        )
    return response.status_code == 202


def sdk_migration_capture_fenced(data_dir: Path) -> bool:
    """Ignore stale fences; a live migration or a failed one pauses hook writes.

    A failed upgrade migration leaves the fence `frozen` (no pid): the home was
    put back as it was before the upgrade and takes no writes until the next
    migration attempt, which replaces the fence and lifts it on success.
    """
    marker = data_dir / SDK_MIGRATION_CAPTURE_FENCE
    try:
        document = json.loads(marker.read_text(encoding="utf-8"))
        if isinstance(document, dict) and document.get("frozen") is True:
            return True
        pid = int(document["pid"])
        if pid < 1:
            raise ValueError("invalid migration fence pid")
        os.kill(pid, 0)
        return True
    except FileNotFoundError:
        return False
    except ProcessLookupError:
        marker.unlink(missing_ok=True)
        return False
    except PermissionError:
        return True
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError):
        marker.unlink(missing_ok=True)
        return False
