"""Cổng hành động (spec mục 5, luồng authorize_action; mục 7.6). Việc 5.

Không gọi LLM (NFR1). Mọi trường hợp không chắc chắn đều ra review (NFR2).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from memgate.labels import Label
from memgate.models import Decision
from memgate.normalize import appears_in, canonical_args, is_number, normalize_value, split_values

if TYPE_CHECKING:
    from memgate.models import AgentContext, Source

# Tham số mang giá trị có thể bị kẻ tấn công cài vào, theo từng tool hành động.
SENSITIVE: dict[str, list[str]] = {
    "send_email": ["to"],
    "wire_transfer": ["to", "amount"],
    "http_post": ["url"],
    "shell": ["cmd"],
    # tool của kịch bản MEM-INV-Bench
    "pay": ["to", "amount"],
    "send_data": ["to"],
    "update_setting": ["key", "value"],
    "purchase": ["to", "item", "amount"],
}
SHORT = 6  # giá trị ngắn hơn thế này (sau chuẩn hoá) hoặc là số thì phải đi cùng nguồn với các giá trị còn lại


def sensitive_values(tool: str, args: dict[str, Any]) -> list[str]:
    """Mỗi người nhận trong trường nhiều giá trị được kiểm tra riêng."""
    args = canonical_args(tool, args)
    return [x for k in SENSITIVE.get(tool, []) if k in args for x in split_values(k, args[k])]


def confirmation_key(tool: str, args: dict[str, Any]) -> tuple:
    args = canonical_args(tool, args)
    return (tool, tuple(sorted((k, normalize_value(x)) for k, v in args.items() if k in SENSITIVE.get(tool, [])
                               for x in split_values(k, v))))


def _is_short(v: str) -> bool:
    return is_number(v) or len(normalize_value(v)) < SHORT


def authorize(ctx: "AgentContext", tool: str, args: dict[str, Any], confirmed: bool = False,
              memory_trusted: "list[Source] | None" = None) -> Decision:
    """1. L >= A (chưa đọc nguồn U trong session): allow.
    2. Mọi giá trị nhạy cảm xuất hiện nguyên văn trong một nguồn T: nguồn T trong source_set, hoặc root
       của một chứng thực có roots toàn T mà agent đã đọc (spec 7.2, memory_trusted). Giá trị số hoặc
       ngắn phải nằm cùng MỘT nguồn T với mọi giá trị còn lại của lời gọi: allow.
    3. Người dùng đã xác nhận đúng hành động này: allow.
    4. Còn lại: review."""
    if tool not in SENSITIVE:
        return Decision(decision="review", reason=f"tool chưa đăng ký với cổng hành động: {tool}")
    if ctx.label >= Label.A:
        return Decision(decision="allow", reason=f"nhãn context {ctx.label.name}: chưa đọc nguồn U")
    if confirmed:
        return Decision(decision="allow", reason="người dùng đã xác nhận hành động")

    values = sensitive_values(tool, args)
    if not values:
        return Decision(decision="review", reason="thiếu tham số nhạy cảm để đối chiếu")
    trusted: list[Source] = ctx.trusted_sources() + list(memory_trusted or [])

    if any(_is_short(v) for v in values):
        # Cần một nguồn T chứa TẤT CẢ giá trị (vd amount phải đi cùng người nhận).
        for s in trusted:
            if all(appears_in(v, s.content) for v in values):
                return Decision(decision="allow", reason=f"mọi giá trị có trong nguồn T {s.locator}")
        missing = [v for v in values if not any(appears_in(v, s.content) for s in trusted)]
        why = f"không có trong nguồn T: {missing}" if missing else "các giá trị không cùng nằm trong một nguồn T"
        return Decision(decision="review", reason=why)

    missing = [v for v in values if not any(appears_in(v, s.content) for s in trusted)]
    if missing:
        return Decision(decision="review", reason=f"không có trong nguồn T: {missing}")
    return Decision(decision="allow", reason="mọi giá trị có trong nguồn T")
