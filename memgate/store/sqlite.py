"""Backend SQLite cho phát triển và unit test. Demo/thí nghiệm dùng PostgreSQL (việc 4c)."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import numpy as np

from memgate import audit
from memgate.labels import Label
from memgate.models import AgentContext, AuditEvent, Edge, MemoryItem, Source, Version
from memgate.store.base import ReadKind

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    source_id    TEXT PRIMARY KEY,
    kind         TEXT NOT NULL,
    locator      TEXT NOT NULL,
    content      TEXT NOT NULL,          -- đã redact
    content_hash TEXT NOT NULL,          -- hash nội dung gốc
    domain       TEXT NOT NULL,
    base_trust   REAL,
    label        INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS items (
    item_id            TEXT PRIMARY KEY,
    type               TEXT NOT NULL CHECK (type IN ('short_term','long_term','retrieved','shared')),
    scope              TEXT NOT NULL CHECK (scope IN ('private','team','global')),
    owner_agent_id     TEXT NOT NULL,
    current_version_id TEXT,
    status             TEXT NOT NULL,
    expires_at         TEXT,
    created_at         TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS versions (
    version_id    TEXT PRIMARY KEY,
    item_id       TEXT NOT NULL REFERENCES items(item_id),
    content       TEXT NOT NULL,
    content_hash  TEXT NOT NULL,
    embedding_ref TEXT,
    label         INTEGER NOT NULL,
    trust_score   REAL,
    created_by    TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    reason        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS edges (
    child_version_id TEXT NOT NULL REFERENCES versions(version_id),
    parent_kind      TEXT NOT NULL CHECK (parent_kind IN ('source','version')),
    parent_id        TEXT NOT NULL,
    relation         TEXT NOT NULL,
    PRIMARY KEY (child_version_id, parent_kind, parent_id, relation)
);
CREATE TABLE IF NOT EXISTS context_reads (
    agent   TEXT NOT NULL,
    session TEXT NOT NULL,
    seq     INTEGER NOT NULL,
    kind    TEXT NOT NULL CHECK (kind IN ('source','version')),
    ref_id  TEXT NOT NULL,
    label   INTEGER NOT NULL,
    PRIMARY KEY (agent, session, seq)
);
CREATE TABLE IF NOT EXISTS embeddings (
    model        TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    vector       BLOB NOT NULL,          -- float32, đã chuẩn hoá L2
    PRIMARY KEY (model, content_hash)
);
CREATE TABLE IF NOT EXISTS roles (
    role_id TEXT PRIMARY KEY,
    name    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agents (
    agent_id  TEXT PRIMARY KEY,
    role_id   TEXT NOT NULL REFERENCES roles(role_id),
    name      TEXT NOT NULL,
    framework TEXT
);
CREATE TABLE IF NOT EXISTS policies (
    policy_id TEXT PRIMARY KEY,
    ord       INTEGER NOT NULL,          -- thứ tự trong file YAML
    role_id   TEXT NOT NULL,             -- vai trò hoặc '*'
    scope     TEXT NOT NULL CHECK (scope IN ('private','team','global','*')),
    action    TEXT NOT NULL CHECK (action IN ('read','write','promote','*')),
    effect    TEXT NOT NULL CHECK (effect IN ('allow','deny')),
    condition TEXT
);
CREATE TABLE IF NOT EXISTS policy_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS audit_events (
    seq       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    actor     TEXT NOT NULL,
    op        TEXT NOT NULL,
    target    TEXT NOT NULL,
    decision  TEXT NOT NULL CHECK (decision IN ('allow','deny','review')),
    reason    TEXT NOT NULL,
    prev_hash TEXT,
    hash      TEXT
);
CREATE TABLE IF NOT EXISTS jobs (
    job_id     TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,             -- embed (việc 7); nli, consolidate, ttl ở việc 9
    ref_id     TEXT NOT NULL,             -- version_id
    status     TEXT NOT NULL CHECK (status IN ('queued','done','failed')),
    attempts   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, created_at);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_events(actor, decision);
CREATE INDEX IF NOT EXISTS idx_versions_item ON versions(item_id, created_at);
CREATE INDEX IF NOT EXISTS idx_items_status ON items(status, expires_at);
CREATE INDEX IF NOT EXISTS idx_edges_child ON edges(child_version_id);
CREATE INDEX IF NOT EXISTS idx_edges_parent ON edges(parent_id);
CREATE INDEX IF NOT EXISTS idx_sources_hash ON sources(content_hash);
"""


