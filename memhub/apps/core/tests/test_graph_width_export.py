"""EXPORT_FIRST has to actually export.

The option existed and did nothing: it took the user's choice and returned,
leaving them believing they had a backup. The next thing they reach for is
REBUILD_WITH_NEW, and at that point the 847-memory relation graph is gone
with no error anywhere. An option that lies about having backed something up
is worse than not offering it — without it the user at least knows they have
no copy.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from one_memory.hy_graph_admin import (
    GraphWidthDecision,
    apply_graph_width_decision,
    inspect_graph_width_mismatch,
)
from one_memory.hy_sdk_runtime import build_sdk_config
from one_memory.model_profiles import ModelProfileSnapshot, ProviderKind
from tests.graph_fixtures import kuzu_graph_with_embedding_width, model_snapshot


def _narrow_profile() -> ModelProfileSnapshot:
    base = model_snapshot(
        ProviderKind.OLLAMA,
        base_url="http://127.0.0.1:11434",
        has_api_key=False,
    )
    return base.model_copy(update={"embedding_dimensions": 512})


def _mismatched(tmp_path: Path) -> tuple[object, Path, Path]:
    """A 768 graph under a data dir, against a 512 profile."""
    data_dir = tmp_path / "data"
    profile = _narrow_profile()
    config = build_sdk_config(profile, api_key=None, data_dir=data_dir)
    db_path = Path(config.graph_store.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    kuzu_graph_with_embedding_width(db_path, 768)
    return config, db_path, data_dir


def test_export_writes_a_copy_outside_the_data_dir(tmp_path: Path) -> None:
    """A backup inside the directory a rebuild rewrites is not a backup."""
    config, db_path, data_dir = _mismatched(tmp_path)

    outcome = apply_graph_width_decision(
        config,
        GraphWidthDecision.EXPORT_FIRST,
        export_root=tmp_path,
    )

    exported = Path(outcome.exported_path or "")
    assert exported.is_dir()
    assert data_dir not in exported.parents
    assert exported.parent == tmp_path
    assert (exported / db_path.name).is_file()
    # Nothing was archived or moved: export is a copy.
    assert outcome.archived_path is None
    assert db_path.exists()


def test_export_returns_the_path_it_actually_wrote(tmp_path: Path) -> None:
    """The user cannot find a backup whose location we kept to ourselves."""
    config, db_path, _ = _mismatched(tmp_path)

    outcome = apply_graph_width_decision(
        config,
        GraphWidthDecision.EXPORT_FIRST,
        export_root=tmp_path,
    )

    exported = Path(outcome.exported_path or "")
    assert exported.exists()
    assert exported.name.startswith("one-memory-graph-")
    copied = (exported / db_path.name).read_bytes()
    assert copied == db_path.read_bytes()


def test_two_exports_do_not_land_on_each_other(tmp_path: Path) -> None:
    config, _, _ = _mismatched(tmp_path)

    first = apply_graph_width_decision(
        config,
        GraphWidthDecision.EXPORT_FIRST,
        export_root=tmp_path,
    )
    second = apply_graph_width_decision(
        config,
        GraphWidthDecision.EXPORT_FIRST,
        export_root=tmp_path,
    )

    assert first.exported_path != second.exported_path
    assert Path(first.exported_path or "").exists()
    assert Path(second.exported_path or "").exists()


def test_a_copy_that_fails_partway_raises_and_leaves_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A half-copied graph looks exactly like a backup, which is the danger.

    Guard on the leftovers rather than on the exception alone: an
    implementation that copies straight into the destination would raise here
    too, and still leave a directory the user would go on to trust.
    """
    config, db_path, _ = _mismatched(tmp_path)
    db_path.with_name(f"{db_path.name}.wal").write_bytes(b"write-ahead")

    calls = {"n": 0}
    real_copy = shutil.copy2

    def fail_on_second(source, target, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise OSError("disk full")
        return real_copy(source, target, *args, **kwargs)

    monkeypatch.setattr(shutil, "copy2", fail_on_second)

    with pytest.raises(OSError):
        apply_graph_width_decision(
            config,
            GraphWidthDecision.EXPORT_FIRST,
            export_root=tmp_path,
        )

    assert calls["n"] >= 2, "the copy never got far enough to test anything"
    leftovers = [path for path in tmp_path.iterdir() if path.name.startswith("one-memory-graph-")]
    assert leftovers == []
    assert db_path.exists()


def test_the_question_is_still_open_after_exporting(tmp_path: Path) -> None:
    """Exporting is not a decision about the graph, only preparation for one."""
    config, _, _ = _mismatched(tmp_path)

    apply_graph_width_decision(
        config,
        GraphWidthDecision.EXPORT_FIRST,
        export_root=tmp_path,
    )

    assert inspect_graph_width_mismatch(config) is not None


def test_keeping_and_rebuilding_export_nothing(tmp_path: Path) -> None:
    """Only EXPORT_FIRST writes a copy; the other two must not."""
    config, _, _ = _mismatched(tmp_path)

    kept = apply_graph_width_decision(
        config,
        GraphWidthDecision.KEEP_EXISTING,
        export_root=tmp_path,
    )
    rebuilt = apply_graph_width_decision(
        config,
        GraphWidthDecision.REBUILD_WITH_NEW,
        export_root=tmp_path,
    )

    assert kept.exported_path is None
    assert rebuilt.exported_path is None
    assert rebuilt.archived_path is not None
