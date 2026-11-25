"""Vòng đời 7 trạng thái, worker inline và retention (việc 7, FR9).

Mọi mục mới (write, trusted fact, bản tóm tắt, mục T do rederive) sinh ở trạng thái `pending` kèm một job
`embed`. Chỉ mục `active` được truy xuất. Worker `inline` gọi process_pending() ngay sau mỗi lần ghi;
việc 9 chuyển hàm này sang tiến trình nền mà không đổi ngữ nghĩa.

Mọi lần chuyển trạng thái là một AuditEvent `op="transition"`, kể cả pending -> active (actor `worker`).
Thời điểm chuyển trạng thái lấy từ audit (không thêm cột ngoài thực thể của đặc tả).

Quét quarantine quá 30 ngày -> rejected thuộc việc 8 (review); bảng dưới đã có sẵn cạnh đó.
"""
from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from memgate.models import new_id

if TYPE_CHECKING:
    from memgate.core import MemGate

STATUSES = ("pending", "active", "quarantined", "under_review", "expired", "superseded", "rejected")
TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"active", "quarantined", "under_review", "rejected"}),
    "active": frozenset({"quarantined", "under_review", "expired", "superseded"}),
    "quarantined": frozenset({"under_review", "active", "rejected"}),
    "under_review": frozenset({"active", "rejected"}),
    "expired": frozenset(),
    "superseded": frozenset(),
    "rejected": frozenset(),
}
TERMINAL = frozenset(s for s, nxt in TRANSITIONS.items() if not nxt)
WORKER = "worker"


class InvalidTransition(ValueError):
    pass


def can_transition(src: str, dst: str) -> bool:
    return dst in TRANSITIONS.get(src, frozenset())


def transition(gate: "MemGate", item_id: str, to: str, actor: str, reason: str = "") -> str:
    """Đổi trạng thái theo bảng TRANSITIONS và ghi audit. Trả trạng thái cũ. Sai bảng: audit deny + lỗi."""
    item = gate.store.get_item(item_id)
    if item is None:
        raise KeyError(f"item không tồn tại: {item_id}")
    src = item.status
    target = f"item:{item_id}"
    if not can_transition(src, to):
        gate._audit(actor, "transition", target, "deny", f"{src}->{to} không hợp lệ")
        raise InvalidTransition(f"{item_id}: {src} -> {to} không hợp lệ")
    with gate.store.transaction():
        gate.store.set_item_status(item_id, to)
        gate._audit(actor, "transition", target, "allow", f"{src}->{to}" + (f": {reason}" if reason else ""))
    return src


def enqueue_embed(gate: "MemGate", version_id: str) -> None:
    gate.store.add_job(new_id("job"), "embed", version_id, gate.clock.now())


def process_pending(gate: "MemGate", limit: int = 100) -> int:
    """Worker: tính embedding cho version trong hàng đợi; mục còn pending thì chuyển active.
    Lỗi: job failed, mục ở lại pending (không được truy xuất, fail-closed), rồi ném lỗi ra."""
    done = 0
    while jobs := gate.store.queued_jobs(limit):
        for job in jobs:
            if job["kind"] != "embed":
                raise ValueError(f"job chưa hỗ trợ: {job['kind']}")
            try:
                v = gate.store.get_version(job["ref_id"])
                if v is None:
                    raise KeyError(f"version không tồn tại: {job['ref_id']}")
                gate._embeddings([v])
            except Exception:
                with gate.store.transaction():
                    gate.store.set_job_status(job["job_id"], "failed")
                raise
            with gate.store.transaction():
                gate.store.set_job_status(job["job_id"], "done")
                item = gate.store.get_item(v.item_id)
                if item is not None and item.status == "pending":
                    transition(gate, item.item_id, "active", WORKER, "embedding xong")
            done += 1
    return done


def expires_at(gate: "MemGate", type: str):
    """Hạn của mục mới theo retention (chỉ khi bật mitigations.expiration); None = không hết hạn."""
    if not gate.mitigations.expiration:
        return None
    days = gate.retention_days.get(type)
    return None if days is None else gate.clock.now() + timedelta(days=float(days))


def sweep_expired(gate: "MemGate") -> list[str]:
    """Mục active đã quá expires_at -> expired. Chỉ chạy khi bật mitigations.expiration."""
    if not gate.mitigations.expiration:
        return []
    ids = gate.store.expired_items(gate.clock.now())
    for item_id in ids:
        transition(gate, item_id, "expired", WORKER, "hết hạn retention")
    return ids
