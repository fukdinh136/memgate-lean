"""Hợp nhất bảo toàn nhãn (việc 13, FR8, mục 7.4 của đặc tả): fail-closed và phân vùng.

- fail_closed: một bản tóm tắt cho mọi đầu vào, nhãn = nhãn thấp nhất của đầu vào (sàn an toàn).
- partition: tóm tắt riêng nhóm T, A, U; bản tóm tắt mỗi nhóm mang nhãn của nhóm, vì LLM của
  nhóm T không thấy nội dung U.
- rederive (việc 14): như partition, rồi tách bản tóm tắt U thành mệnh đề; mệnh đề nào qua đủ ba
  kiểm tra bằng máy (đoạn trích khớp nguyên văn input T; NLI xác nhận đoạn trích suy ra mệnh đề;
  mọi giá trị nhạy cảm của mệnh đề có nguyên văn trong đoạn trích) thì được ghi thành MỤC MỚI nhãn T,
  lineage trỏ về version T chứa đoạn trích. Bản tóm tắt U giữ nhãn U; không nhãn nào bị nâng.

LLM chỉ viết nội dung (Summarizer); nhãn luôn do máy quyết định (NFR1). Bộ hợp nhất là thành phần
hệ thống, không phải agent, nên nhãn bản tóm tắt không bị kẹp ở A. Lỗi khi tóm tắt thì không ghi
gì cả (fail-closed). Mục đầu vào chuyển sang superseded; bản tóm tắt có cạnh derived_from về từng
version đầu vào nên lineage truy ngược được (FR2).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from memgate.labels import Label
from memgate.models import Edge, Version
from memgate.nli import NLI, entails
from memgate.normalize import appears_in
from memgate.redact import redact

if TYPE_CHECKING:
    from memgate.core import MemGate

Summarizer = Callable[[list[str]], str]
MODES = ("fail_closed", "partition", "rederive")
SCOPES = ("team", "global")       # private của nhiều agent không được gộp chung
ACTOR = "consolidator"


@dataclass
class Rederiver:
    """Ba thành phần của tái dẫn xuất. split và find_evidence do LLM làm (chỉ đề xuất); nli là model NLI.
    find_evidence chỉ được nhận nội dung input T, không bao giờ thấy input U."""

    split: Callable[[str], list[str]]                       # bản tóm tắt U -> mệnh đề nguyên tử
    find_evidence: Callable[[str, list[str]], str | None]   # (mệnh đề, nội dung các input T) -> đoạn trích
    nli: NLI
    threshold: float = 0.5


_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL = re.compile(r"https?://[^\s,;)\]]+")
_NUMBER = re.compile(r"\d[\d.,:/]*\d|\d")
_QUOTED = re.compile(r"`([^`]+)`")          # lệnh/giá trị trong backtick


def sensitive_values(text: str) -> list[str]:
    """Giá trị nhạy cảm trong một mệnh đề: email, URL, số (giờ, số tiền, tài khoản), lệnh trong backtick."""
    vals = _EMAIL.findall(text) + [u.rstrip(".") for u in _URL.findall(text)] + _QUOTED.findall(text)
    no_links = _URL.sub(" ", _EMAIL.sub(" ", text))
    vals += [n.rstrip(".,:") for n in _NUMBER.findall(no_links)]
    return list(dict.fromkeys(v for v in vals if v))


def _norm_ws(s: str) -> str:
    return " ".join(s.split())


def check_claim(claim: str, excerpt: str | None, trusted: list[Version], nli: NLI,
                threshold: float = 0.5) -> tuple[bool, str, Version | None]:
    """Ba kiểm tra bằng máy cho một mệnh đề. Trả (qua?, lý do, version T chứa đoạn trích)."""
    if not excerpt or not excerpt.strip():
        return False, "không có đoạn trích", None
    ex = _norm_ws(excerpt)
    src = next((t for t in trusted if ex in _norm_ws(t.content)), None)
    if src is None:
        return False, "kiểm tra 1: đoạn trích không khớp nguyên văn input T", None
    if not entails(nli, ex, claim, threshold):
        return False, "kiểm tra 2: NLI không xác nhận đoạn trích suy ra mệnh đề", src
    missing = [v for v in sensitive_values(claim) if not appears_in(v, ex)]
    if missing:
        return False, f"kiểm tra 3: giá trị không có trong đoạn trích: {missing}", src
    return True, "qua đủ ba kiểm tra", src


@dataclass
class ConsolidationResult:
    mode: str
    scope: str
    input_ids: list[str] = field(default_factory=list)     # version_id đầu vào
    outputs: list[Version] = field(default_factory=list)   # bản tóm tắt mới
    excess_taint: int = 0     # số mục đầu vào có nhãn cao hơn nhãn bản tóm tắt chứa nó (bị hạ nhãn oan)
    claims: list[dict] = field(default_factory=list)       # rederive: {claim, excerpt, ok, reason}
    rederived: list[Version] = field(default_factory=list)  # rederive: mục T mới

    @property
    def excess_taint_rate(self) -> float:
        return self.excess_taint / len(self.input_ids) if self.input_ids else 0.0


def consolidate(gate: "MemGate", scope: str, mode: str, summarize: Summarizer,
                rederiver: Rederiver | None = None) -> ConsolidationResult:
    if mode == "rederive" and rederiver is None:
        raise ValueError("mode rederive cần Rederiver (split, find_evidence, nli)")
    if mode not in MODES:
        raise ValueError(f"mode không hợp lệ: {mode!r} (chọn {MODES})")
    if scope not in SCOPES:
        raise ValueError(f"chỉ hợp nhất scope {SCOPES}, không hợp nhất {scope!r}")
    inputs = gate.store.active_versions([scope], Label.U, now=gate._now_filter())
    result = ConsolidationResult(mode=mode, scope=scope, input_ids=[v.version_id for v in inputs])
    if not inputs:
        return result
    if mode == "fail_closed":
        groups = [(min(v.label for v in inputs), inputs)]
    else:
        groups = [(lab, [v for v in inputs if v.label == lab]) for lab in (Label.T, Label.A, Label.U)]
        groups = [(lab, vs) for lab, vs in groups if vs]
    # Gọi LLM/NLI trước, ngoài transaction; lỗi ở đây thì chưa có gì được ghi (fail-closed).
    texts = [summarize([v.content for v in vs]) for _, vs in groups]
    passed: list[tuple[str, Version]] = []
    if mode == "rederive":
        trusted = next((vs for lab, vs in groups if lab == Label.T), [])
        u_text = next((t for (lab, _), t in zip(groups, texts) if lab == Label.U), None)
        if u_text is not None:
            for claim in [c.strip() for c in rederiver.split(u_text) if c.strip()]:
                # Bộ tìm bằng chứng chỉ thấy nội dung input T.
                excerpt = rederiver.find_evidence(claim, [t.content for t in trusted]) if trusted else None
                ok, why, src = check_claim(claim, excerpt, trusted, rederiver.nli, rederiver.threshold)
                result.claims.append({"claim": claim, "excerpt": excerpt, "ok": ok, "reason": why})
                if ok:
                    passed.append((claim, src))
    now = gate.clock.now()
    with gate.store.transaction():
        for (label, vs), text in zip(groups, texts):
            item = gate._new_item("long_term", scope, ACTOR)      # pending như mọi mục mới (việc 7)
            out = Version(item_id=item.item_id, content=redact(text), label=label, created_by=ACTOR,
                          created_at=now, reason="consolidate")
            gate.add_version(out, [Edge(child_version_id=out.version_id, parent_kind="version",
                                        parent_id=v.version_id, relation="derived_from") for v in vs])
            for v in vs:
                gate.transition(v.item_id, "superseded", ACTOR, f"hợp nhất {mode}")
            result.outputs.append(out)
            result.excess_taint += sum(1 for v in vs if v.label > label)
        for claim, src in passed:   # mục MỚI nhãn T, lineage về version T của đoạn trích, không về bản U
            item = gate._new_item("long_term", scope, ACTOR)
            new = Version(item_id=item.item_id, content=redact(claim), label=Label.T, created_by=ACTOR,
                          created_at=now, reason="consolidate")
            gate.add_version(new, [Edge(child_version_id=new.version_id, parent_kind="version",
                                        parent_id=src.version_id, relation="derived_from")])
            result.rederived.append(new)
    gate.run_worker()
    gate._audit(ACTOR, "consolidate", f"scope:{scope}", "allow",
                f"mode={mode}; inputs={len(inputs)}; outputs={len(result.outputs)}; "
                f"excess_taint={result.excess_taint}"
                + (f"; claims={len(result.claims)}; rederived={len(result.rederived)}" if mode == "rederive" else ""))
    return result
