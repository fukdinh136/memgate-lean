"""Vòng đời 7 trạng thái, worker inline và retention (việc 7, FR9).

Mọi mục mới (write, trusted fact, bản tóm tắt, mục T do rederive) sinh ở trạng thái `pending` kèm một job
`embed`. Chỉ mục `active` được truy xuất. Worker `inline` gọi process_pending() ngay sau mỗi lần ghi;
việc 9 chuyển hàm này sang tiến trình nền mà không đổi ngữ nghĩa.

Mọi lần chuyển trạng thái là một AuditEvent `op="transition"`, kể cả pending -> active (actor `worker`).
Thời điểm chuyển trạng thái lấy từ audit (không thêm cột ngoài thực thể của đặc tả).

Retention theo bảng mục 6 của đặc tả (chỉ áp khi bật mitigations.expiration):

| Loại        | Thời hạn                                     | Khi hết hạn                                  |
|-------------|----------------------------------------------|----------------------------------------------|
| short_term  | theo session                                 | end_session: xoá mục, chỉ giữ audit          |
| long_term   | 90 ngày không được sử dụng                   | expired, giữ version để audit                |
| retrieved   | 7 ngày nếu có nguồn web (hoặc hash nguồn đổi)| expired                                      |
| shared      | không có TTL cứng                            | kiểm tra cũ khi có version mới từ cùng nguồn |
| quarantined | 30 ngày không được review                    | rejected                                     |

"Hash nguồn đổi" và "version mới hơn từ cùng nguồn" là bộ phát hiện memory cũ (việc 11b); quarantine quá hạn
thuộc việc 8 (review). Số ngày cấu hình được ở `memgate.retention`.
"""
from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from memgate.models import MemoryItem, new_id

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
DEFAULT_RETENTION = {"long_term_idle_days": 90, "retrieved_web_days": 7, "quarantine_days": 30}


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


def _after(gate: "MemGate", key: str):
    days = gate.retention.get(key)
    return None if days is None else gate.clock.now() + timedelta(days=float(days))


def expires_at(gate: "MemGate", type: str):
    """Hạn lúc tạo mục. Chỉ long_term có hạn ngay (90 ngày, gia hạn mỗi lần dùng); retrieved chỉ có hạn khi
    có nguồn web, tính sau khi biết roots (set_retrieved_expiry); short_term theo session; shared không hạn."""
    if not gate.mitigations.expiration or type != "long_term":
        return None
    return _after(gate, "long_term_idle_days")


def set_retrieved_expiry(gate: "MemGate", item: MemoryItem, version_id: str) -> None:
    """Retrieved: 7 ngày nếu roots của version có nguồn web. Gọi trong transaction, sau add_version."""
    if not gate.mitigations.expiration or item.type != "retrieved":
        return
    if any(s.kind == "web" for s in gate.store.roots(version_id)):
        gate.store.set_item_expires(item.item_id, _after(gate, "retrieved_web_days"))


def touch(gate: "MemGate", item_ids: list[str]) -> None:
    """Long-term hết hạn sau 90 ngày KHÔNG được dùng: mỗi lần retrieve trúng hoặc cập nhật thì gia hạn."""
    if not gate.mitigations.expiration:
        return
    for item_id in dict.fromkeys(item_ids):
        item = gate.store.get_item(item_id)
        if item is not None and item.type == "long_term" and item.status == "active":
            gate.store.set_item_expires(item_id, _after(gate, "long_term_idle_days"))


def end_session(gate: "MemGate", item_ids: list[str]) -> list[str]:
    """Short-term hết session: active -> expired, rồi xoá hẳn mục (chỉ giữ audit). Mục đã có mục khác dẫn
    xuất từ nó thì giữ ở expired để không gãy lineage (FR2). Mục quarantined/under_review chờ review (việc 8).
    Trả danh sách mục đã xoá."""
    if not gate.mitigations.expiration:
        return []
    deleted = []
    for item_id in dict.fromkeys(item_ids):
        item = gate.store.get_item(item_id)
        if item is None or item.type != "short_term":
            continue
        if item.status == "active":
            transition(gate, item_id, "expired", WORKER, "short_term hết session")
        elif item.status not in ("pending", "expired"):
            continue
        if gate.store.has_dependents(item_id):
            continue
        with gate.store.transaction():
            gate.store.delete_item(item_id)
            gate._audit(WORKER, "delete", f"item:{item_id}", "allow", "short_term hết session, chỉ giữ audit")
        deleted.append(item_id)
    return deleted


def sweep_expired(gate: "MemGate") -> list[str]:
    """Mục active đã quá expires_at -> expired. Chỉ chạy khi bật mitigations.expiration."""
    if not gate.mitigations.expiration:
        return []
    ids = gate.store.expired_items(gate.clock.now())
    for item_id in ids:
        transition(gate, item_id, "expired", WORKER, "hết hạn retention")
    return ids
