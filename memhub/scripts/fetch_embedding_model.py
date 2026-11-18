#!/usr/bin/env python
"""Fetch the built-in embedding model and check it against the manifest.

Runs at build time, never on a user's machine: the point of building the
model in is that a first launch works with no network. Downloading here and
verifying against `embedding_assets.EMBEDDING_ASSETS` is what lets the weights
stay out of a repository that is about to go public.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "apps" / "core" / "src"))

from one_memory.embedding_assets import (  # noqa: E402
    EMBEDDING_ASSETS,
    AssetVerificationError,
    digest_of,
    resolve_asset_dir,
    verify_assets,
)
from one_memory.bm25_assets import (  # noqa: E402
    BM25_MODEL,
    Bm25AssetError,
    resolve_cache_dir as resolve_bm25_cache_dir,
    verify_bm25_assets,
)


def fetch(target: Path, *, force: bool) -> int:
    target.mkdir(parents=True, exist_ok=True)
    for asset in EMBEDDING_ASSETS:
        path = target / asset.filename
        if path.is_file() and not force and digest_of(path) == asset.sha256:
            print(f"  ok       {asset.filename}")
            continue
        print(f"  fetching {asset.filename} from {asset.url}")
        with urllib.request.urlopen(asset.url, timeout=300) as response:
            path.write_bytes(response.read())

    try:
        verify_assets(target)
    except AssetVerificationError as error:
        # Do not leave unverified bytes where the encoder will happily load
        # them: a partial download that still parses is the failure mode this
        # whole manifest exists to catch.
        print(f"\nverification failed: {error}", file=sys.stderr)
        for asset in EMBEDDING_ASSETS:
            (target / asset.filename).unlink(missing_ok=True)
        return 1

    total = sum((target / asset.filename).stat().st_size for asset in EMBEDDING_ASSETS)
    print(f"\nverified {len(EMBEDDING_ASSETS)} files, {total / 1048576:.1f} MB")
    print(f"into {target}")
    return 0


def fetch_bm25(target: Path, *, force: bool) -> int:
    if force:
        shutil.rmtree(target, ignore_errors=True)
    try:
        from fastembed import SparseTextEmbedding

        model = SparseTextEmbedding(BM25_MODEL, cache_dir=str(target))
        # Construction downloads the snapshot; one encode proves its tokenizer
        # and language assets are usable before they are frozen.
        if not list(model.embed(["MemHub offline BM25 release check"])):
            raise Bm25AssetError("bm25_embedding_empty")
        verify_bm25_assets(target)
    except (Bm25AssetError, ImportError, RuntimeError, ValueError) as error:
        print(f"BM25 verification failed: {error}", file=sys.stderr)
        shutil.rmtree(target, ignore_errors=True)
        return 1
    print(f"verified {BM25_MODEL} offline cache in {target}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, default=None)
    parser.add_argument("--bm25-target", type=Path, default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="check what is already on disk and fetch nothing",
    )
    args = parser.parse_args()

    target = args.target or resolve_asset_dir()
    bm25_target = args.bm25_target or resolve_bm25_cache_dir()
    if args.verify_only:
        try:
            verify_assets(target)
            verify_bm25_assets(bm25_target)
        except (AssetVerificationError, Bm25AssetError) as error:
            print(f"verification failed: {error}", file=sys.stderr)
            return 1
        print(f"verified {len(EMBEDDING_ASSETS)} files in {target}")
        print(f"verified {BM25_MODEL} cache in {bm25_target}")
        return 0
    result = fetch(target, force=args.force)
    if result != 0:
        return result
    return fetch_bm25(bm25_target, force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())
