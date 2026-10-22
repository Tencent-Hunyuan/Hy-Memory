import json

import pytest

from one_memory.memory_server.config_state import ConfigFileError, ConfigStore


def test_sync_config_defaults_off_and_survives_restart(tmp_path):
    store = ConfigStore(tmp_path)
    assert not store.sync_enabled("cursor")

    store.set_sync("cursor", True)

    saved = json.loads((tmp_path / "config.json").read_text())
    assert saved == {
        "extraction_window_preset": "3m",
        "schema_version": 1,
        "sedimentation_enabled": True,
        "sources": {"cursor": {"sync_enabled": True}},
    }
    assert ConfigStore(tmp_path).sync_enabled("cursor")


def test_sync_config_updates_one_source_without_losing_another(tmp_path):
    store = ConfigStore(tmp_path)
    store.set_sync("cursor", True)
    store.set_sync("claude-code", True)
    store.set_sync("cursor", False)

    reopened = ConfigStore(tmp_path)
    assert not reopened.sync_enabled("cursor")
    assert reopened.sync_enabled("claude-code")


def test_legacy_model_content_permission_is_ignored_on_upgrade(tmp_path):
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "model_content_allowed": False,
                "sources": {"cursor": {"sync_enabled": True}},
            }
        )
    )

    store = ConfigStore(tmp_path)
    assert store.sync_enabled("cursor")
    store.set_sync("codex", True)
    assert json.loads((tmp_path / "config.json").read_text()) == {
        "extraction_window_preset": "3m",
        "schema_version": 1,
        "sedimentation_enabled": True,
        "sources": {
            "codex": {"sync_enabled": True},
            "cursor": {"sync_enabled": True},
        },
    }


def test_invalid_config_fails_loudly_instead_of_silently_disabling_sources(tmp_path):
    (tmp_path / "config.json").write_text('{"schema_version":1,"sources":{"cursor":{}}')
    with pytest.raises(ConfigFileError, match="config_invalid"):
        ConfigStore(tmp_path)


def test_sedimentation_defaults_on_and_survives_restart(tmp_path):
    assert ConfigStore(tmp_path).sedimentation_enabled()

    store = ConfigStore(tmp_path)
    store.set_sedimentation_enabled(False)
    assert not store.sedimentation_enabled()

    # The model configuration is a separate store, so stopping must not touch
    # it: starting again has to be a switch, not a re-entry of a key.
    reopened = ConfigStore(tmp_path)
    assert not reopened.sedimentation_enabled()
    reopened.set_sedimentation_enabled(True)
    assert ConfigStore(tmp_path).sedimentation_enabled()


def test_stopping_sedimentation_keeps_source_sync_config(tmp_path):
    store = ConfigStore(tmp_path)
    store.set_sync("cursor", True)
    store.set_sedimentation_enabled(False)

    reopened = ConfigStore(tmp_path)
    assert not reopened.sedimentation_enabled()
    assert reopened.sync_enabled("cursor")


def test_sedimentation_rejects_a_non_boolean(tmp_path):
    store = ConfigStore(tmp_path)
    with pytest.raises(ValueError, match="invalid_sedimentation_config"):
        store.set_sedimentation_enabled("yes")  # type: ignore[arg-type]
    assert store.sedimentation_enabled()
