"""So sánh giá trị dùng chung cho cổng hành động, scorer và tái dẫn xuất.

"Nguyên văn" = bằng nhau sau khi chuẩn hoá định dạng, nội dung không đổi:
- bỏ khoảng trắng hai đầu, gộp khoảng trắng liên tiếp, không phân biệt hoa/thường;
- số viết theo một dạng: "5000", "5000.0", "5,000", "$5,000" là một.
"""
from __future__ import annotations

import re
from typing import Any

_NUM = re.compile(r"^[$€£]?\s*-?\d{1,3}(?:,\d{3})+(?:\.\d+)?$|^[$€£]?\s*-?\d+(?:\.\d+)?$")
_NUM_IN_TEXT = re.compile(r"[$€£]?-?\d[\d,]*(?:\.\d+)?")
# Ký tự được coi là một phần của token: chữ, số, _, -, @, ., /, :, =, +, %
_TOKEN_CHAR = r"[\w\-@./:=+%]"


def _canon_number(s: str) -> str | None:
    if not _NUM.match(s):
        return None
    f = float(re.sub(r"[$€£,\s]", "", s))
    return str(int(f)) if f.is_integer() else repr(f)


def normalize_value(v: Any) -> str:
    s = re.sub(r"\s+", " ", str(v)).strip()
    num = _canon_number(s)
    return num if num is not None else s.casefold()


def is_number(v: Any) -> bool:
    return _canon_number(re.sub(r"\s+", " ", str(v)).strip()) is not None


def appears_in(value: Any, text: str) -> bool:
    """value có xuất hiện nguyên văn, như một token trọn vẹn, trong text không.

    Token trọn vẹn: ký tự ngay trước và ngay sau không phải ký tự của token. Dấu chấm hoặc phẩy
    ngay sau được chấp nhận nếu theo sau nó không phải chữ/số (dấu kết câu, không phải ".com")."""
    nv = normalize_value(value)
    if not nv:
        return False
    if is_number(value):
        return any(_canon_number(t.rstrip(".,")) == nv for t in _NUM_IN_TEXT.findall(text))
    hay = normalize_value(text)
    for m in re.finditer(rf"(?<!{_TOKEN_CHAR}){re.escape(nv)}", hay):
        nxt = hay[m.end():m.end() + 2]
        if not nxt or not re.match(_TOKEN_CHAR, nxt[0]):
            return True
        if nxt[0] in ".," and (len(nxt) == 1 or not re.match(r"\w", nxt[1])):
            return True
    return False


MULTI_VALUE_FIELDS = frozenset({"to", "cc", "bcc", "recipient", "recipients"})


def split_values(field: str, v: Any) -> list[str]:
    """Trường người nhận có thể chứa nhiều giá trị: "a@x.com, b@y.com" hoặc list."""
    if isinstance(v, (list, tuple)):
        items = [str(x) for x in v]
    elif field in MULTI_VALUE_FIELDS and not is_number(v):   # "$5,000" là một số, không tách
        items = re.split(r"[,;]", str(v))
    else:
        items = [str(v)]
    return [i.strip() for i in items if i.strip()]


def canonical_args(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    """Đưa tham số về một dạng trước khi cổng hành động hoặc scorer đối chiếu.

    update_setting: LLM có thể truyền {"key": "k", "value": "v"}, {"key": "k", "value": "k=v"} hoặc
    {"key": "k=v"}; cả ba thành {"key": "k", "value": "v"}."""
    if tool != "update_setting":
        return args
    out = dict(args)
    for field in ("value", "key"):
        k, sep, v = str(out.get(field, "")).partition("=")
        if sep and k.strip() and v.strip():
            out["value"] = v.strip()
            if field == "key" or not str(out.get("key", "")).strip():
                out["key"] = k.strip()
            break
    return out
