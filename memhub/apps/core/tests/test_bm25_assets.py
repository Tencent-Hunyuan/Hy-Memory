from __future__ import annotations

import os
import sys

import pytest

from one_memory import bm25_assets


def test_the_release_manifest_pins_the_complete_bm25_snapshot() -> None:
    assert bm25_assets.BM25_MODEL == "Qdrant/bm25"
    assert len(bm25_assets.BM25_SNAPSHOT) == 40
    assert "config.json" in bm25_assets.BM25_FILES
    assert "english.txt" in bm25_assets.BM25_FILES
    assert len(bm25_assets.BM25_FILES) == 18


def test_a_frozen_core_resolves_the_bundled_cache(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    assert bm25_assets.resolve_cache_dir() == (
        tmp_path / "one_memory" / bm25_assets.BM25_CACHE_DIR_NAME
    )


def test_missing_or_changed_assets_fail_closed(tmp_path) -> None:
    with pytest.raises(bm25_assets.Bm25AssetError, match="bm25_snapshot_ref_missing"):
        bm25_assets.verify_bm25_assets(tmp_path)


def test_a_complete_snapshot_tree_passes_verification(tmp_path) -> None:
    model = tmp_path / "models--Qdrant--bm25"
    ref = model / "refs" / "main"
    snapshot = model / "snapshots" / bm25_assets.BM25_SNAPSHOT
    ref.parent.mkdir(parents=True)
    snapshot.mkdir(parents=True)
    ref.write_text(f"{bm25_assets.BM25_SNAPSHOT}\n", encoding="utf-8")
    for name, digest in bm25_assets.BM25_FILES.items():
        # Write bytes that hash to the expected digest by using the digest as a
        # stand-in is impossible; instead write known content and patch the
        # expected map for this fixture.
        (snapshot / name).write_bytes(name.encode("utf-8"))

    expected = {
        name: __import__("hashlib").sha256(name.encode("utf-8")).hexdigest()
        for name in bm25_assets.BM25_FILES
    }
    original = bm25_assets.BM25_FILES
    try:
        bm25_assets.BM25_FILES = expected
        assert bm25_assets.verify_bm25_assets(tmp_path) == tmp_path
    finally:
        bm25_assets.BM25_FILES = original


def test_configure_forces_the_bundled_cache_offline(tmp_path, monkeypatch) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(bm25_assets, "resolve_cache_dir", lambda: cache)
    monkeypatch.setattr(bm25_assets, "verify_bm25_assets", lambda value: value)
    monkeypatch.delenv("FASTEMBED_CACHE_PATH", raising=False)
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("HF_HUB_DISABLE_TELEMETRY", raising=False)

    assert bm25_assets.configure_bm25_cache() == cache
    assert os.environ["FASTEMBED_CACHE_PATH"] == str(cache)
    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert os.environ["HF_HUB_DISABLE_TELEMETRY"] == "1"
