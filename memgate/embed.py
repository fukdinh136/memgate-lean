"""Embedding cho retrieve (việc 4b).

Mặc định: sentence-transformers với BAAI/bge-small-en-v1.5 (384 chiều, chạy CPU), tải lần đầu từ
Hugging Face. Đặt MEMGATE_EMBED_MODEL=<đường dẫn thư mục model> để dùng bản có sẵn trên máy, vd
bản trong models/bge-small-en-v1.5 của repo nhóm cũ. Model chỉ được nạp khi retrieve lần đầu.
MEMGATE_EMBED_MODEL=hash dùng HashEmbedder (lệch đặc tả; chỉ khi máy không nạp được torch).
"""
from __future__ import annotations

import hashlib
import os
import re
from typing import Protocol

import numpy as np

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class Embedder(Protocol):
    name: str

    def encode(self, texts: list[str], is_query: bool = False) -> np.ndarray: ...  # hàng đã chuẩn hoá L2


class SentenceTransformerEmbedder:
    def __init__(self, model: str | None = None) -> None:
        self.model_id = model or os.environ.get("MEMGATE_EMBED_MODEL") or DEFAULT_MODEL
        self.name = os.path.basename(self.model_id.rstrip("/\\")) or self.model_id
        self._model = None

    def encode(self, texts: list[str], is_query: bool = False) -> np.ndarray:
        if self._model is None:
            from sentence_transformers import SentenceTransformer  # extra [ml]

            self._model = SentenceTransformer(self.model_id, device="cpu")
        if is_query and "bge" in self.model_id.lower():
            texts = [BGE_QUERY_PREFIX + t for t in texts]
        return np.asarray(self._model.encode(texts, normalize_embeddings=True), dtype=np.float32)


class OnnxEmbedder:
    """bge-small-en-v1.5 bản ONNX (Xenova/bge-small-en-v1.5), chạy bằng onnxruntime, không cần torch.
    Pooling CLS rồi chuẩn hoá L2, như sentence-transformers làm với BGE. Tên trùng bản torch nên
    embedding đã lưu và index HNSW dùng chung được."""

    def __init__(self, repo: str = "Xenova/bge-small-en-v1.5", model_file: str = "onnx/model.onnx",
                 tokenizer_file: str = "tokenizer.json", name: str = "bge-small-en-v1.5", max_len: int = 512) -> None:
        self.repo, self.model_file, self.tokenizer_file, self.max_len = repo, model_file, tokenizer_file, max_len
        self.name = name
        self._session = self._tok = None

    def encode(self, texts: list[str], is_query: bool = False) -> np.ndarray:
        if self._session is None:
            from memgate.onnx_util import load
            self._session, self._tok, _ = load(self.repo, self.model_file, self.tokenizer_file)
            self._tok.enable_truncation(self.max_len)
            self._tok.enable_padding()
        from memgate.onnx_util import feeds
        if is_query:
            texts = [BGE_QUERY_PREFIX + t for t in texts]
        out = self._session.run(None, feeds(self._session, self._tok.encode_batch(list(texts))))[0]
        cls = out[:, 0, :].astype(np.float32)
        norms = np.linalg.norm(cls, axis=1, keepdims=True)
        return cls / np.where(norms == 0, 1, norms)


def make_embedder(model: str | None = None) -> "Embedder":
    """Chọn embedder theo tên (hoặc MEMGATE_EMBED_MODEL):
    - mặc định / "onnx": OnnxEmbedder (bge-small-en-v1.5 qua onnxruntime; chạy được trên máy bị chặn torch);
    - "hash": HashEmbedder (test, hoặc khi không tải được model);
    - tên/thư mục model khác: SentenceTransformerEmbedder (cần torch)."""
    model_id = (model or os.environ.get("MEMGATE_EMBED_MODEL") or "onnx").strip()
    if model_id.lower() == "hash":
        return HashEmbedder()
    if model_id.lower() in ("onnx", DEFAULT_MODEL.lower()):
        return OnnxEmbedder()
    return SentenceTransformerEmbedder(model_id)


class HashEmbedder:
    """Embedding tất định, không cần model: túi từ băm vào `dim` chiều. Dùng cho test."""

    def __init__(self, dim: int = 256) -> None:
        self.dim, self.name = dim, f"hash{dim}"

    def encode(self, texts: list[str], is_query: bool = False) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            for tok in re.findall(r"\w+", t.lower()):
                out[i, int(hashlib.md5(tok.encode()).hexdigest(), 16) % self.dim] += 1.0
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        return out / np.where(norms == 0, 1, norms)
