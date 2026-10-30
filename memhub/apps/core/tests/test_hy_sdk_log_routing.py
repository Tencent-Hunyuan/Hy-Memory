"""HY SDK text logs go to hy_memory.log only, with warnings also in core.log.

The SDK's first client adds a stdout and a stderr stream to the "hy_memory"
logger and stops it propagating. Under launchd those streams are
service.log and service-error.log, so each SDK line used to be written
twice and none reached core.log.
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import SimpleNamespace

import pytest

from one_memory import hy_sdk_runtime
from one_memory.log_file import (
    HY_SDK_LOGGER,
    confine_hy_sdk_logging,
    log_path,
    start_logging_to_file,
)


@pytest.fixture
def sdk_logger():
    """The "hy_memory" logger as HY's setup_logging leaves it, restored after."""
    sdk = logging.getLogger(HY_SDK_LOGGER)
    root = logging.getLogger()
    saved = (list(sdk.handlers), sdk.propagate, sdk.level, list(root.handlers), root.level)
    yield sdk
    for handler in sdk.handlers:
        if handler not in saved[0]:
            handler.close()
    for handler in root.handlers:
        if handler not in saved[3]:
            handler.close()
    sdk.handlers, sdk.propagate, root.handlers = saved[0], saved[1], saved[3]
    sdk.setLevel(saved[2])
    root.setLevel(saved[4])


def _configure_like_hy(sdk: logging.Logger, log_dir: Path) -> logging.FileHandler:
    log_dir.mkdir(parents=True, exist_ok=True)
    text_log = logging.FileHandler(log_dir / "hy_memory.log", encoding="utf-8")
    stdout = logging.StreamHandler(sys.stdout)
    stderr = logging.StreamHandler(sys.stderr)
    stderr.setLevel(logging.WARNING)
    sdk.handlers = [text_log, stdout, stderr]
    sdk.propagate = False
    sdk.setLevel(logging.INFO)
    return text_log


def test_sdk_lines_stay_in_hy_memory_log_and_warnings_reach_core_log(
    tmp_path: Path, sdk_logger: logging.Logger
) -> None:
    start_logging_to_file(tmp_path)
    text_log = _configure_like_hy(sdk_logger, tmp_path / "hy")

    confine_hy_sdk_logging()
    confine_hy_sdk_logging()

    assert not [
        handler
        for handler in sdk_logger.handlers
        if isinstance(handler, logging.StreamHandler)
        and not isinstance(handler, logging.FileHandler)
    ]
    assert text_log in sdk_logger.handlers
    assert len(sdk_logger.handlers) == 2, "the core.log forwarder is added once"

    child = logging.getLogger("hy_memory.client")
    child.info("[add] routine sdk line")
    child.warning("[add] sdk problem")
    for handler in logging.getLogger().handlers:
        handler.flush()
    text_log.flush()

    hy_text = (tmp_path / "hy" / "hy_memory.log").read_text(encoding="utf-8")
    core_text = log_path(tmp_path).read_text(encoding="utf-8")
    assert "routine sdk line" in hy_text and "sdk problem" in hy_text
    assert "sdk problem" in core_text
    assert "routine sdk line" not in core_text
    assert core_text.count("sdk problem") == 1


def test_nothing_changes_before_hy_has_configured_its_logger(
    tmp_path: Path, sdk_logger: logging.Logger
) -> None:
    start_logging_to_file(tmp_path)
    sdk_logger.handlers = []
    sdk_logger.propagate = True

    confine_hy_sdk_logging()

    assert sdk_logger.handlers == []


def test_without_core_log_the_streams_are_still_dropped(
    tmp_path: Path, sdk_logger: logging.Logger
) -> None:
    logging.getLogger().handlers = [
        handler
        for handler in logging.getLogger().handlers
        if not isinstance(handler, RotatingFileHandler)
    ]
    text_log = _configure_like_hy(sdk_logger, tmp_path / "hy")

    confine_hy_sdk_logging()

    assert sdk_logger.handlers == [text_log]


@pytest.mark.parametrize("fails", [False, True])
def test_every_client_construction_confines_the_sdk_log(
    tmp_path: Path, monkeypatch, fails: bool
) -> None:
    order: list[str] = []

    class Client:
        def __init__(self, **_options):
            order.append("client")
            if fails:
                raise RuntimeError("construction failed")

    monkeypatch.setattr(hy_sdk_runtime, "HyMemoryClient", Client)
    monkeypatch.setattr(hy_sdk_runtime, "build_sdk_config", lambda *_a, **_k: SimpleNamespace())
    monkeypatch.setattr(hy_sdk_runtime, "inspect_graph_width_mismatch", lambda _c: None)
    monkeypatch.setattr(hy_sdk_runtime, "_acquire_runtime", lambda _c, _k: object())
    monkeypatch.setattr(hy_sdk_runtime, "_release_runtime", lambda _k: None)
    monkeypatch.setattr(hy_sdk_runtime, "confine_hy_sdk_logging", lambda: order.append("confine"))
    profile = SimpleNamespace(embedding_provider_kind=None, provider_kind=None)

    if fails:
        with pytest.raises(RuntimeError):
            hy_sdk_runtime._acquire_client(
                profile, api_key=None, data_dir=tmp_path, http_transport_factory=None
            )
    else:
        hy_sdk_runtime._acquire_client(
            profile, api_key=None, data_dir=tmp_path, http_transport_factory=None
        )

    assert order == ["client", "confine"]
