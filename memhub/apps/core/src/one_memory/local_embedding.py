"""Embedding that runs on this machine, with nothing to configure.

Deliberately onnxruntime and tokenizers rather than sentence-transformers:
the convenience wrapper drags in torch, which takes the installer past 2GB and
defeats the reason for building a model in at all. Both libraries are already
in the tree through chromadb, so the runtime cost of this module is the
weights alone.
"""

from __future__ import annotations

import threading
from importlib import import_module
from typing import TYPE_CHECKING, Any

from one_memory.embedding_assets import (
    BUILTIN_EMBEDDING_DIMENSIONS,
    BUILTIN_EMBEDDING_MAX_TOKENS,
    MissingEmbeddingAssetsError,
    assets_present,
    resolve_asset_dir,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pathlib import Path

_FETCH_HINT = (
    "The built-in embedding model is not in this tree. "
    "Run scripts/fetch_embedding_model.py to fetch and verify it."
)


class LocalEmbedder:
    """Encodes text with the built-in model, loading it on first use."""

    REQUIRED_FILES = frozenset({"model_int8.onnx", "tokenizer.json"})

    dimensions = BUILTIN_EMBEDDING_DIMENSIONS

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._session: Any | None = None
        self._tokenizer: Any | None = None
        self._input_names: frozenset[str] = frozenset()

    def _load(self) -> tuple[Any, Any]:
        # Double-checked under a lock: memory processing encodes from worker
        # threads, and two of them racing would each build a session and hold
        # the weights twice.
        if self._session is not None and self._tokenizer is not None:
            return self._session, self._tokenizer
        with self._lock:
            if self._session is not None and self._tokenizer is not None:
                return self._session, self._tokenizer

            directory: Path = resolve_asset_dir()
            if not assets_present(directory):
                raise MissingEmbeddingAssetsError(f"{_FETCH_HINT} Looked in {directory}.")

            # Imported by name for the same reason kuzu and hy_memory are in
            # onnxruntime ships no py.typed, and a dynamic
            # import keeps the strict checker on everything around it.
            ort = import_module("onnxruntime")
            from tokenizers import Tokenizer

            tokenizer = Tokenizer.from_file(str(directory / "tokenizer.json"))
            tokenizer.enable_truncation(max_length=BUILTIN_EMBEDDING_MAX_TOKENS)
            tokenizer.enable_padding()

            options = ort.SessionOptions()
            # One model, many short texts: the default thread pool competes
            # with the import workers for cores and makes both slower.
            options.intra_op_num_threads = 1
            options.inter_op_num_threads = 1
            session = ort.InferenceSession(
                str(directory / "model_int8.onnx"),
                sess_options=options,
                providers=["CPUExecutionProvider"],
            )

            self._input_names = frozenset(item.name for item in session.get_inputs())
            self._session = session
            self._tokenizer = tokenizer
            return session, tokenizer

    def encode(self, texts: list[str]) -> list[list[float]]:
        """Return one unit-length vector per text, in the order given."""
        if not texts:
            return []

        session, tokenizer = self._load()

        import numpy as np

        encodings = tokenizer.encode_batch(texts)
        candidates = {
            "input_ids": [encoding.ids for encoding in encodings],
            "attention_mask": [encoding.attention_mask for encoding in encodings],
            "token_type_ids": [encoding.type_ids for encoding in encodings],
        }
        feed = {
            name: np.array(value, dtype=np.int64)
            for name, value in candidates.items()
            if name in self._input_names
        }

        hidden = session.run(None, feed)[0]
        # BGE reads the CLS position rather than pooling the sequence, then
        # normalises; skipping the normalisation leaves similarities on an
        # arbitrary scale and quietly distorts every comparison downstream.
        cls = hidden[:, 0]
        lengths = np.linalg.norm(cls, axis=1, keepdims=True)
        vectors = cls / np.maximum(lengths, 1e-12)
        return [[float(value) for value in row] for row in vectors]
