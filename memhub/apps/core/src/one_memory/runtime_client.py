from __future__ import annotations

import json
import os
import stat
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

import httpx

from one_memory.http_client import create_client
from one_memory.runtime import process_is_alive

RuntimeState = Literal["running", "stopped", "unhealthy", "untrusted"]
IdentityProbe = Callable[[str, int, str], Mapping[str, object]]


@dataclass(frozen=True)
class RuntimeStatus:
    state: RuntimeState
    code: str
    instance_id: str | None = None
    host: str | None = None
    port: int | None = None
    version: str | None = None

    def json_value(self) -> dict[str, object]:
        return {key: value for key, value in asdict(self).items() if value is not None}


@dataclass(frozen=True)
class RuntimeReceipt:
    instance_id: str
    pid: int
    parent_pid: int | None
    host: Literal["127.0.0.1"]
    port: int


def inspect_runtime(
    data_dir: Path,
    *,
    identity_probe: IdentityProbe | None = None,
    alive: Callable[[int], bool] = process_is_alive,
) -> RuntimeStatus:
    receipt_path = data_dir / "runtime.json"
    try:
        raw = receipt_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return RuntimeStatus("stopped", "runtime_receipt_missing")
    except OSError:
        return RuntimeStatus("untrusted", "runtime_receipt_unreadable")

    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, UnicodeError):
        return RuntimeStatus("untrusted", "runtime_receipt_invalid")
    receipt = _receipt(parsed)
    if receipt is None:
        return RuntimeStatus("untrusted", "runtime_receipt_invalid")
    if not alive(receipt.pid):
        return RuntimeStatus(
            "stopped",
            "runtime_process_not_running",
            instance_id=receipt.instance_id,
            host=receipt.host,
            port=receipt.port,
        )

    token = _runtime_token(data_dir)
    if token is None:
        return RuntimeStatus(
            "untrusted",
            "runtime_token_unavailable",
            instance_id=receipt.instance_id,
            host=receipt.host,
            port=receipt.port,
        )

    probe = identity_probe or _probe_identity
    try:
        identity = probe(receipt.host, receipt.port, token)
    except (httpx.HTTPError, OSError, ValueError):
        return RuntimeStatus(
            "unhealthy",
            "runtime_identity_unreachable",
            instance_id=receipt.instance_id,
            host=receipt.host,
            port=receipt.port,
        )
    if (
        identity.get("status") != "ok"
        or identity.get("service") != "one-memory-core"
        or identity.get("instance_id") != receipt.instance_id
    ):
        return RuntimeStatus(
            "untrusted",
            "runtime_identity_mismatch",
            instance_id=receipt.instance_id,
            host=receipt.host,
            port=receipt.port,
        )
    version = identity.get("version")
    return RuntimeStatus(
        "running",
        "runtime_verified",
        instance_id=receipt.instance_id,
        host=receipt.host,
        port=receipt.port,
        version=version if isinstance(version, str) else None,
    )


@dataclass(frozen=True)
class CoreEndpoint:
    host: Literal["127.0.0.1"]
    port: int
    token: str


def core_endpoint(
    data_dir: Path,
    *,
    alive: Callable[[int], bool] = process_is_alive,
) -> CoreEndpoint | None:
    """Where this home's Core listens and the token it accepts, from files alone.

    The receipt and token checks `inspect_runtime` makes, without its identity
    round trip: a caller that only has to hand something over quickly lets the
    authenticated request itself be the proof. None when no live Core is named.
    """
    try:
        raw = (data_dir / "runtime.json").read_text(encoding="utf-8")
        receipt = _receipt(json.loads(raw))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if receipt is None or not alive(receipt.pid):
        return None
    token = _runtime_token(data_dir)
    if token is None:
        return None
    return CoreEndpoint(host=receipt.host, port=receipt.port, token=token)


def _receipt(value: object) -> RuntimeReceipt | None:
    if not isinstance(value, dict):
        return None
    if value.get("schema_version") != 1 or value.get("service") != "one-memory-core":
        return None
    instance_id = value.get("instance_id")
    pid = value.get("pid")
    parent_pid = value.get("parent_pid")
    host = value.get("host")
    port = value.get("port")
    if (
        not isinstance(instance_id, str)
        or not instance_id
        or not isinstance(pid, int)
        or isinstance(pid, bool)
        or pid <= 1
        or (parent_pid is not None and (not isinstance(parent_pid, int) or parent_pid <= 1))
        or host != "127.0.0.1"
        or not isinstance(port, int)
        or isinstance(port, bool)
        or not 1024 <= port <= 65_535
    ):
        return None
    return RuntimeReceipt(
        instance_id=instance_id,
        pid=pid,
        parent_pid=parent_pid,
        host="127.0.0.1",
        port=port,
    )


def _runtime_token(data_dir: Path) -> str | None:
    if os.name != "posix":
        return None
    target = data_dir / "runtime-token"
    try:
        metadata = target.lstat()
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            return None
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            return None
        if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
            return None
        token = target.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    return token if 0 < len(token) <= 4096 else None


def _probe_identity(host: str, port: int, token: str) -> Mapping[str, object]:
    with create_client(timeout=2.0) as client:
        response = client.get(
            f"http://{host}:{port}/api/v1/runtime/identity",
            headers={"Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        value = response.json()
    if not isinstance(value, dict):
        raise ValueError("runtime_identity_invalid")
    return value
