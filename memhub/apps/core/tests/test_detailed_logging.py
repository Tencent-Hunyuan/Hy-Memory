import json
import logging
from importlib import import_module
from pathlib import Path

from one_memory.hy_runtime_config import (
    configure_hy_runtime_environment,
    detailed_logging_enabled,
    diagnostic_log_level,
    set_detailed_logging,
    set_diagnostic_log_level,
)


def test_detailed_pipeline_logging_defaults_off_and_survives_restart(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("MEMORY_PIPELINE_TRACE_ENABLED", raising=False)
    assert detailed_logging_enabled(tmp_path) is False
    configure_hy_runtime_environment(tmp_path)
    assert __import__("os").environ["MEMORY_PIPELINE_TRACE_ENABLED"] == "false"
    assert diagnostic_log_level(tmp_path) == "info"

    assert set_detailed_logging(tmp_path, True) is True
    assert detailed_logging_enabled(tmp_path) is True
    monkeypatch.delenv("MEMORY_PIPELINE_TRACE_ENABLED", raising=False)
    configure_hy_runtime_environment(tmp_path)
    assert __import__("os").environ["MEMORY_PIPELINE_TRACE_ENABLED"] == "true"


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
    assert __import__("os").environ["MEMORY_PIPELINE_TRACE_ENABLED"] == "false"
    assert __import__("os").environ["MEMORY_LOG_LLM_CONTENT"] == "false"


def test_live_debug_switch_makes_sdk_pipeline_writer_keep_original_content(
    tmp_path: Path,
) -> None:
    writer_type = import_module("hy_memory.utils.pipeline_log_writer").PipelineLogWriter
    writer = writer_type(str(tmp_path / "pipeline"))

    set_diagnostic_log_level(tmp_path, "info")
    writer.write_step(
        request_id="info",
        step="EXTRACT",
        prompt="info prompt",
        response="info response",
    )
    set_diagnostic_log_level(tmp_path, "debug")
    writer.write_step(
        request_id="debug",
        step="EXTRACT",
        prompt="debug prompt",
        response="debug response",
    )

    lines = [
        json.loads(line)
        for target in (tmp_path / "pipeline").rglob("*.log")
        for line in target.read_text(encoding="utf-8").splitlines()
    ]
    info, debug = lines
    assert info["prompt"].startswith("<redacted len=")
    assert info["content"].startswith("<redacted len=")
    assert debug["prompt"] == "debug prompt"
    assert debug["content"] == "debug response"


def test_log_level_document_migrates_the_old_boolean_shape(tmp_path: Path) -> None:
    target = tmp_path / "settings" / "diagnostic-logging.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"schema_version": 1, "enabled": True}))
    assert diagnostic_log_level(tmp_path) == "debug"

    target.write_text(json.dumps({"schema_version": 1, "enabled": False}))
    assert diagnostic_log_level(tmp_path) == "info"

    assert set_diagnostic_log_level(tmp_path, "error") == "error"
    assert json.loads(target.read_text()) == {"schema_version": 2, "level": "error"}


def test_configure_environment_passes_debug_content_settings_to_sdk(tmp_path: Path) -> None:
    set_diagnostic_log_level(tmp_path, "debug")
    configure_hy_runtime_environment(tmp_path)

    environment = __import__("os").environ
    assert environment["MEMORY_LOG_LEVEL"] == "DEBUG"
    assert environment["MEMORY_PIPELINE_TRACE_ENABLED"] == "true"
    assert environment["MEMORY_LOG_LLM_CONTENT"] == "true"
