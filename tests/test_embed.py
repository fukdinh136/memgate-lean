import os

import numpy as np
import pytest

from memgate import MemGate
from memgate.embed import HashEmbedder, OnnxEmbedder, SentenceTransformerEmbedder, make_embedder

# Test model thật tải ~130 MB từ Hugging Face: chỉ chạy khi đặt MEMGATE_TEST_ONNX=1.
needs_onnx = pytest.mark.skipif(not os.environ.get("MEMGATE_TEST_ONNX"), reason="đặt MEMGATE_TEST_ONNX=1")


def test_make_embedder_hash_by_name():
    assert isinstance(make_embedder("hash"), HashEmbedder)
    assert isinstance(make_embedder(" HASH "), HashEmbedder)


def test_make_embedder_reads_env(monkeypatch):
    monkeypatch.setenv("MEMGATE_EMBED_MODEL", "hash")
    assert isinstance(MemGate().embedder, HashEmbedder)
    monkeypatch.delenv("MEMGATE_EMBED_MODEL")
    e = make_embedder()
    assert isinstance(e, OnnxEmbedder) and e._session is None and e.name == "bge-small-en-v1.5"  # chưa nạp
    assert isinstance(make_embedder("BAAI/bge-small-en-v1.5"), OnnxEmbedder)
    assert isinstance(make_embedder("models/khac"), SentenceTransformerEmbedder)


@needs_onnx
def test_onnx_bge_ranks_related_text_higher():
    e = OnnxEmbedder()
    docs = e.encode(["The backup job runs at 02:00 UTC every night.", "Our cafeteria serves pho on Fridays."])
    q = e.encode(["When does the nightly backup run?"], is_query=True)[0]
    assert docs.shape == (2, 384)
    assert np.allclose(np.linalg.norm(docs, axis=1), 1, atol=1e-4)
    s = docs @ q
    assert s[0] > s[1]
