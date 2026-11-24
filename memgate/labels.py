"""Nhãn nguồn và luật lan truyền (spec mục 8.1).

Thứ tự U < A < T. Nhãn chỉ có thể đi xuống: mọi phép kết hợp dùng min.
"""
from __future__ import annotations

from enum import IntEnum


class Label(IntEnum):
    U = 0  # không tin cậy: web, MCP, file bên ngoài, nguồn chưa đăng ký
    A = 1  # agent: mọi thứ do agent tạo ra khi chưa đọc nguồn U
    T = 2  # tin cậy: user, tool đã xác thực


def meet(*labels: Label) -> Label:
    """Nhãn thấp nhất trong các nhãn."""
    return Label(min(labels))


def output_label(context_label: Label) -> Label:
    """label(o) = min(A, L(a)): output của agent không bao giờ mang nhãn T."""
    return meet(Label.A, context_label)


# Kênh đầu vào đã xác thực (giả định A1). Mọi kind khác mặc định U (NFR2: fail-closed).
TRUSTED_KINDS = frozenset({"user", "tool_trusted"})


def label_for_kind(kind: str) -> Label:
    return Label.T if kind in TRUSTED_KINDS else Label.U
