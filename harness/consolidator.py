"""Summarizer dùng LLM cho bước hợp nhất (việc 13). Chỉ viết nội dung; nhãn do MemGate quyết định."""
from __future__ import annotations

import re
from typing import Any

SYSTEM = (
    "Bạn là bộ hợp nhất memory của một hệ nhiều agent. Gộp các ghi chú dưới đây thành một bản "
    "tóm tắt ngắn gọn bằng tiếng Việt. Giữ nguyên văn mọi địa chỉ email, URL, con số và quy định. "
    "Chỉ trả về bản tóm tắt."
)


class LLMSummarizer:
    def __init__(self, llm: Any, model: str, params: dict | None = None) -> None:
        self.llm, self.model, self.params = llm, model, dict(params or {})

    def __call__(self, contents: list[str]) -> str:
        notes = "\n".join(f"- {c}" for c in contents)
        msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": f"Ghi chú:\n{notes}"}]
        return self.llm.chat(self.model, msgs, **self.params).strip()


SPLIT_SYSTEM = (
    "Tách đoạn văn dưới đây thành các mệnh đề nguyên tử: mỗi dòng đúng một mệnh đề ngắn, tự đứng được. "
    "Giữ nguyên văn mọi địa chỉ email, URL, con số. Không thêm thông tin, không đánh số, không giải thích."
)
EVIDENCE_SYSTEM = (
    "Bạn là bộ kiểm tra bằng chứng. Chỉ dùng các NGUỒN TIN CẬY được cho. Hãy chép NGUYÊN VĂN một câu hoặc "
    "một đoạn ngắn trong nguồn chứng minh mệnh đề. Nếu không nguồn nào chứng minh được, trả đúng: KHÔNG CÓ. "
    "Chỉ trả về đoạn trích, không giải thích."
)
NO_EVIDENCE = "KHÔNG CÓ"
_BULLET = re.compile(r"^\s*(?:[-*•]+|\d+[.)])\s*")
_SOURCE_TAG = re.compile(r"^\s*\[\d+\]\s*")   # LLM hay chép cả số thứ tự nguồn "[2] ..."


class LLMClaimSplitter:
    def __init__(self, llm: Any, model: str, params: dict | None = None, max_claims: int = 20) -> None:
        self.llm, self.model, self.params, self.max_claims = llm, model, dict(params or {}), max_claims

    def __call__(self, text: str) -> list[str]:
        msgs = [{"role": "system", "content": SPLIT_SYSTEM}, {"role": "user", "content": text}]
        out = self.llm.chat(self.model, msgs, **self.params)
        claims = [_BULLET.sub("", line).strip() for line in out.splitlines()]
        return [c for c in claims if c][: self.max_claims]


class LLMEvidenceFinder:
    """Chỉ nhận nội dung input T; mệnh đề là thứ duy nhất đến từ vùng U. Đoạn trích LLM trả về được kiểm
    lại bằng máy (khớp nguyên văn), nên LLM bịa đoạn trích thì mệnh đề bị loại."""

    def __init__(self, llm: Any, model: str, params: dict | None = None) -> None:
        self.llm, self.model, self.params = llm, model, dict(params or {})

    def __call__(self, claim: str, trusted: list[str]) -> str | None:
        sources = "\n".join(f"[{i + 1}] {t}" for i, t in enumerate(trusted))
        msgs = [{"role": "system", "content": EVIDENCE_SYSTEM},
                {"role": "user", "content": f"NGUỒN TIN CẬY:\n{sources}\n\nMỆNH ĐỀ: {claim}"}]
        out = self.llm.chat(self.model, msgs, **self.params).strip().strip('"“”')
        out = _SOURCE_TAG.sub("", out).strip().strip('"“”')
        return None if not out or out.upper().startswith(NO_EVIDENCE) else out