def _dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


def _ts(d: datetime | None) -> str | None:
    """Mốc thời gian so sánh được dạng chuỗi (expires_at): UTC, luôn đủ micro giây."""
    if d is None:
        return None
    d = d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).isoformat(timespec="microseconds")


class SQLiteStore:
    def __init__(self, path: str | Path = ":memory:", busy_timeout_ms: int = 5000) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None: tự quản lý BEGIN/COMMIT để dùng BEGIN IMMEDIATE.
        self.conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
        self.conn.execute("PRAGMA foreign_keys = ON")
        if str(path) != ":memory:":
            self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(SCHEMA)
        self._depth = 0
        if self.conn.execute("SELECT 1 FROM audit_events WHERE hash IS NULL LIMIT 1").fetchone():
            self.rehash_audit()     # DB từ trước việc 7: nối chuỗi cho các dòng cũ

    def close(self) -> None:
        self.conn.close()

    # ---------- transaction ----------

    @contextmanager
    def transaction(self) -> Iterator[None]:
        if self._depth == 0:
            self.conn.execute("BEGIN IMMEDIATE")
        self._depth += 1
        try:
            yield
        except BaseException:
            self._depth -= 1
            if self._depth == 0:
                self.conn.execute("ROLLBACK")
            raise
        else:
            self._depth -= 1
            if self._depth == 0:
                self.conn.execute("COMMIT")

    # ---------- nguồn ----------

    def add_source(self, src: Source) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO sources VALUES (?,?,?,?,?,?,?,?)",
            (src.source_id, src.kind, src.locator, src.content, src.content_hash,
             src.domain, src.base_trust, int(src.label)),
        )

    def get_sources(self, ids: list[str]) -> list[Source]:
        if not ids:
            return []
        q = ",".join("?" * len(set(ids)))
        rows = {r["source_id"]: r for r in self.conn.execute(
            f"SELECT * FROM sources WHERE source_id IN ({q})", list(set(ids)))}
        return [self._source(rows[i]) for i in ids if i in rows]

    @staticmethod
    def _source(r: sqlite3.Row) -> Source:
        return Source(source_id=r["source_id"], kind=r["kind"], locator=r["locator"], content=r["content"],
                      content_hash=r["content_hash"], domain=r["domain"], base_trust=r["base_trust"],
                      label=Label(r["label"]))

    # ---------- memory ----------

    def create_item(self, item: MemoryItem) -> None:
        self.conn.execute(
            "INSERT INTO items VALUES (?,?,?,?,?,?,?,?)",
            (item.item_id, item.type, item.scope, item.owner_agent_id, item.current_version_id,
             item.status, _ts(item.expires_at), item.created_at.isoformat()),
        )

    def get_item(self, item_id: str) -> MemoryItem | None:
        r = self.conn.execute("SELECT * FROM items WHERE item_id = ?", (item_id,)).fetchone()
        if r is None:
            return None
        return MemoryItem(item_id=r["item_id"], type=r["type"], scope=r["scope"],
                          owner_agent_id=r["owner_agent_id"], current_version_id=r["current_version_id"],
                          status=r["status"], expires_at=_dt(r["expires_at"]), created_at=_dt(r["created_at"]))

    def set_item_scope(self, item_id: str, scope: str) -> None:
        self.conn.execute("UPDATE items SET scope = ? WHERE item_id = ?", (scope, item_id))

    def set_item_status(self, item_id: str, status: str) -> None:
        self.conn.execute("UPDATE items SET status = ? WHERE item_id = ?", (status, item_id))

    def add_version(self, v: Version, edges: list[Edge]) -> None:
        self.conn.execute(
            "INSERT INTO versions VALUES (?,?,?,?,?,?,?,?,?,?)",
            (v.version_id, v.item_id, v.content, v.content_hash, v.embedding_ref, int(v.label),
             v.trust_score, v.created_by, v.created_at.isoformat(), v.reason),
        )
        self.conn.executemany(
            "INSERT OR IGNORE INTO edges VALUES (?,?,?,?)",
            [(e.child_version_id, e.parent_kind, e.parent_id, e.relation) for e in edges],
        )
        cur = self.conn.execute("UPDATE items SET current_version_id = ? WHERE item_id = ?",
                                (v.version_id, v.item_id))
        if cur.rowcount != 1:
            raise KeyError(f"item không tồn tại: {v.item_id}")

    @staticmethod
    def _version(r: sqlite3.Row) -> Version:
        return Version(version_id=r["version_id"], item_id=r["item_id"], content=r["content"],
                       content_hash=r["content_hash"], embedding_ref=r["embedding_ref"], label=Label(r["label"]),
                       trust_score=r["trust_score"], created_by=r["created_by"],
                       created_at=_dt(r["created_at"]), reason=r["reason"])

    def get_version(self, version_id: str) -> Version | None:
        r = self.conn.execute("SELECT * FROM versions WHERE version_id = ?", (version_id,)).fetchone()
        return self._version(r) if r else None

    def list_versions(self, item_id: str) -> list[Version]:
        rows = self.conn.execute(
            "SELECT * FROM versions WHERE item_id = ? ORDER BY created_at, rowid", (item_id,))
        return [self._version(r) for r in rows]

    def active_versions(self, scopes: list[str], min_label: Label = Label.U,
                        agent: str | None = None, now: datetime | None = None) -> list[Version]:
        """now (khi bật expiration): bỏ mục đã quá expires_at dù worker chưa quét."""
        if not scopes:
            return []
        q = ",".join("?" * len(scopes))
        rows = self.conn.execute(
            f"""SELECT v.* FROM items i JOIN versions v ON v.version_id = i.current_version_id
                WHERE i.status = 'active' AND i.scope IN ({q}) AND v.label >= ?
                  AND (i.scope != 'private' OR i.owner_agent_id = ?)
                  AND (? IS NULL OR i.expires_at IS NULL OR i.expires_at > ?)
                ORDER BY v.created_at, v.rowid""",
            [*scopes, int(min_label), agent, _ts(now), _ts(now)])
        return [self._version(r) for r in rows]

    def expired_items(self, now: datetime) -> list[str]:
        return [r["item_id"] for r in self.conn.execute(
            "SELECT item_id FROM items WHERE status = 'active' AND expires_at IS NOT NULL AND expires_at <= ?"
            " ORDER BY expires_at, item_id", (_ts(now),))]

    def nearest(self, model: str, query: np.ndarray, scopes: list[str], min_label: Label = Label.U,
                agent: str | None = None, k: int = 5, now: datetime | None = None) -> list[Version]:
        """Top-k theo cosine bằng numpy; phá hoà theo thứ tự chèn, giống ORDER BY của PostgreSQL."""
        if k <= 0:
            return []
        cands = self.active_versions(scopes, min_label, agent=agent, now=now)
        have = self.get_embeddings(model, [v.content_hash for v in cands])
        cands = [v for v in cands if v.content_hash in have]
        if not cands:
            return []
        vecs = np.stack([np.frombuffer(have[v.content_hash], dtype=np.float32) for v in cands])
        order = np.argsort(-(vecs @ np.asarray(query, dtype=np.float32)), kind="stable")[:k]
        return [cands[i] for i in order]

    def get_embeddings(self, model: str, hashes: list[str]) -> dict[str, bytes]:
        if not hashes:
            return {}
        uniq = list(set(hashes))
        q = ",".join("?" * len(uniq))
        return {r["content_hash"]: r["vector"] for r in self.conn.execute(
            f"SELECT content_hash, vector FROM embeddings WHERE model = ? AND content_hash IN ({q})",
            [model, *uniq])}

    def put_embeddings(self, model: str, vectors: dict[str, bytes]) -> None:
        self.conn.executemany("INSERT OR IGNORE INTO embeddings VALUES (?,?,?)",
                              [(model, h, v) for h, v in vectors.items()])

    def parents(self, version_id: str) -> list[Edge]:
        rows = self.conn.execute("SELECT * FROM edges WHERE child_version_id = ?", (version_id,))
        return [Edge(child_version_id=r["child_version_id"], parent_kind=r["parent_kind"],
                     parent_id=r["parent_id"], relation=r["relation"]) for r in rows]

    def roots(self, version_id: str) -> list[Source]:
        # UNION (không ALL) để không duyệt một nút hai lần.
        rows = self.conn.execute(
            """WITH RECURSIVE anc(kind, id) AS (
                   SELECT parent_kind, parent_id FROM edges WHERE child_version_id = ?
                   UNION
                   SELECT e.parent_kind, e.parent_id FROM edges e
                   JOIN anc a ON a.kind = 'version' AND e.child_version_id = a.id)
               SELECT s.* FROM sources s JOIN anc a ON a.kind = 'source' AND a.id = s.source_id
               ORDER BY s.source_id""",
            (version_id,))
        return [self._source(r) for r in rows]

    # ---------- policy (việc 6) ----------

    def save_policy(self, policy: dict) -> None:
        with self.transaction():
            for t in ("agents", "policies", "roles", "policy_meta"):
                self.conn.execute(f"DELETE FROM {t}")
            self.conn.executemany("INSERT INTO roles VALUES (?,?)", [(r, r) for r in policy["roles"]])
            self.conn.executemany("INSERT INTO agents VALUES (?,?,?,NULL)",
                                  [(a, r, a) for a, r in policy["agents"].items()])
            self.conn.executemany(
                "INSERT INTO policies VALUES (?,?,?,?,?,?,?)",
                [(p["policy_id"], i, p["role_id"], p["scope"], p["action"], p["effect"], p.get("condition"))
                 for i, p in enumerate(policy["policies"])])
            self.conn.execute("INSERT INTO policy_meta VALUES ('default_role', ?)", (policy.get("default_role"),))

    def load_policy(self) -> dict | None:
        meta = self.conn.execute("SELECT value FROM policy_meta WHERE key = 'default_role'").fetchone()
        if meta is None:
            return None
        policies = []
        for r in self.conn.execute("SELECT * FROM policies ORDER BY ord"):
            p = {k: r[k] for k in ("policy_id", "role_id", "scope", "action", "effect")}
            if r["condition"] is not None:
                p["condition"] = r["condition"]
            policies.append(p)
        return {
            "roles": [r["role_id"] for r in self.conn.execute("SELECT role_id FROM roles ORDER BY rowid")],
            "default_role": meta["value"],
            "agents": {r["agent_id"]: r["role_id"] for r in self.conn.execute("SELECT * FROM agents ORDER BY rowid")},
            "policies": policies,
        }

    # ---------- audit ----------

    def append_audit(self, event: AuditEvent) -> AuditEvent:
        """Nối hash chain. BEGIN IMMEDIATE giữ khoá ghi của cả DB từ lúc đọc hash cuối tới COMMIT."""
        with self.transaction():
            last = self.conn.execute("SELECT hash FROM audit_events ORDER BY seq DESC LIMIT 1").fetchone()
            prev = last["hash"] if last else audit.GENESIS
            cur = self.conn.execute(
                "INSERT INTO audit_events (ts, actor, op, target, decision, reason) VALUES (?,?,?,?,?,?)",
                (event.ts.isoformat(), event.actor, event.op, event.target, event.decision, event.reason))
            ev = event.model_copy(update={"seq": cur.lastrowid, "prev_hash": prev})
            ev.hash = audit.event_hash(prev, ev)
            self.conn.execute("UPDATE audit_events SET prev_hash = ?, hash = ? WHERE seq = ?",
                              (prev, ev.hash, ev.seq))
        return ev

    def rehash_audit(self) -> int:
        """Nối chuỗi cho DB cũ (trước việc 7). Chỉ chạy khi chưa dòng nào có hash; chuỗi chỉ chứng minh
        log không bị sửa KỂ TỪ lúc này."""
        with self.transaction():
            if self.conn.execute("SELECT 1 FROM audit_events WHERE hash IS NOT NULL LIMIT 1").fetchone():
                raise RuntimeError("audit đã có hash chain; rehash sẽ che dấu vết sửa log")
            rows = audit.chain(self.list_audit())
            self.conn.executemany("UPDATE audit_events SET prev_hash = ?, hash = ? WHERE seq = ?",
                                  [(p, h, s) for s, p, h in rows])
        return len(rows)

    # ---------- jobs (worker inline, việc 7; chạy nền ở việc 9) ----------

    def add_job(self, job_id: str, kind: str, ref_id: str, created_at: datetime) -> None:
        self.conn.execute("INSERT INTO jobs VALUES (?,?,?,'queued',0,?)",
                          (job_id, kind, ref_id, created_at.isoformat()))

    def queued_jobs(self, limit: int = 100) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM jobs WHERE status = 'queued' ORDER BY created_at, rowid LIMIT ?", (limit,))]

    def set_job_status(self, job_id: str, status: str) -> None:
        self.conn.execute("UPDATE jobs SET status = ?, attempts = attempts + 1 WHERE job_id = ?",
                          (status, job_id))

    def list_audit(self, actor: str | None = None, decision: str | None = None) -> list[AuditEvent]:
        q, args = "SELECT * FROM audit_events WHERE 1=1", []
        if actor is not None:
            q, args = q + " AND actor = ?", [*args, actor]
        if decision is not None:
            q, args = q + " AND decision = ?", [*args, decision]
        return [AuditEvent(seq=r["seq"], ts=_dt(r["ts"]), actor=r["actor"], op=r["op"], target=r["target"],
                           decision=r["decision"], reason=r["reason"], prev_hash=r["prev_hash"], hash=r["hash"])
                for r in self.conn.execute(q + " ORDER BY seq", args)]

    # ---------- context ----------

    def append_context_read(self, agent: str, session: str, kind: ReadKind, ref_id: str, label: Label) -> None:
        with self.transaction():
            seq = self.conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM context_reads WHERE agent = ? AND session = ?",
                (agent, session)).fetchone()[0]
            self.conn.execute("INSERT INTO context_reads VALUES (?,?,?,?,?,?)",
                              (agent, session, seq, kind, ref_id, int(label)))

    def load_context(self, agent: str, session: str) -> AgentContext:
        rows = self.conn.execute(
            "SELECT kind, ref_id, label FROM context_reads WHERE agent = ? AND session = ? ORDER BY seq",
            (agent, session)).fetchall()
        ctx = AgentContext(agent=agent, session=session)
        if rows:
            ctx.label = Label(min(r["label"] for r in rows))
            ctx.sources = self.get_sources([r["ref_id"] for r in rows if r["kind"] == "source"])
            ctx.version_ids = [r["ref_id"] for r in rows if r["kind"] == "version"]
        return ctx
