"""Audit hash chain (NFR4, việc 7).

Mỗi AuditEvent nối chuỗi: hash = sha256(prev_hash + JSON chuẩn của {seq, ts, actor, op, target, decision,
reason}); dòng đầu có prev_hash = GENESIS. Store tính hash trong cùng khoá với lần chèn (SQLite:
BEGIN IMMEDIATE; PostgreSQL: pg_advisory_xact_lock), nên nhiều tiến trình ghi cùng lúc không gãy chuỗi.

verify() phát hiện: sửa một trường, xoá một dòng giữa chuỗi, đổi thứ tự (seq nằm trong hash), dòng chưa có
hash. Không phát hiện được việc cắt bỏ các dòng CUỐI chuỗi (cần neo hash đầu chuỗi ra ngoài; ghi ở notes).

    python -m memgate.audit verify --store sqlite:///.cache/memgate.sqlite
    python -m memgate.audit rehash --store postgresql://...  --schema public   # DB cũ chưa có hash
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import timezone
from typing import Iterable

from memgate.models import AuditEvent

GENESIS = "0" * 64


def canonical(e: AuditEvent) -> str:
    ts = e.ts if e.ts.tzinfo else e.ts.replace(tzinfo=timezone.utc)
    return json.dumps({"seq": e.seq, "ts": ts.astimezone(timezone.utc).isoformat(timespec="microseconds"),
                       "actor": e.actor, "op": e.op, "target": e.target, "decision": e.decision,
                       "reason": e.reason}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def event_hash(prev_hash: str, e: AuditEvent) -> str:
    return hashlib.sha256((prev_hash + canonical(e)).encode("utf-8")).hexdigest()


@dataclass
class VerifyResult:
    ok: bool
    count: int
    bad_seq: int | None = None
    reason: str = ""

    def __str__(self) -> str:
        if self.ok:
            return f"OK: {self.count} sự kiện, chuỗi nguyên vẹn"
        return f"HỎNG tại seq {self.bad_seq}: {self.reason}"


def verify(events: Iterable[AuditEvent]) -> VerifyResult:
    """events theo thứ tự seq tăng dần (list_audit() không lọc)."""
    prev, n, last_seq = GENESIS, 0, None
    for e in events:
        n += 1
        if last_seq is not None and e.seq <= last_seq:
            return VerifyResult(False, n, e.seq, f"seq không tăng (sau {last_seq})")
        if e.hash is None or e.prev_hash is None:
            return VerifyResult(False, n, e.seq, "dòng chưa có hash")
        if e.prev_hash != prev:
            return VerifyResult(False, n, e.seq, "prev_hash không khớp hash dòng trước (dòng bị xoá hoặc chèn)")
        if e.hash != event_hash(prev, e):
            return VerifyResult(False, n, e.seq, "hash không khớp nội dung (dòng bị sửa)")
        prev, last_seq = e.hash, e.seq
    return VerifyResult(True, n)


def chain(events: Iterable[AuditEvent]) -> list[tuple[int, str, str]]:
    """(seq, prev_hash, hash) tính lại từ đầu cho các dòng đã có; dùng cho rehash."""
    out, prev = [], GENESIS
    for e in events:
        h = event_hash(prev, e)
        out.append((e.seq, prev, h))
        prev = h
    return out


def main(argv: list[str] | None = None) -> int:
    from memgate.store import open_store

    p = argparse.ArgumentParser(prog="python -m memgate.audit")
    p.add_argument("command", choices=["verify", "rehash"])
    p.add_argument("--store", required=True, help="sqlite:///đường/dẫn.db hoặc postgresql://...")
    p.add_argument("--schema", help="schema PostgreSQL (mặc định public)")
    args = p.parse_args(argv)
    store = open_store(args.store, schema=args.schema)
    try:
        if args.command == "rehash":
            print(f"đã tính hash cho {store.rehash_audit()} dòng")
        res = verify(store.list_audit())
        print(res)
        return 0 if res.ok else 1
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
