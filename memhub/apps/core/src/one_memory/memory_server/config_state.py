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

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from one_memory.memory_queue import ExtractionWindowPreset

_SOURCE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,127}$")


class SourceConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    sync_enabled: StrictBool = False


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
        payload = (
            json.dumps(
                document.model_dump(mode="json"),
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
