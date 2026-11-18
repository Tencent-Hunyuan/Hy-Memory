"""Pinned offline assets for FastEmbed's ``Qdrant/bm25`` tokenizer.

The model has no weights, but FastEmbed still resolves its config and
language stopword files through a Hugging Face snapshot. Release builds fetch
that snapshot once and freeze it beside Core. A user's first Qdrant read or
write must never need the network.
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

BM25_MODEL = "Qdrant/bm25"
BM25_CACHE_DIR_NAME = "fastembed_cache"
BM25_SNAPSHOT = "22b8d2af71a76161e18dd432d2cee0eefa66e412"

# The exact Qdrant/bm25 snapshot used by FastEmbed 0.9.0.
BM25_FILES = {
    "arabic.txt": "0df01c0a184d8c15077c6f0ee70e25e0c0308d827b92b14a19f5f819a0c465d0",
    "config.json": "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a",
    "danish.txt": "1bedc9cf5a8830dacf8c4ee0d8b301f0801861756ad0d504431d01047f961b0c",
    "dutch.txt": "e5a2a7c390fe3ad0c0a132586ed11492b635d66a1f426cd89677f80b07bc76a6",
    "english.txt": "019f104ba2ed07436d05f9cdd3383034ad66014edc27fc651f837e1a038b6451",
    "finnish.txt": "952af766edc9b8e7ddc877fc464cbd94b91754b5621fdfdd7020568fd4813fcd",
    "french.txt": "58ac7f7c074b70dc0fc86f6fcf40b2c227e9f95627e90e57971d5cce1e30e2e9",
    "german.txt": "f0c25acd7ec02a31f6679d9664ec5222ca846fdbc58db96160f4e7c0ddb0f7ea",
    "greek.txt": "7a97c6dc81144f7e8fcfe976025954ca3731f78a75d1fd86aa87f261fd195dae",
    "hungarian.txt": "64a68b5ebaa616b25bd976ac915c326f2ea8cce8b59a0ac8c5728b980ae4fb0c",
    "italian.txt": "293d7841f198e4012f49e8e5653c3bfd073a58cea1259fa2c0fcec894167b628",
    "norwegian.txt": "f7e5b42208ccf1b1f282f9e0f8570e464272762bda5718b6b26750f510a688dc",
    "portuguese.txt": "6e7b98378c6b728266a83ebf035d927abf16b9e0d815d6204d81d90d896bcc9b",
    "romanian.txt": "0ab53d21cbde00c6bf367706a0243c646140ba564cce7e83ac33d80f8923e957",
    "russian.txt": "1743191192b4a4f77fcc216499455dc00c1b8626fdd407076a8deefff80e3d59",
    "spanish.txt": "6125eadf28ba664a60bf4296147bcbd40b80be93670056fdb229960ac15e2310",
    "swedish.txt": "2a9d9d756bc4257d49329994f805c712cd5ab6746162e2082752238766c1c0c8",
    "turkish.txt": "f2c7f0c2bd3dba42da700776266831853a3b5f1207fada5c15207334109abb57",
}


class Bm25AssetError(RuntimeError):
    """The pinned FastEmbed snapshot is absent or does not match its manifest."""


def resolve_cache_dir() -> Path:
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle is not None:
        return Path(bundle) / "one_memory" / BM25_CACHE_DIR_NAME
    return Path(__file__).resolve().parent / BM25_CACHE_DIR_NAME


def verify_bm25_assets(directory: Path | None = None) -> Path:
    root = directory or resolve_cache_dir()
    model = root / "models--Qdrant--bm25"
    ref = model / "refs" / "main"
    try:
        snapshot = ref.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise Bm25AssetError("bm25_snapshot_ref_missing") from error
    if snapshot != BM25_SNAPSHOT:
        raise Bm25AssetError(f"bm25_snapshot_mismatch:{snapshot}")

    snapshot_dir = model / "snapshots" / BM25_SNAPSHOT
    problems: list[str] = []
    for name, expected in BM25_FILES.items():
        path = snapshot_dir / name
        try:
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            problems.append(f"{name}:missing")
            continue
        if actual != expected:
            problems.append(f"{name}:sha256:{actual}")
    if problems:
        raise Bm25AssetError(";".join(problems))
    return root


def configure_bm25_cache() -> Path | None:
    """Use the frozen cache offline; leave source checkouts alone until fetched."""
    root = resolve_cache_dir()
    if not root.is_dir():
        return None
    verify_bm25_assets(root)
    os.environ["FASTEMBED_CACHE_PATH"] = str(root)
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    return root
