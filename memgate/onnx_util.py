"""Nạp model ONNX + tokenizer từ Hugging Face (dùng chung cho NLI và embedding).

Dùng onnxruntime + tokenizers thay cho torch: trên máy Windows của nhóm, Application Control chặn
DLL của torch nhưng không chặn hai thư viện này. Model tải một lần vào cache của huggingface_hub.
"""
from __future__ import annotations

import json
from typing import Any

import numpy as np


def load(repo: str, model_file: str, tokenizer_file: str, config_file: str | None = None
         ) -> tuple[Any, Any, dict | None]:
    import onnxruntime as ort                       # extra [ml]
    from huggingface_hub import hf_hub_download
    from tokenizers import Tokenizer

    session = ort.InferenceSession(hf_hub_download(repo, model_file), providers=["CPUExecutionProvider"])
    tok = Tokenizer.from_file(hf_hub_download(repo, tokenizer_file))
    cfg = None
    if config_file:
        with open(hf_hub_download(repo, config_file), encoding="utf-8") as f:
            cfg = json.load(f)
    return session, tok, cfg


def feeds(session: Any, encodings: list[Any]) -> dict[str, np.ndarray]:
    """Đưa đúng các input model cần (input_ids, attention_mask, token_type_ids nếu có)."""
    names = {i.name for i in session.get_inputs()}
    out = {"input_ids": np.array([e.ids for e in encodings], dtype=np.int64),
           "attention_mask": np.array([e.attention_mask for e in encodings], dtype=np.int64)}
    if "token_type_ids" in names:
        out["token_type_ids"] = np.array([e.type_ids for e in encodings], dtype=np.int64)
    return {k: v for k, v in out.items() if k in names}
