"""Wrapper NLI dùng chung cho việc 11b (mâu thuẫn) và 14 (tái dẫn xuất). Việc 11a.

Mặc định: MoritzLaurer/multilingual-MiniLMv2-L6-mnli-xnli (đa ngôn ngữ, có tiếng Việt), bản ONNX,
chạy CPU. Kết quả cache theo hash cặp câu. NLI chỉ là MỘT trong ba kiểm tra bằng máy của tái dẫn xuất;
không bao giờ một mình cấp quyền.
"""
from __future__ import annotations

import hashlib
from typing import Protocol

import numpy as np

LABELS = ("entailment", "neutral", "contradiction")
DEFAULT_MODEL = "MoritzLaurer/multilingual-MiniLMv2-L6-mnli-xnli"


class NLI(Protocol):
    def predict(self, premise: str, hypothesis: str) -> dict[str, float]: ...  # xác suất 3 nhãn


def entails(nli: NLI, premise: str, hypothesis: str, threshold: float = 0.5) -> bool:
    """premise suy ra hypothesis: entailment là nhãn cao nhất và không thấp hơn ngưỡng."""
    p = nli.predict(premise, hypothesis)
    return p["entailment"] >= threshold and p["entailment"] == max(p.values())


class OnnxNLI:
    def __init__(self, repo: str = DEFAULT_MODEL, model_file: str = "onnx/model.onnx",
                 tokenizer_file: str = "onnx/tokenizer.json", max_len: int = 512) -> None:
        self.repo, self.model_file, self.tokenizer_file, self.max_len = repo, model_file, tokenizer_file, max_len
        self.name = repo.split("/")[-1]
        self._session = self._tok = None
        self._labels: list[str] = list(LABELS)
        self._cache: dict[str, dict[str, float]] = {}

    def _load(self) -> None:
        from memgate.onnx_util import load
        self._session, self._tok, cfg = load(self.repo, self.model_file, self.tokenizer_file, "config.json")
        self._tok.enable_truncation(self.max_len)
        if cfg and "id2label" in cfg:   # thứ tự nhãn lấy từ config, không đoán
            self._labels = [cfg["id2label"][str(i)].lower() for i in range(len(cfg["id2label"]))]

    def predict(self, premise: str, hypothesis: str) -> dict[str, float]:
        key = hashlib.sha256(f"{premise}\0{hypothesis}".encode()).hexdigest()
        if key in self._cache:
            return self._cache[key]
        if self._session is None:
            self._load()
        from memgate.onnx_util import feeds
        enc = self._tok.encode(premise, hypothesis)
        logits = self._session.run(["logits"], feeds(self._session, [enc]))[0][0].astype(np.float64)
        e = np.exp(logits - logits.max())
        probs = e / e.sum()
        out = {lab: float(probs[i]) for i, lab in enumerate(self._labels)}
        self._cache[key] = out
        return out


class FakeNLI:
    """NLI giả cho test: entailment khi mọi từ của hypothesis có trong premise (hoặc theo bảng cho sẵn)."""

    def __init__(self, table: dict[tuple[str, str], str] | None = None) -> None:
        self.table = table or {}

    def predict(self, premise: str, hypothesis: str) -> dict[str, float]:
        lab = self.table.get((premise, hypothesis))
        if lab is None:
            lab = "entailment" if set(hypothesis.lower().split()) <= set(premise.lower().split()) else "neutral"
        return {k: (0.9 if k == lab else 0.05) for k in LABELS}
