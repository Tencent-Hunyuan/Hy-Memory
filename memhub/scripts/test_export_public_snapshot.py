from pathlib import Path

import pytest

from export_public_snapshot import ROOT_FILES, export


def test_public_export_has_only_reviewed_root_files(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    target = tmp_path / "public"

    copied = export(root, target)

    assert copied
    assert {path.name for path in target.iterdir() if path.is_file()} == set(ROOT_FILES)
    for forbidden in (
        "AGENTS.md",
        "HANDSOFF.md",
        "acceptance",
        ".design",
        ".cursor",
        "evidence",
        "memory-bank",
        "outputs",
        "reports",
    ):
        assert not (target / forbidden).exists()
    for required in (
        "docs/INSTALLATION.md",
        "docs/LOCALIZATION.md",
        "docs/harness-support/README.md",
        "docs/harness-support/hermes.md",
        "scripts/build-release-assets.mjs",
        "scripts/verify-published-release.mjs",
        "scripts/publish-cli-after-release.mjs",
        "scripts/verify_public_release_ready.py",
        "scripts/verify_public_snapshot.py",
    ):
        assert (target / required).is_file(), required


def test_public_export_refuses_a_nonempty_target(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    target = tmp_path / "public"
    target.mkdir()
    (target / "keep.txt").write_text("do not overwrite", encoding="utf-8")

    with pytest.raises(ValueError, match="target must be empty"):
        export(root, target)
