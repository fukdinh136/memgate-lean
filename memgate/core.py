"""MemGate: nhãn context, ghi memory, versioning, lineage (việc 4a). retrieve là việc 4b.
Vòng đời, worker inline, retention, redact, công tắc biện pháp: việc 7."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from memgate import action_gate, lifecycle
import numpy as np

from memgate.clock import Clock, SystemClock
from memgate.embed import Embedder, make_embedder
from memgate.labels import Label, meet, output_label
from memgate.mitigations import Mitigations
from memgate.models import AgentContext, AuditEvent, Decision, Edge, MemoryItem, Message, Source, Version
from memgate.policy import SCOPE_RANK, SCOPES, PolicyDenied, PolicyEngine
from memgate.redact import redact
from memgate.store import Store, open_store

if TYPE_CHECKING:
    from memgate.consolidate import ConsolidationResult, Rederiver, Summarizer

__all__ = ["MemGate", "AgentContext"]



class MemGate:
    def __init__(self, store: Store | None = None, clock: Clock | None = None,
                 config: dict | None = None, embedder: Embedder | None = None,
                 policy: PolicyEngine | None = None) -> None:
        self.store = store or open_store(":memory:")
        self.clock = clock or SystemClock()
        # Policy truyền vào được lưu xuống store; không truyền thì dùng bản đã lưu, chưa có thì mặc định.
        if policy is None:
            saved = self.store.load_policy()
            policy = PolicyEngine.from_dict(saved) if saved is not None else PolicyEngine.default()
            if saved is None:
                self.store.save_policy(policy.to_dict())
        else:
            self.store.save_policy(policy.to_dict())
        self.policy = policy
        self.embedder = embedder or make_embedder((config or {}).get("embedding_model"))
        self.config = config or {}
        self.mitigations = Mitigations.from_config(self.config.get("mitigations"))
        self.retention = {**lifecycle.DEFAULT_RETENTION, **(self.config.get("retention") or {})}
        if unknown := set(self.retention) - set(lifecycle.DEFAULT_RETENTION):
            raise ValueError(f"khoá retention không biết: {sorted(unknown)}")
        self.worker = self.config.get("worker", "inline")
        if self.worker != "inline":
            raise NotImplementedError(f"worker {self.worker!r}: chạy nền ở việc 9, hiện chỉ có 'inline'")
        self._contexts: dict[tuple[str, str], AgentContext] = {}
        self._confirmed: set[tuple] = set()  # TODO(việc 8): chuyển sang hàng đợi review

    # ---------- nhãn context (đã xong) ----------

    def context(self, agent: str, session: str) -> AgentContext:
        """Context trong bộ nhớ; chưa có thì khôi phục từ store (nhãn = min những gì đã đọc)."""
        key = (agent, session)
        if key not in self._contexts:
            self._contexts[key] = self.store.load_context(agent, session)
        return self._contexts[key]

    def context_label(self, agent: str, session: str) -> Label:
        return self.context(agent, session).label

    def observe(self, agent: str, session: str, source: Source) -> Label:
        """L(a) <- min(L(a), label(x)). Hạ nhãn trong bộ nhớ trước (luôn an toàn), rồi ghi xuống store."""
        ctx = self.context(agent, session)
        src = source.model_copy(update={"content": redact(source.content)})
        ctx.label = meet(ctx.label, src.label)
        ctx.sources.append(src)
        with self.store.transaction():
            self.store.add_source(src)
            self.store.append_context_read(agent, session, "source", src.source_id, src.label)
        return ctx.label

    def _read_version(self, ctx: AgentContext, v: Version) -> None:
        """Ghi nhận agent đã đọc một version (retrieve, việc 4b dùng)."""
        ctx.label = meet(ctx.label, v.label)
        ctx.version_ids.append(v.version_id)
        self.store.append_context_read(ctx.agent, ctx.session, "version", v.version_id, v.label)

    def send_message(self, sender: str, receiver: str, session: str, content: str) -> Message:
        """Message mang nhãn output của người gửi; người nhận observe nó ngay (chặn K1).
        Tắt mitigations.boundary_labeling (TMA-NM): message mang nhãn T như framework thường làm."""
        label = output_label(self.context_label(sender, session)) if self.mitigations.boundary_labeling else Label.T
        msg = Message(sender=sender, receiver=receiver, session=session, content=content, label=label)
        self.observe(
            receiver,
            session,
            Source(kind="agent", locator=f"msg:{msg.message_id}", content=content, label=label),
        )
        return msg

    # ---------- memory (việc 4) ----------

    def write(self, agent: str, session: str, type: str, scope: str, content: str,
              item_id: str | None = None) -> Version:
        """Ghi một version mới, không ghi đè. Nhãn = min(A, L). Mỗi thứ agent đã đọc trong session
        thành một cạnh read_in_context; cập nhật item có thêm cạnh derived_from về version trước."""
        # Kiểm quyền trước transaction: lần từ chối được ghi audit dù không có gì được ghi.
        if item_id is None:
            self._enforce(agent, "write", "write", scope, f"scope:{scope}", owner=agent)
        else:
            existing = self.store.get_item(item_id)
            if existing is None:
                raise KeyError(f"item không tồn tại: {item_id}")
            self._enforce(agent, "write", "write", existing.scope, f"item:{item_id}", owner=existing.owner_agent_id)
            self._require_status(agent, "write", existing, ("active",))
        ctx = self.context(agent, session)
        now = self.clock.now()
        with self.store.transaction():
            if item_id is None:
                item = self._new_item(type, scope, agent)        # pending tới khi worker tính embedding
            else:
                item = self.store.get_item(item_id)
                if item is None:
                    raise KeyError(f"item không tồn tại: {item_id}")
            v = Version(item_id=item.item_id, content=redact(content), label=output_label(ctx.label),
                        created_by=agent, created_at=now, reason="write")
            edges = self._context_edges(ctx, v, agent)
            if item.current_version_id:
                edges.append(Edge(child_version_id=v.version_id, parent_kind="version",
                                  parent_id=item.current_version_id, relation="derived_from"))
            self.add_version(v, edges)
            lifecycle.set_retrieved_expiry(self, item, v.version_id)
            if item_id is not None:
                lifecycle.touch(self, [item_id])                 # cập nhật = được dùng
        if item_id is None and type == "short_term":
            ctx.short_term_items.append(item.item_id)
        self.run_worker()
        return v

    # ---------- vòng đời (việc 7) ----------

    def _new_item(self, type: str, scope: str, owner: str) -> MemoryItem:
        """Lối tạo mục duy nhất: pending, hạn theo retention (khi bật expiration). Gọi trong transaction."""
        item = MemoryItem(type=type, scope=scope, owner_agent_id=owner, status="pending",
                          expires_at=lifecycle.expires_at(self, type), created_at=self.clock.now())
        self.store.create_item(item)
        return item

    def add_version(self, v: Version, edges: list[Edge]) -> None:
        """Ghi version và xếp job embedding cho worker. Gọi trong transaction."""
        self.store.add_version(v, edges)
        lifecycle.enqueue_embed(self, v.version_id)

    def run_worker(self) -> int:
        """Worker inline: xử lý hàng đợi ngay tại chỗ (việc 9 chuyển sang tiến trình nền)."""
        return lifecycle.process_pending(self)

    def sweep_expired(self) -> list[str]:
        return lifecycle.sweep_expired(self)

    def end_session(self, session: str) -> list[str]:
        """Kết thúc session: xoá mục short_term ghi trong session (retention, mục 6) và bỏ AgentContext
        runtime của session. Danh sách mục short_term chỉ nằm trong bộ nhớ tiến trình (AgentContext)."""
        keys = [k for k in self._contexts if k[1] == session]
        items = [i for k in keys for i in self._contexts[k].short_term_items]
        deleted = lifecycle.end_session(self, items)
        for k in keys:
            del self._contexts[k]
        return deleted

    def transition(self, item_id: str, to: str, actor: str, reason: str = "") -> str:
        return lifecycle.transition(self, item_id, to, actor, reason)

    def _now_filter(self):
        """Mốc giờ để store bỏ mục quá hạn chưa được quét; None khi tắt expiration."""
        return self.clock.now() if self.mitigations.expiration else None

    def _require_status(self, agent: str, op: str, item: MemoryItem, allowed: tuple[str, ...]) -> None:
        if item.status not in allowed:
            self._deny(agent, op, f"item:{item.item_id}",
                       f"trạng thái {item.status}, cần {' hoặc '.join(allowed)}")

    def _context_edges(self, ctx: AgentContext, v: Version, agent: str) -> list[Edge]:
        edges = [Edge(child_version_id=v.version_id, parent_kind="source", parent_id=sid,
                      relation="read_in_context") for sid in dict.fromkeys(ctx.source_ids)]
        edges += [Edge(child_version_id=v.version_id, parent_kind="version", parent_id=vid,
                       relation="read_in_context") for vid in dict.fromkeys(ctx.version_ids)]
        if not ctx.sources:
            # Chưa đọc nguồn nào: gốc là chính agent (nhãn A), để mọi version đều truy ngược được (FR2).
            self_src = Source(kind="agent", locator=f"agent:{agent}", content="", label=Label.A)
            self.store.add_source(self_src)
            edges.append(Edge(child_version_id=v.version_id, parent_kind="source",
                              parent_id=self_src.source_id, relation="read_in_context"))
        return edges

    def ingest_trusted(self, content: str, scope: str = "global", locator: str = "trusted_fact",
                       type: str = "long_term", kind: str = "user") -> Version:
        """Nạp sự thật từ kênh đã xác thực (trusted_facts của kịch bản) với nhãn T."""
        now = self.clock.now()
        src = Source(kind=kind, locator=locator, content=content)   # hash trên nội dung gốc
        src = src.model_copy(update={"content": redact(content)})
        with self.store.transaction():
            self.store.add_source(src)
            item = self._new_item(type, scope, "system")
            v = Version(item_id=item.item_id, content=src.content, label=src.label,
                        created_by="system", created_at=now, reason="write")
            self.add_version(v, [Edge(child_version_id=v.version_id, parent_kind="source",
                                      parent_id=src.source_id, relation="read_in_context")])
        self.run_worker()
        return v

    def retrieve(
        self, agent: str, session: str, query: str, k: int = 5, min_label: Label = Label.U,
        scopes: list[str] | None = None,
    ) -> list[Version]:
        """Top-k version active theo cosine với query; chỉ lấy mục có nhãn >= min_label.
        Mỗi mục trả về được ghi là đã đọc, nên L hạ xuống nhãn thấp nhất trong kết quả."""
        scopes = scopes or list(SCOPES)
        allowed = [s for s in scopes if self.policy.check(agent, "read", s).allowed]
        denied = [s for s in scopes if s not in allowed]
        if denied:   # lọc bỏ, không ném lỗi; một dòng audit cho mỗi lần gọi
            self._audit(agent, "retrieve", "scopes:" + ",".join(denied), "deny",
                        "; ".join(self.policy.check(agent, "read", s).reason for s in denied))
        # Mục private của agent khác do store lọc (owner), policy không ghi đè được.
        now = self._now_filter()
        cands = self.store.active_versions(allowed, min_label, agent=agent, now=now)
        if not cands or k <= 0:
            return []
        self._embeddings(cands)          # bảo đảm mọi ứng viên có embedding trước khi tìm
        q = self.embedder.encode([query], is_query=True)[0]
        # pgvector (<=>) trên PostgreSQL, numpy trên SQLite. TODO(việc 12): nhân với lambda + (1 - lambda) * trust
        hits = self.store.nearest(self.embedder.name, q, allowed, min_label, agent=agent, k=k, now=now)
        ctx = self.context(agent, session)
        with self.store.transaction():
            for v in hits:
                self._read_version(ctx, v)
            lifecycle.touch(self, [v.item_id for v in hits])     # long_term: gia hạn khi được dùng
        return hits

    def _embeddings(self, versions: list[Version]) -> np.ndarray:
        """Embedding theo content_hash, có cache trong store. TODO(việc 9): worker tính trước."""
        model = self.embedder.name
        hashes = [v.content_hash for v in versions]
        have = self.store.get_embeddings(model, hashes)
        missing = [v for v in {v.content_hash: v for v in versions}.values() if v.content_hash not in have]
        if missing:
            arr = self.embedder.encode([v.content for v in missing])
            new = {v.content_hash: arr[i].astype(np.float32).tobytes() for i, v in enumerate(missing)}
            with self.store.transaction():
                self.store.put_embeddings(model, new)
            have.update(new)
        return np.stack([np.frombuffer(have[h], dtype=np.float32) for h in hashes])

    def history(self, item_id: str) -> list[Version]:
        return self.store.list_versions(item_id)

    def rollback(self, item_id: str, version_id: str, agent: str) -> Version:
        """Version mới mang nội dung và nhãn của version cũ; không lấy nhãn context của agent vì
        agent chỉ khôi phục dữ liệu đã có. Cần quyền write trên scope của item."""
        old = self.store.get_version(version_id)
        if old is None or old.item_id != item_id:
            raise KeyError(f"version {version_id} không thuộc item {item_id}")
        item = self.store.get_item(item_id)
        self._enforce(agent, "rollback", "write", item.scope, f"item:{item_id}", owner=item.owner_agent_id)
        # Trạng thái cuối (expired, superseded, rejected) không được hồi sinh; trạng thái giữ nguyên.
        self._require_status(agent, "rollback", item, ("active", "quarantined"))
        with self.store.transaction():
            v = Version(item_id=item_id, content=old.content, label=old.label, created_by=agent,
                        created_at=self.clock.now(), reason="rollback")
            self.add_version(v, [Edge(child_version_id=v.version_id, parent_kind="version",
                                      parent_id=old.version_id, relation="derived_from")])
        self.run_worker()
        return v

    # ---------- policy, share, audit (việc 6) ----------

    def share(self, agent: str, session: str, item_id: str, to_scope: str) -> MemoryItem:
        """Promote item lên scope rộng hơn (private → team → global). Giữ nguyên nhãn và mọi version;
        chỉ đổi scope của item. Item private chỉ chủ sở hữu được promote."""
        item = self.store.get_item(item_id)
        if item is None:
            raise KeyError(f"item không tồn tại: {item_id}")
        target = f"item:{item_id}->{to_scope}"
        if to_scope not in SCOPES or SCOPE_RANK[to_scope] <= SCOPE_RANK[item.scope]:
            self._deny(agent, "share", target, f"chỉ mở rộng scope ({item.scope} -> {to_scope} không hợp lệ)")
        if item.scope == "private" and item.owner_agent_id != agent:
            self._deny(agent, "share", target, f"mục private của {item.owner_agent_id}")
        if item.status != "active":
            self._deny(agent, "share", target, f"trạng thái {item.status}, chỉ promote mục active")
        self._enforce(agent, "share", "promote", to_scope, target, audit_allow=True)
        with self.store.transaction():
            self.store.set_item_scope(item_id, to_scope)
        return self.store.get_item(item_id)

    def consolidate(self, scope: str, mode: str, summarize: "Summarizer",
                    rederiver: "Rederiver | None" = None) -> "ConsolidationResult":
        """Hợp nhất các mục active trong scope (việc 13, 14). Xem memgate/consolidate.py."""
        from memgate.consolidate import consolidate
        return consolidate(self, scope, mode, summarize, rederiver)

    def audit_log(self, actor: str | None = None, decision: str | None = None) -> list[AuditEvent]:
        return self.store.list_audit(actor=actor, decision=decision)

    def _audit(self, actor: str, op: str, target: str, decision: str, reason: str) -> None:
        self.store.append_audit(AuditEvent(ts=self.clock.now(), actor=actor, op=op, target=redact(target),
                                           decision=decision, reason=redact(reason)))

    def _deny(self, agent: str, op: str, target: str, reason: str) -> None:
        self._audit(agent, op, target, "deny", reason)
        raise PolicyDenied(agent, op, target, reason)

    def _enforce(self, agent: str, op: str, action: str, scope: str, target: str,
                 owner: str | None = None, audit_allow: bool = False) -> None:
        c = self.policy.check(agent, action, scope, owner=owner)
        if not c.allowed:
            self._deny(agent, op, target, c.reason)
        if audit_allow:
            self._audit(agent, op, target, "allow", c.reason)

    # ---------- hành động (việc 5) ----------

    def _trusted_memory_roots(self, ctx: AgentContext) -> list[Source]:
        """Spec 7.2: quyền hành động chỉ dựa vào chứng thực có roots toàn là nguồn T. Mỗi version agent đã
        đọc trong session mà mục còn active và mọi root mang nhãn T thì các root đó vào kho nguồn T."""
        if ctx.label >= Label.A:
            return []                    # luật 1 đã allow, khỏi truy roots
        out: dict[str, Source] = {}
        for vid in dict.fromkeys(ctx.version_ids):
            v = self.store.get_version(vid)
            item = self.store.get_item(v.item_id) if v is not None else None
            if item is None or item.status != "active":
                continue
            roots = self.store.roots(vid)
            if roots and all(s.label == Label.T for s in roots):
                out.update({s.source_id: s for s in roots})
        return list(out.values())

    def confirm_action(self, agent: str, session: str, tool: str, args: dict[str, Any]) -> None:
        """Người dùng xác nhận đúng một hành động (đúng tool, đúng giá trị nhạy cảm) trong session."""
        self._confirmed.add((agent, session, action_gate.confirmation_key(tool, args)))

    def authorize(self, agent: str, session: str, tool: str, args: dict[str, Any]) -> Decision:
        """Mỗi quyết định ghi một dòng audit. Tắt mitigations.action_gate: luôn allow (ablation)."""
        if self.mitigations.action_gate:
            confirmed = (agent, session, action_gate.confirmation_key(tool, args)) in self._confirmed
            ctx = self.context(agent, session)
            d = action_gate.authorize(ctx, tool, args, confirmed=confirmed,
                                      memory_trusted=self._trusted_memory_roots(ctx))
        else:
            d = Decision(decision="allow", reason="action_gate tắt (ablation)")
        self._audit(agent, "authorize", f"{tool} {json.dumps(args, ensure_ascii=False, default=str)}",
                    d.decision, d.reason)
        return d
