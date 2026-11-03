"""Core-owned durable user configuration.

The document stores desired state only. Runtime health is rebuilt from it and
is deliberately not written back into the user's configuration.
"""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Mapping
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from one_memory.memory_features import MemoryFeatures
from one_memory.memory_queue import ExtractionWindowPreset
from one_memory.ui_language import UI_LANGUAGES, UiLanguage

_SOURCE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,127}$")


class SourceConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    sync_enabled: StrictBool = False


#: The SDK pipeline mode (hy-memory `HyMemoryClient(mode=...)`):
#: lite keeps raw conversations only, with no model call; pro extracts
#: memories (System 1); ultra adds the knowledge graph and evolve (System 2).
MemoryMode = Literal["lite", "pro", "ultra"]
MEMORY_MODES: tuple[MemoryMode, ...] = ("lite", "pro", "ultra")
_SCHEDULE_TIME = re.compile(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")


class EvolveSchedule(BaseModel):
    """When ultra runs evolve for every user: once a day, local time."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    enabled: StrictBool = True
    #: `HH:MM`, 24-hour, the machine's local time.
    time: str = "03:00"

    @field_validator("time")
    @classmethod
    def valid_time(cls, value: str) -> str:
        if _SCHEDULE_TIME.fullmatch(value) is None:
            raise ValueError("invalid_evolve_schedule_time")
        return value


class ConfigDocument(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: int = Field(default=1, ge=1, le=1)
    sources: dict[str, SourceConfig] = Field(default_factory=dict)
    #: Whether the configured model may turn imported conversations into
    #: memories. Defaulting to on keeps every existing install doing what it
    #: did before the switch existed; stopping it is the user's cost control
    #: and leaves the model configuration itself untouched, so starting again
    #: needs no re-entry of a key.
    sedimentation_enabled: StrictBool = True
    extraction_window_preset: ExtractionWindowPreset = ExtractionWindowPreset.THREE_MONTHS
    #: The SDK mode every client is built with. `ONE_MEMORY_GRAPH_STORE` (dev)
    #: still forces ultra over it; see `hy_graph_admin.effective_memory_mode`.
    memory_mode: MemoryMode = "pro"
    evolve_schedule: EvolveSchedule = Field(default_factory=EvolveSchedule)
    #: SDK retrieval and extraction features a person set; an unset one keeps
    #: the SDK default. See `one_memory.memory_features`.
    memory_features: MemoryFeatures = Field(default_factory=MemoryFeatures)
    #: The display language the App shows (its choice, or the system language
    #: it follows); None = none set. See `one_memory.ui_language` for what it
    #: does to the SDK.
    ui_language: UiLanguage | None = None

    @field_validator("sources")
    @classmethod
    def valid_source_ids(cls, value: dict[str, SourceConfig]) -> dict[str, SourceConfig]:
        if any(_SOURCE_ID.fullmatch(source_id) is None for source_id in value):
            raise ValueError("invalid_source_id")
        return value

    @field_validator("extraction_window_preset", mode="before")
    @classmethod
    def valid_extraction_window_preset(cls, value: object) -> ExtractionWindowPreset:
        if isinstance(value, ExtractionWindowPreset):
            return value
        if isinstance(value, str):
            return ExtractionWindowPreset(value)
        raise ValueError("invalid_extraction_window_config")


class ConfigFileError(RuntimeError):
    pass


_WRITTEN_WHEN_SET = frozenset({"memory_mode", "evolve_schedule", "memory_features", "ui_language"})


class ConfigStore:
    """The only writer of ``<data_dir>/config.json`` inside Core."""

    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "config.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._document = self._read()

    def document(self) -> ConfigDocument:
        with self._lock:
            return self._document

    def sync_enabled(self, source_id: str) -> bool:
        if _SOURCE_ID.fullmatch(source_id) is None:
            return False
        with self._lock:
            source = self._document.sources.get(source_id)
            return bool(source and source.sync_enabled)

    def sedimentation_enabled(self) -> bool:
        with self._lock:
            return bool(self._document.sedimentation_enabled)

    def set_sedimentation_enabled(self, enabled: bool) -> ConfigDocument:
        if type(enabled) is not bool:
            raise ValueError("invalid_sedimentation_config")
        with self._lock:
            updated = self._document.model_copy(update={"sedimentation_enabled": enabled})
            self._write(updated)
            self._document = updated
            return updated

    def extraction_window_preset(self) -> ExtractionWindowPreset:
        with self._lock:
            return self._document.extraction_window_preset

    def set_extraction_window_preset(self, preset: ExtractionWindowPreset) -> ConfigDocument:
        if not isinstance(preset, ExtractionWindowPreset):
            raise ValueError("invalid_extraction_window_config")
        with self._lock:
            updated = self._document.model_copy(update={"extraction_window_preset": preset})
            self._write(updated)
            self._document = updated
            return updated

    def memory_mode(self) -> MemoryMode:
        with self._lock:
            return self._document.memory_mode

    def memory_mode_explicitly_configured(self) -> bool:
        """Whether a person chose the mode, including choosing the default."""
        with self._lock:
            return "memory_mode" in self._document.model_fields_set

    def set_memory_mode(self, mode: MemoryMode) -> ConfigDocument:
        if mode not in MEMORY_MODES:
            raise ValueError("invalid_memory_mode")
        with self._lock:
            updated = self._document.model_copy(update={"memory_mode": mode})
            self._write(updated)
            self._document = updated
            return updated

    def evolve_schedule(self) -> EvolveSchedule:
        with self._lock:
            return self._document.evolve_schedule

    def set_evolve_schedule(self, schedule: EvolveSchedule) -> ConfigDocument:
        if not isinstance(schedule, EvolveSchedule):
            raise ValueError("invalid_evolve_schedule")
        with self._lock:
            updated = self._document.model_copy(update={"evolve_schedule": schedule})
            self._write(updated)
            self._document = updated
            return updated

    def memory_features(self) -> MemoryFeatures:
        with self._lock:
            return self._document.memory_features

    def set_memory_features(self, features: MemoryFeatures) -> ConfigDocument:
        if not isinstance(features, MemoryFeatures):
            raise ValueError("invalid_memory_features")
        with self._lock:
            updated = self._document.model_copy(update={"memory_features": features})
            self._write(updated)
            self._document = updated
            return updated

    def ui_language(self) -> UiLanguage | None:
        with self._lock:
            return self._document.ui_language

    def set_ui_language(self, language: UiLanguage | None) -> ConfigDocument:
        if language is not None and language not in UI_LANGUAGES:
            raise ValueError("invalid_ui_language")
        with self._lock:
            updated = self._document.model_copy(update={"ui_language": language})
            self._write(updated)
            self._document = updated
            return updated

    def migrate_extraction_window_preset(self, preset: ExtractionWindowPreset) -> ConfigDocument:
        """Adopt the legacy SQL value only when config.json has no value."""
        if not isinstance(preset, ExtractionWindowPreset):
            raise ValueError("invalid_extraction_window_config")
        with self._lock:
            if "extraction_window_preset" in self._document.model_fields_set:
                return self._document
            updated = self._document.model_copy(update={"extraction_window_preset": preset})
            self._write(updated)
            self._document = updated
            return updated

    def set_sync(self, source_id: str, enabled: bool) -> ConfigDocument:
        return self.set_sync_many({source_id: enabled})

    def set_sync_many(self, updates: Mapping[str, bool]) -> ConfigDocument:
        if not updates or any(
            _SOURCE_ID.fullmatch(source_id) is None or type(enabled) is not bool
            for source_id, enabled in updates.items()
        ):
            raise ValueError("invalid_source_sync_config")
        with self._lock:
            sources = dict(self._document.sources)
            for source_id, enabled in updates.items():
                sources[source_id] = SourceConfig(sync_enabled=enabled)
            updated = self._document.model_copy(update={"sources": sources})
            self._write(updated)
            self._document = updated
            return updated

    def _read(self) -> ConfigDocument:
        if not self.path.exists():
            return ConfigDocument()
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                # Builds through 1.0.2 persisted this global consent bit.
                # The embedded SDK is now trusted to use all local memory, so
                # accept existing files while discarding the retired setting.
                value.pop("model_content_allowed", None)
            return ConfigDocument.model_validate(value)
        except (OSError, ValueError) as error:
            raise ConfigFileError("config_invalid") from error

    def _write(self, document: ConfigDocument) -> None:
        # Settings added after 1.0.5 are written only once a person chose
        # them, so an untouched install's file stays what older builds read.
        unset = {name for name in _WRITTEN_WHEN_SET if name not in document.model_fields_set}
        values = document.model_dump(mode="json", exclude=unset)
        if "memory_features" in values:
            # Only the features a person set; an absent one is the SDK default.
            values["memory_features"] = document.memory_features.model_dump(exclude_none=True)
        payload = (
            json.dumps(
                values,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        temporary: Path | None = None
        try:
            with NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=".config.",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError as error:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            raise ConfigFileError("config_write_failed") from error
