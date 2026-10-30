import json
import logging
from pathlib import Path

from one_memory.hy_runtime_config import (
    configure_hy_runtime_environment,
    detailed_logging_enabled,
    diagnostic_log_level,
    set_detailed_logging,
    set_diagnostic_log_level,
)


def test_detailed_logging_defaults_off_and_survives_restart(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("MEMORY_LOG_LEVEL", raising=False)
    assert detailed_logging_enabled(tmp_path) is False
    configure_hy_runtime_environment(tmp_path)
    assert __import__("os").environ["MEMORY_LOG_LEVEL"] == "INFO"
    assert diagnostic_log_level(tmp_path) == "info"

    assert set_detailed_logging(tmp_path, True) is True
    assert detailed_logging_enabled(tmp_path) is True
    monkeypatch.delenv("MEMORY_LOG_LEVEL", raising=False)
    configure_hy_runtime_environment(tmp_path)
    assert __import__("os").environ["MEMORY_LOG_LEVEL"] == "DEBUG"


def test_hy_pipeline_log_and_trace_stay_off_at_every_level(tmp_path: Path, monkeypatch) -> None:
    environment = __import__("os").environ
    for level in ("error", "warning", "info", "debug"):
        for name in ("MEMORY_PIPELINE_LOG_ENABLED", "MEMORY_PIPELINE_TRACE_ENABLED"):
            monkeypatch.setenv(name, "true")
        set_diagnostic_log_level(tmp_path, level)
        assert environment["MEMORY_PIPELINE_LOG_ENABLED"] == "false"
        assert environment["MEMORY_PIPELINE_TRACE_ENABLED"] == "false"

        for name in ("MEMORY_PIPELINE_LOG_ENABLED", "MEMORY_PIPELINE_TRACE_ENABLED"):
            monkeypatch.setenv(name, "true")
        configure_hy_runtime_environment(tmp_path)
        assert environment["MEMORY_PIPELINE_LOG_ENABLED"] == "false"
        assert environment["MEMORY_PIPELINE_TRACE_ENABLED"] == "false"


def test_live_level_change_reaches_the_hy_text_log_file(tmp_path: Path) -> None:
    sdk = logging.getLogger("hy_memory")
    text_log = logging.FileHandler(tmp_path / "hy_memory.log", encoding="utf-8")
    text_log.setLevel(logging.INFO)
    sdk.addHandler(text_log)
    try:
        set_diagnostic_log_level(tmp_path, "debug")
        assert text_log.level == logging.DEBUG
        set_diagnostic_log_level(tmp_path, "warning")
        assert text_log.level == logging.WARNING
    finally:
        sdk.removeHandler(text_log)
        text_log.close()
        set_diagnostic_log_level(tmp_path, "info")


def test_live_log_level_applies_to_full_hy_and_provider_call_chain(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("MEMORY_LOG_LLM_CONTENT", raising=False)
    set_diagnostic_log_level(tmp_path, "debug")

    assert diagnostic_log_level(tmp_path) == "debug"
    assert logging.getLogger("one_memory.sdk_write_processor").isEnabledFor(logging.DEBUG)
    assert logging.getLogger("hy_memory.runtime").isEnabledFor(logging.DEBUG)
    assert logging.getLogger("hy_memory.client").isEnabledFor(logging.DEBUG)
    assert logging.getLogger("hy_memory.agent.llm_provider").isEnabledFor(logging.DEBUG)
    assert logging.getLogger("hy_memory.agent.extractor").isEnabledFor(logging.DEBUG)
    assert logging.getLogger("hy_memory.agent.reconciler").isEnabledFor(logging.DEBUG)
    assert logging.getLogger("openai._base_client").isEnabledFor(logging.DEBUG)
    assert logging.getLogger("httpcore.http11").isEnabledFor(logging.DEBUG)
    assert __import__("os").environ["MEMORY_LOG_LEVEL"] == "DEBUG"
    assert __import__("os").environ["MEMORY_LOG_LLM_CONTENT"] == "true"

    assert set_diagnostic_log_level(tmp_path, "warning") == "warning"
    assert not logging.getLogger("hy_memory.runtime").isEnabledFor(logging.INFO)


def test_info_collects_hy_info_without_enabling_debug_pipeline_content(
    tmp_path: Path,
) -> None:
    set_diagnostic_log_level(tmp_path, "info")

    assert logging.getLogger("hy_memory.client").isEnabledFor(logging.INFO)
    assert not logging.getLogger("hy_memory.client").isEnabledFor(logging.DEBUG)
    assert logging.getLogger("openai._base_client").isEnabledFor(logging.INFO)
    assert not logging.getLogger("openai._base_client").isEnabledFor(logging.DEBUG)
    assert __import__("os").environ["MEMORY_LOG_LEVEL"] == "INFO"
    assert __import__("os").environ["MEMORY_LOG_LLM_CONTENT"] == "false"


def test_log_level_document_migrates_the_old_boolean_shape(tmp_path: Path) -> None:
    target = tmp_path / "settings" / "diagnostic-logging.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"schema_version": 1, "enabled": True}))
    assert diagnostic_log_level(tmp_path) == "debug"

    target.write_text(json.dumps({"schema_version": 1, "enabled": False}))
    assert diagnostic_log_level(tmp_path) == "info"

    assert set_diagnostic_log_level(tmp_path, "error") == "error"
    assert json.loads(target.read_text()) == {"schema_version": 2, "level": "error"}


def test_configure_environment_passes_debug_content_settings_to_sdk(
    tmp_path: Path, monkeypatch
) -> None:
    for name in (
        "MEMORY_DATA_DIR",
        "MEMORY_LOG_DIR",
        "MEMORY_LOG_LEVEL",
        "MEMORY_PIPELINE_LOG_ENABLED",
        "MEMORY_PIPELINE_TRACE_ENABLED",
        "MEMORY_LOG_LLM_CONTENT",
    ):
        monkeypatch.delenv(name, raising=False)
    set_diagnostic_log_level(tmp_path, "debug")
    configure_hy_runtime_environment(tmp_path)

    environment = __import__("os").environ
    memory_root = tmp_path / "hy-memory" / "governed-v2"
    assert environment["MEMORY_DATA_DIR"] == str(memory_root)
    assert environment["MEMORY_LOG_DIR"] == str(memory_root / "logs")
    assert environment["MEMORY_LOG_LEVEL"] == "DEBUG"
    assert environment["MEMORY_PIPELINE_LOG_ENABLED"] == "false"
    assert environment["MEMORY_PIPELINE_TRACE_ENABLED"] == "false"
    assert environment["MEMORY_LOG_LLM_CONTENT"] == "true"
