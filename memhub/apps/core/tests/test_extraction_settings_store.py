"""Extraction-window desired state belongs in config.json, not a SQL table."""

from __future__ import annotations

import json
import sqlite3

from one_memory.extraction_settings import ExtractionSettingsStore
from one_memory.memory_queue import ExtractionWindowPreset


def test_default_is_three_months(tmp_path) -> None:
    store = ExtractionSettingsStore(tmp_path / "s.sqlite3")
    assert store.get().window_preset == ExtractionWindowPreset.THREE_MONTHS


def test_set_then_get_round_trips(tmp_path) -> None:
    store = ExtractionSettingsStore(tmp_path / "s.sqlite3")
    store.set_window_preset(ExtractionWindowPreset.ALL)
    assert store.get().window_preset == ExtractionWindowPreset.ALL
    assert json.loads((tmp_path / "config.json").read_text())["extraction_window_preset"] == "all"
    assert not (tmp_path / "s.sqlite3").exists()


def test_setting_twice_updates_rather_than_duplicating(tmp_path) -> None:
    """The upsert must UPDATE the existing row, not fail or add a second one.

    This is the semantic difference that does not raise: if `ON DUPLICATE KEY
    UPDATE` were wrong, the setting would silently keep its old value and
    nothing would report an error.
    """
    store = ExtractionSettingsStore(tmp_path / "s.sqlite3")
    store.set_window_preset(ExtractionWindowPreset.ALL)
    store.set_window_preset(ExtractionWindowPreset.THREE_MONTHS)

    assert store.get().window_preset == ExtractionWindowPreset.THREE_MONTHS


def test_a_new_store_sees_the_persisted_value(tmp_path) -> None:
    """Survives a restart — the reason this lives apart from onboarding."""
    path = tmp_path / "s.sqlite3"
    ExtractionSettingsStore(path).set_window_preset(ExtractionWindowPreset.ALL)

    reopened = ExtractionSettingsStore(path)
    assert reopened.get().window_preset == ExtractionWindowPreset.ALL


def test_legacy_sql_value_moves_to_config_without_recreating_the_table(tmp_path) -> None:
    database = tmp_path / "s.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE extraction_settings "
            "(singleton INTEGER PRIMARY KEY, window_preset TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO extraction_settings VALUES (1,'1y')")

    store = ExtractionSettingsStore(database)

    assert store.get().window_preset == ExtractionWindowPreset.ONE_YEAR
    assert json.loads((tmp_path / "config.json").read_text())["extraction_window_preset"] == "1y"


def test_explicit_config_wins_over_legacy_sql_value(tmp_path) -> None:
    database = tmp_path / "s.sqlite3"
    (tmp_path / "config.json").write_text(
        '{"schema_version":1,"sources":{},'
        '"sedimentation_enabled":true,"extraction_window_preset":"6m"}'
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE extraction_settings "
            "(singleton INTEGER PRIMARY KEY, window_preset TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO extraction_settings VALUES (1,'all')")

    assert (
        ExtractionSettingsStore(database).get().window_preset == ExtractionWindowPreset.SIX_MONTHS
    )
