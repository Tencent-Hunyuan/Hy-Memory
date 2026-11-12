import errno
import io

import pytest

from one_memory import cli
from one_memory.runtime_client import RuntimeStatus


def test_port_in_use_by_runtime_names_the_runtime() -> None:
    status = RuntimeStatus(
        state="running",
        code="runtime_verified",
        instance_id="inst-377",
        host="127.0.0.1",
        port=8765,
        version="1.0.4",
    )
    code, message = cli._port_in_use_explanation(status)
    assert code == "core_port_in_use_by_runtime"
    assert "8765" in message
    assert "1.0.4" in message


def test_port_in_use_by_other_process_keeps_the_generic_code() -> None:
    for status in (
        RuntimeStatus(state="stopped", code="runtime_process_not_running"),
        RuntimeStatus(state="untrusted", code="runtime_receipt_invalid"),
        RuntimeStatus(state="unhealthy", code="runtime_identity_unreachable"),
    ):
        code, _message = cli._port_in_use_explanation(status)
        assert code == "core_port_in_use"


def test_refused_bind_exits_structured_when_the_port_holder_is_foreign(
    monkeypatch, tmp_path
) -> None:
    """A refused bind is an exit with a code, not a crash (#377).

    The receipt names a dead process, so the operator is told the port is held
    by something else -- exactly the reporter's state after the old Runtime
    died with its files left behind.
    """
    monkeypatch.setattr(
        cli,
        "inspect_runtime",
        lambda _data_dir: RuntimeStatus(state="stopped", code="runtime_process_not_running"),
    )
    stream = io.StringIO()

    with pytest.raises(SystemExit) as exited:
        cli._serve_forever_refused(
            OSError(errno.EADDRINUSE, "Address already in use"),
            settings_port=8765,
            data_dir=tmp_path,
            stderr=stream,
        )
    assert exited.value.code == cli.PORT_IN_USE_EXIT
    output = stream.getvalue()
    assert "core_port_in_use" in output
    assert "Traceback" not in output


def test_refused_bind_blames_this_homes_runtime_when_the_receipt_proves_one(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(
        cli,
        "inspect_runtime",
        lambda _data_dir: RuntimeStatus(
            state="running",
            code="runtime_verified",
            instance_id="inst-377",
            host="127.0.0.1",
            port=8765,
            version="1.0.4",
        ),
    )
    stream = io.StringIO()

    with pytest.raises(SystemExit) as exited:
        cli._serve_forever_refused(
            OSError(errno.EADDRINUSE, "Address already in use"),
            settings_port=8765,
            data_dir=tmp_path,
            stderr=stream,
        )
    assert exited.value.code == cli.PORT_IN_USE_EXIT
    output = stream.getvalue()
    assert "core_port_in_use_by_runtime" in output
    assert "1.0.4" in output


def test_refused_bind_reraises_other_os_errors(tmp_path) -> None:
    error = OSError(errno.EACCES, "Permission denied")
    with pytest.raises(OSError) as reraised:
        cli._serve_forever_refused(
            error, settings_port=8765, data_dir=tmp_path, stderr=io.StringIO()
        )
    assert reraised.value is error


def test_serve_exits_named_when_a_foreign_process_holds_the_port(tmp_path) -> None:
    """serve() against a really occupied port exits refused, not crashed.

    The home has no receipt, so the refusal is attributed to a process that
    is not this home's Core: the operator gets the code and the lever
    (release the port), never uvicorn's internal SystemExit(3).
    """
    import os
    import socket
    import subprocess
    import sys

    holder = socket.socket()
    try:
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        port = int(holder.getsockname()[1])
        process = subprocess.Popen(
            [sys.executable, "-m", "one_memory.cli", "serve"],
            env={
                **os.environ,
                "ONE_MEMORY_DATA_DIR": str(tmp_path),
                "ONE_MEMORY_PORT": str(port),
                "ONE_MEMORY_INSTANCE_ID": "port-in-use-e2e",
            },
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            _stdout, stderr = process.communicate(timeout=300)
        except subprocess.TimeoutExpired as expired:
            process.kill()
            _stdout, stderr = process.communicate(timeout=30)
            raise AssertionError("serve did not exit while the port was held") from expired
    finally:
        holder.close()

    assert process.returncode == cli.PORT_IN_USE_EXIT, stderr
    assert "core_port_in_use" in stderr
    assert "Traceback" not in stderr


def test_serve_exits_named_when_this_homes_runtime_holds_the_port(tmp_path) -> None:
    """The #377 state: a live Core of this home owns the port (#377).

    A second Core must refuse the bind and name the holder -- the running
    Core's receipt, with its version -- instead of crashing on the bind, and
    without deleting or rewriting the first Core's state.
    """
    import os
    import subprocess
    import sys
    import time

    import httpx

    def start_core(
        port: int, instance_id: str, stderr_target: object = subprocess.DEVNULL
    ) -> subprocess.Popen:
        return subprocess.Popen(
            [sys.executable, "-m", "one_memory.cli"],
            env={
                **os.environ,
                "ONE_MEMORY_DATA_DIR": str(tmp_path),
                "ONE_MEMORY_PORT": str(port),
                "ONE_MEMORY_INSTANCE_ID": instance_id,
            },
            stdout=subprocess.DEVNULL,
            stderr=stderr_target,
            text=True,
        )

    def free_port() -> int:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            return int(probe.getsockname()[1])

    import socket

    port = free_port()
    first = start_core(port, "port-in-use-holder")
    try:
        health = None
        for _ in range(100):
            if first.poll() is not None:
                raise AssertionError("holder Core exited before serving")
            try:
                health = httpx.get(f"http://127.0.0.1:{port}/api/v1/health", timeout=0.2).json()
                break
            except httpx.HTTPError:
                time.sleep(0.1)
        assert health is not None, "holder Core never became healthy"

        second = start_core(port, "port-in-use-second", stderr_target=subprocess.PIPE)
        try:
            _stdout, stderr = second.communicate(timeout=300)
        except subprocess.TimeoutExpired as expired:
            second.kill()
            _stdout, stderr = second.communicate(timeout=30)
            raise AssertionError(
                "second Core did not exit while the first held the port"
            ) from expired
    finally:
        first.terminate()
        try:
            first.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            first.kill()
            first.communicate(timeout=30)

    assert second.returncode == cli.PORT_IN_USE_EXIT, stderr
    assert "core_port_in_use_by_runtime" in stderr
    assert "Traceback" not in stderr
