"""Graph width mismatch must ask, never silently archive.

A-29 measured the live library at 768. Built-in bge-small-zh-v1.5 is 512.
That mismatch is certain, not hypothetical, and the old
`retire_mismatched_graph_store` path is exactly what archived a 13MB graph
once already. These tests pin the replacement: detect, offer a choice, and
leave the graph alone until the user picks.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from one_memory.hy_graph_admin import (
    GraphWidthDecision,
    GraphWidthMismatchError,
    apply_graph_width_decision,
    inspect_graph_width_mismatch,
)
from one_memory.hy_sdk_runtime import acquire_client, build_sdk_config
from one_memory.model_profiles import ModelProfileSnapshot, ProviderKind
from tests.graph_fixtures import kuzu_graph_with_embedding_width, model_snapshot


def _narrow_profile(**overrides: object) -> ModelProfileSnapshot:
    # Width alone is what matters here. The built-in provider arrives in S1.3;
    # this guard has to hold for any 512-dim profile against a 768 graph.
    base = model_snapshot(
        ProviderKind.OLLAMA,
        base_url="http://127.0.0.1:11434",
        has_api_key=False,
    )
    return base.model_copy(
        update={
            "embedding_dimensions": 512,
            **overrides,
        }
    )


def test_a_768_graph_is_not_silently_archived_when_switching_to_512(
    tmp_path: Path,
) -> None:
    profile = _narrow_profile()
    config = build_sdk_config(profile, api_key=None, data_dir=tmp_path)
    db_path = Path(config.graph_store.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    kuzu_graph_with_embedding_width(db_path, 768)

    mismatch = inspect_graph_width_mismatch(config)

    assert mismatch is not None
    assert mismatch.stored_width == 768
    assert mismatch.expected_width == 512
    assert db_path.exists()
    assert list(db_path.parent.glob("*.bak")) == []


def test_creating_a_client_against_a_mismatched_graph_raises_instead_of_retiring(
    tmp_path: Path,
) -> None:
    profile = _narrow_profile()
    config = build_sdk_config(profile, api_key=None, data_dir=tmp_path)
    db_path = Path(config.graph_store.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    kuzu_graph_with_embedding_width(db_path, 768)

    with pytest.raises(GraphWidthMismatchError) as caught:
        acquire_client(profile, api_key=None, data_dir=tmp_path)

    assert caught.value.mismatch.stored_width == 768
    assert caught.value.mismatch.expected_width == 512
    assert db_path.exists()
    assert list(db_path.parent.glob("*.bak")) == []


def test_keep_existing_leaves_the_graph_and_refuses_the_new_width(
    tmp_path: Path,
) -> None:
    profile = _narrow_profile()
    config = build_sdk_config(profile, api_key=None, data_dir=tmp_path)
    db_path = Path(config.graph_store.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    kuzu_graph_with_embedding_width(db_path, 768)

    outcome = apply_graph_width_decision(config, GraphWidthDecision.KEEP_EXISTING)

    assert outcome.archived_path is None
    assert outcome.exported_path is None
    assert db_path.exists()
    assert list(db_path.parent.glob("*.bak")) == []


def test_rebuild_archives_the_graph_and_lets_the_new_width_proceed(
    tmp_path: Path,
) -> None:
    profile = _narrow_profile()
    config = build_sdk_config(profile, api_key=None, data_dir=tmp_path)
    db_path = Path(config.graph_store.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    kuzu_graph_with_embedding_width(db_path, 768)

    outcome = apply_graph_width_decision(config, GraphWidthDecision.REBUILD_WITH_NEW)

    assert outcome.archived_path is not None
    archive = Path(outcome.archived_path)
    assert archive.exists()
    assert "dim768" in archive.name
    assert not db_path.exists()


def test_export_first_leaves_the_graph_untouched(tmp_path: Path) -> None:
    profile = _narrow_profile()
    config = build_sdk_config(profile, api_key=None, data_dir=tmp_path / "data")
    db_path = Path(config.graph_store.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    kuzu_graph_with_embedding_width(db_path, 768)

    outcome = apply_graph_width_decision(
        config,
        GraphWidthDecision.EXPORT_FIRST,
        export_root=tmp_path,
    )

    assert outcome.archived_path is None
    assert db_path.exists()
    # Where the copy lands, and that it is all there, is pinned in
    # test_graph_width_export.py; here the point is only that the original
    # is not touched.
    assert outcome.exported_path is not None


def test_a_fresh_install_with_no_graph_raises_nothing(tmp_path: Path) -> None:
    profile = _narrow_profile()
    config = build_sdk_config(profile, api_key=None, data_dir=tmp_path)

    assert inspect_graph_width_mismatch(config) is None
