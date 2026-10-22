"""Persisted extraction-window choice stored in Core-owned config.json."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from one_memory.database_access import main_database
from one_memory.memory_queue import ExtractionWindowPreset
from one_memory.memory_server.config_state import ConfigStore


class ExtractionSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # Left at THREE_MONTHS deliberately. Widening this to ALL makes the
    # deferral mechanism a no-op under the default config, which is a product
    # call about first-run cost, not part of the timeout fix.
    window_preset: ExtractionWindowPreset = ExtractionWindowPreset.THREE_MONTHS


class ExtractionSettingsStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self._config = ConfigStore(database_path.parent)
        self._adopt_legacy_value()

    def get(self) -> ExtractionSettings:
        return ExtractionSettings(window_preset=self._config.extraction_window_preset())

    def set_window_preset(self, preset: ExtractionWindowPreset) -> ExtractionSettings:
        self._config.set_extraction_window_preset(preset)
        return self.get()

    def _adopt_legacy_value(self) -> None:
        """Move the old singleton into config without creating its table."""
        if not self.database_path.exists():
            return
        try:
            with main_database(self.database_path).read() as connection:
                row = connection.execute(
                    "SELECT window_preset FROM extraction_settings WHERE singleton=1"
                ).fetchone()
        except sqlite3.DatabaseError:
            return
        if row is None:
            return
        try:
            preset = ExtractionWindowPreset(str(row[0]))
        except ValueError:
            return
        self._config.migrate_extraction_window_preset(preset)
