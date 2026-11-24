"""Kiểu dữ liệu dùng chung giữa MemGate và harness."""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, model_validator

from memgate.labels import Label, label_for_kind, meet

SourceKind = Literal["user", "tool_trusted", "web", "mcp", "file", "agent"]
MemoryType = Literal["short_term", "long_term", "retrieved", "shared"]
Scope = Literal["private", "team", "global"]
ItemStatus = Literal["pending", "active", "quarantined", "under_review", "expired", "superseded", "rejected"]


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def now() -> datetime:
    return datetime.now(timezone.utc)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class Source(BaseModel):
    """Một input agent đã đọc. Nội dung (đã redact) được lưu để gate khớp nguyên văn sau khi restart."""

    source_id: str = Field(default_factory=lambda: new_id("src"))
    kind: SourceKind
    locator: str
    content: str
    label: Label | None = None
    content_hash: str | None = None   # hash của nội dung GỐC, tính trước khi redact
    domain: str | None = None         # mặc định: host của URL, nếu không thì chính kind
    base_trust: float | None = None   # việc 12

    @model_validator(mode="after")
    def _fill(self) -> "Source":
        # Không ai tự nâng nhãn được: nhãn khai báo bị kẹp bởi nhãn của kênh.
        # Nguồn kind=agent (message) phải mang nhãn tường minh, thiếu thì U.
        if self.kind == "agent":
            self.label = self.label if self.label is not None else Label.U
        else:
            kind_label = label_for_kind(self.kind)
            self.label = kind_label if self.label is None else meet(self.label, kind_label)
        if self.content_hash is None:
            self.content_hash = sha256(self.content)
        if self.domain is None:
            host = urlparse(self.locator).hostname if "://" in self.locator else None
            self.domain = host or self.kind
        return self


class MemoryItem(BaseModel):
    """Danh tính logic của một mục memory; nội dung nằm trong các Version bất biến."""

    item_id: str = Field(default_factory=lambda: new_id("item"))
    type: MemoryType
    scope: Scope
    owner_agent_id: str
    current_version_id: str | None = None
    status: ItemStatus = "active"
    expires_at: datetime | None = None
    created_at: datetime = Field(default_factory=now)


class Version(BaseModel):
    version_id: str = Field(default_factory=lambda: new_id("ver"))
    item_id: str
    content: str
    content_hash: str | None = None
    label: Label
    created_by: str
    created_at: datetime = Field(default_factory=now)
    reason: Literal["write", "consolidate", "rollback"] = "write"
    embedding_ref: str | None = None  # việc 4b
    trust_score: float | None = None  # việc 12

    @model_validator(mode="after")
    def _hash(self) -> "Version":
        if self.content_hash is None:
            self.content_hash = sha256(self.content)
        return self


class Edge(BaseModel):
    """Cạnh lineage: version con trỏ về source hoặc version cha."""

    child_version_id: str
    parent_kind: Literal["source", "version"]
    parent_id: str
    relation: Literal["derived_from", "read_in_context", "corroborates", "contradicts"]


@dataclass
class AgentContext:
    """Trạng thái runtime của một agent trong một session.

    label luôn bằng min nhãn của mọi thứ đã đọc (T nếu chưa đọc gì); store tính lại từ
    bảng context_reads khi khởi động lại, nên không bao giờ khôi phục nhãn cao hơn nhãn thật.
    """

    agent: str
    session: str
    label: Label = Label.T
    sources: list[Source] = field(default_factory=list)
    version_ids: list[str] = field(default_factory=list)

    @property
    def source_ids(self) -> list[str]:
        return [s.source_id for s in self.sources]

    def trusted_sources(self) -> list[Source]:
        return [s for s in self.sources if s.label == Label.T]


class Message(BaseModel):
    message_id: str = Field(default_factory=lambda: new_id("msg"))
    sender: str
    receiver: str
    session: str
    content: str
    label: Label


class AuditEvent(BaseModel):
    """Một quyết định của MemGate (mục 6 của đặc tả). Append-only; prev_hash/hash điền ở việc 7."""

    seq: int | None = None          # store cấp khi ghi
    ts: datetime
    actor: str
    op: str                         # write | retrieve | share | rollback | ...
    target: str
    decision: Literal["allow", "deny", "review"]
    reason: str = ""
    prev_hash: str | None = None
    hash: str | None = None


class Decision(BaseModel):
    decision: Literal["allow", "deny", "review"]
    reason: str = ""
    task_id: str | None = None

    @property
    def allowed(self) -> bool:
        return self.decision == "allow"
