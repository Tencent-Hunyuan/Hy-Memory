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
