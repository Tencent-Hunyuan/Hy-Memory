"""Pinned public HY Memory SDK identity and One Memory's local runtime policy.

The embedded runtime remains local-first and explicitly owns its Chroma, Kuzu,
SQLite, credentials, and permission boundaries.
"""

from __future__ import annotations

import json
import logging
import os
from importlib import metadata
from pathlib import Path
from typing import Literal, cast

HY_MEMORY_DISTRIBUTION = "hy-memory"
HY_MEMORY_VERSION = "2.0.8"
HY_MEMORY_NAMESPACE = "governed-v2"
DiagnosticLogLevel = Literal["error", "warning", "info", "debug"]

_LOG_LEVELS: dict[DiagnosticLogLevel, int] = {
    "error": logging.ERROR,
    "warning": logging.WARNING,
    "info": logging.INFO,
    "debug": logging.DEBUG,
}
_COLLECTION_LOGGERS = (
    "one_memory",
    "hy_memory",
    "openai",
    "httpx",
    "httpcore",
)
_NOISY_LIBRARY_LOGGERS = ("httpx", "httpcore", "watchfiles", "jieba")

HY_MEMORY_READER = "hybrid"


def detailed_logging_enabled(data_dir: Path) -> bool:
    return diagnostic_log_level(data_dir) == "debug"


def diagnostic_log_level(data_dir: Path) -> DiagnosticLogLevel:
    target = data_dir / "settings" / "diagnostic-logging.json"
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return "info"
    # Read the old boolean document written by builds before level selection.
    if value == {"schema_version": 1, "enabled": True}:
        return "debug"
    if value == {"schema_version": 1, "enabled": False}:
        return "info"
    if (
        isinstance(value, dict)
        and value.get("schema_version") == 2
        and value.get("level") in _LOG_LEVELS
    ):
        return cast(DiagnosticLogLevel, value["level"])
    return "info"


def set_detailed_logging(data_dir: Path, enabled: bool) -> bool:
    set_diagnostic_log_level(data_dir, "debug" if enabled else "info")
    return enabled


def set_diagnostic_log_level(
    data_dir: Path,
    level: DiagnosticLogLevel,
) -> DiagnosticLogLevel:
    if level not in _LOG_LEVELS:
        raise ValueError("diagnostic_log_level_invalid")
    target = data_dir / "settings" / "diagnostic-logging.json"
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"schema_version": 2, "level": level}, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    temporary.replace(target)
    apply_diagnostic_log_level(level)
    return level


def apply_diagnostic_log_level(level: DiagnosticLogLevel) -> None:
    """Apply the selected collection level without rewriting log content."""

    if level not in _LOG_LEVELS:
        raise ValueError("diagnostic_log_level_invalid")
    selected = _LOG_LEVELS[level]
    logging.getLogger().setLevel(selected)
    for name in _COLLECTION_LOGGERS:
        logging.getLogger(name).setLevel(selected)

    # Imported libraries may have assigned levels to individual modules. Reset
    # the complete call chain so a live level change takes effect immediately.
    for name in tuple(logging.Logger.manager.loggerDict):
        if any(name == root or name.startswith(f"{root}.") for root in _COLLECTION_LOGGERS):
            logging.getLogger(name).setLevel(selected)
    # Successful HTTP exchanges, file-watch notifications and tokenizer
    # initialization are useful only while actively debugging. At INFO they
    # dominated core.log and the in-app panel without describing product work.
    noisy_level = selected if selected == logging.DEBUG else max(selected, logging.WARNING)
    for root in _NOISY_LIBRARY_LOGGERS:
        logging.getLogger(root).setLevel(noisy_level)
    for name in tuple(logging.Logger.manager.loggerDict):
        if any(name == root or name.startswith(f"{root}.") for root in _NOISY_LIBRARY_LOGGERS):
            logging.getLogger(name).setLevel(noisy_level)

    # HY configures its text log once, on the first client, at the level of
    # that moment. Its file handler must follow later changes too, or a live
    # switch to debug never reaches hy_memory.log.
    for handler in logging.getLogger("hy_memory").handlers:
        if isinstance(handler, logging.FileHandler):
            handler.setLevel(selected)

    os.environ["MEMORY_LOG_LEVEL"] = logging.getLevelName(selected)
    _apply_hy_pipeline_policy()
    os.environ["MEMORY_LOG_LLM_CONTENT"] = "true" if level == "debug" else "false"


def _apply_hy_pipeline_policy() -> None:
    """Keep HY's per-step pipeline log and trace off at every level.

    Nothing in MemHub reads them: Core's own diagnostics carry the step
    timing, and the JSONL/SQLite copies only grew on disk. With both off
    the SDK skips the per-step bookkeeping altogether.
    """

    os.environ["MEMORY_PIPELINE_LOG_ENABLED"] = "false"
    os.environ["MEMORY_PIPELINE_TRACE_ENABLED"] = "false"


def hy_memory_version() -> str:
    """Return the installed SDK version, failing if the pinned dist is absent."""

    return metadata.version(HY_MEMORY_DISTRIBUTION)


def configure_hy_runtime_environment(data_dir: Path) -> None:
    """Apply the audited local policy before importing HY runtime modules."""

    memory_root = (data_dir / "hy-memory" / HY_MEMORY_NAMESPACE).resolve()
    log_dir = memory_root / "logs"
    memory_root.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    os.environ["MEMORY_DATA_DIR"] = str(memory_root)
    os.environ["MEMORY_LOG_DIR"] = str(log_dir)
    level = diagnostic_log_level(data_dir)
    os.environ["MEMORY_LOG_LEVEL"] = level.upper()
    _apply_hy_pipeline_policy()
    # Still meaningful with the pipeline log off: it decides whether HY's
    # DEBUG lines in hy_memory.log carry full model prompts and responses.
    os.environ["MEMORY_LOG_LLM_CONTENT"] = "true" if level == "debug" else "false"
    os.environ["MEMORY_MEMORY_OPERATIONS_ENABLED"] = "false"
    os.environ["ANONYMIZED_TELEMETRY"] = "False"
    os.environ.pop("CHROMA_OTEL_COLLECTION_ENDPOINT", None)
