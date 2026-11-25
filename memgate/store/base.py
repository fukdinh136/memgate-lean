"""Interface lưu trữ. core.py chỉ gọi các hàm dưới đây.

Quy ước:
- Các hàm không tự mở transaction; người gọi bọc chúng trong store.transaction().
- transaction() gọi lồng được: lớp ngoài cùng mới BEGIN/COMMIT/ROLLBACK.
- Nhãn context không được lưu riêng: L = min(label) trong context_reads, T nếu chưa đọc gì.
"""
from __future__ import annotations

from datetime import datetime
from typing import ContextManager, Literal, Protocol

import numpy as np

from memgate.labels import Label
from memgate.models import AgentContext, AuditEvent, Edge, MemoryItem, Source, Version

ReadKind = Literal["source", "version"]


class Store(Protocol):
    def transaction(self) -> ContextManager[None]: ...
    def close(self) -> None: ...

    # nguồn
    def add_source(self, src: Source) -> None: ...                    # bỏ qua nếu source_id đã có
    def get_sources(self, ids: list[str]) -> list[Source]: ...        # giữ thứ tự của ids

    # memory
    def create_item(self, item: MemoryItem) -> None: ...
    def get_item(self, item_id: str) -> MemoryItem | None: ...
    def set_item_scope(self, item_id: str, scope: str) -> None: ...         # share/promote (việc 6)
    def set_item_status(self, item_id: str, status: str) -> None: ...       # vòng đời (việc 7, 13)
    def set_item_expires(self, item_id: str, expires_at: datetime | None) -> None: ...  # retention
    def has_dependents(self, item_id: str) -> bool: ...                   # mục khác dẫn xuất từ mục này
    def delete_item(self, item_id: str) -> None: ...                      # short-term hết session
    def add_version(self, v: Version, edges: list[Edge]) -> None: ...   # version + cạnh + current_version_id
    def get_version(self, version_id: str) -> Version | None: ...
    def list_versions(self, item_id: str) -> list[Version]: ...         # cũ -> mới
    def active_versions(self, scopes: list[str], min_label: Label = Label.U, agent: str | None = None,
                        now: datetime | None = None) -> list[Version]: ...  # private chỉ của chính agent;
                                                                            # now: bỏ mục quá expires_at
    def nearest(self, model: str, query: "np.ndarray", scopes: list[str], min_label: Label = Label.U,
                agent: str | None = None, k: int = 5,
                now: datetime | None = None) -> list[Version]: ...  # top-k cosine, chỉ mục có embedding
    def expired_items(self, now: datetime) -> list[str]: ...           # item active có expires_at <= now
    def get_embeddings(self, model: str, hashes: list[str]) -> dict[str, bytes]: ...
    def put_embeddings(self, model: str, vectors: dict[str, bytes]) -> None: ...
    def parents(self, version_id: str) -> list[Edge]: ...
    def roots(self, version_id: str) -> list[Source]: ...               # mọi Source gốc truy được

    # policy (việc 6): YAML là nơi viết, các bảng roles/agents/policies là bản lưu
    def save_policy(self, policy: dict) -> None: ...                      # thay toàn bộ
    def load_policy(self) -> dict | None: ...                             # None nếu chưa lưu

    # audit (append-only, hash chain: memgate/audit.py)
    def append_audit(self, event: AuditEvent) -> AuditEvent: ...          # trả về event có seq, prev_hash, hash
    def list_audit(self, actor: str | None = None, decision: str | None = None) -> list[AuditEvent]: ...
    def rehash_audit(self) -> int: ...                                    # DB cũ chưa có hash

    # jobs của worker (inline ở việc 7, chạy nền ở việc 9)
    def add_job(self, job_id: str, kind: str, ref_id: str, created_at: datetime) -> None: ...
    def queued_jobs(self, limit: int = 100) -> list[dict]: ...           # cũ -> mới
    def set_job_status(self, job_id: str, status: str) -> None: ...      # done | failed

    # context
    def append_context_read(self, agent: str, session: str, kind: ReadKind, ref_id: str, label: Label) -> None: ...
    def load_context(self, agent: str, session: str) -> AgentContext: ...
