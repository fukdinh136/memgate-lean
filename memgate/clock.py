"""Đồng hồ truyền vào được. Không module nào đọc giờ hệ thống trực tiếp (NFR8)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FakeClock:
    """Đồng hồ giả lập cho test và thí nghiệm: chỉ chạy khi gọi advance()."""

    def __init__(self, start: datetime | None = None) -> None:
        self._t = start or datetime(2026, 1, 1, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self._t

    def advance(self, **delta: float) -> datetime:
        self._t += timedelta(**delta)
        return self._t
