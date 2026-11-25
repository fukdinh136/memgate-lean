"""Redact secret trước khi lưu (NFR7, việc 7). MemGate gọi hàm này ở mọi chỗ ghi xuống store: nội dung
Source (observe, message, trusted fact), nội dung Version (write, consolidate, rederive) và target/reason
của audit. Hash nội dung (`content_hash`) vẫn tính trên bản gốc (xem rủi ro trong notes/lifecycle_audit.md).

Bộ dò là detect-secrets với bộ plugin và filter mặc định, quét từng dòng như một file YAML: chế độ này bắt
được cả giá trị không có ngoặc kép (`db_password: ...`). Đã kiểm trên mọi kịch bản và trace hiện có: không
dòng nào bị redact nhầm.

Gate so khớp nguyên văn trên nội dung đã redact, nên giá trị chứa secret không khớp và rơi vào review
(fail-closed, NFR2).
"""
from __future__ import annotations

import re

from detect_secrets.core.scan import _process_line_based_plugins
from detect_secrets.settings import default_settings

MARK = "[REDACTED:{}]"
_PEM = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)", re.S)
_URL_AUTH = re.compile(r"(?P<pre>\b[a-z][a-z0-9+.-]*://[^\s:/@]+:)(?P<pw>[^\s@/]+)(?P<post>@)", re.I)
# Ranh giới khi mở rộng một giá trị secret ra cả token chứa nó (plugin có khi chỉ trả tiền tố, vd "ghp").
_STOP = set(" \t\r\n\"'`,;()<>[]{}=:")
_FILENAME = "memgate.yaml"


def _expand(line: str, start: int, end: int) -> tuple[int, int]:
    while start > 0 and line[start - 1] not in _STOP:
        start -= 1
    while end < len(line) and line[end] not in _STOP:
        end += 1
    return start, end


def _redact_line(line: str, secrets: list[tuple[str, str]]) -> str:
    spans: list[tuple[int, int, str]] = []
    for kind, value in secrets:
        if not value:
            continue
        i = line.find(value)
        while i >= 0:
            s, e = _expand(line, i, i + len(value))
            spans.append((s, e, kind))
            i = line.find(value, i + len(value))
    if not spans:
        return line
    out, pos = [], 0
    for s, e, kind in sorted(spans):
        if s < pos:            # chồng lấn với span trước: đã nằm trong phần bị redact
            continue
        out += [line[pos:s], MARK.format(kind)]
        pos = e
    return "".join(out) + line[pos:]


def find_secrets(text: str) -> list[tuple[int, str, str]]:
    """(số dòng, loại, giá trị) mà detect-secrets báo, dòng đánh số từ 1."""
    lines = list(enumerate(text.split("\n"), 1))
    with default_settings():
        return [(s.line_number, s.type, s.secret_value or "")
                for s in _process_line_based_plugins(lines, filename=_FILENAME)]


def redact(text: str) -> str:
    if not text:
        return text
    text = _PEM.sub(MARK.format("Private Key"), text)
    text = _URL_AUTH.sub(lambda m: m.group("pre") + MARK.format("Basic Auth Credentials") + m.group("post"), text)
    found = find_secrets(text)
    if not found:
        return text
    lines = text.split("\n")
    by_line: dict[int, list[tuple[str, str]]] = {}
    for n, kind, value in found:
        by_line.setdefault(n, []).append((kind, value))
    for n, secrets in by_line.items():
        lines[n - 1] = _redact_line(lines[n - 1], secrets)
    return "\n".join(lines)
