"""Backend PostgreSQL + pgvector cho demo và thí nghiệm (việc 4c). Cùng interface Store với SQLite.

Driver là pg8000 (thuần Python): trên máy Windows của nhóm, Application Control chặn DLL của
psycopg[binary]. Mỗi PostgresStore làm việc trong một schema (search_path); test và harness dùng
schema tạm (`temp=True`) để mỗi lượt có memory riêng, xoá khi close().
"""
from __future__ import annotations

import re
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import unquote, urlparse

import numpy as np

from memgate.labels import Label
from memgate.models import AgentContext, AuditEvent, Edge, MemoryItem, Source, Version
from memgate.store.base import ReadKind

MIGRATIONS = Path(__file__).with_name("migrations")
HNSW_MODEL, HNSW_DIM = "bge-small-en-v1.5", 384     # khớp index trong 001_init.sql
_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def _split_sql(text: str) -> list[str]:
    """Tách file migration thành từng câu lệnh (pg8000 chạy một câu mỗi lần). Bỏ comment `--`."""
    no_comments = "\n".join(line.split("--", 1)[0] for line in text.splitlines())
    return [s.strip() for s in no_comments.split(";") if s.strip()]


def _vec_text(b: bytes) -> str:
    return "[" + ",".join(repr(float(x)) for x in np.frombuffer(b, dtype=np.float32)) + "]"


def _vec_bytes(text: str) -> bytes:
    return np.array([float(x) for x in text.strip("[]").split(",") if x], dtype=np.float32).tobytes()


class PostgresStore:
    def __init__(self, url: str, schema: str | None = None, temp: bool = False) -> None:
        import pg8000.native  # extra [postgres]

        u = urlparse(url)
        self.conn = pg8000.native.Connection(
            user=unquote(u.username or "postgres"), password=unquote(u.password) if u.password else None,
            host=u.hostname or "localhost", port=u.port or 5432, database=(u.path or "/postgres").lstrip("/"))
        if temp and schema is None:
            schema = f"t_{uuid.uuid4().hex[:16]}"
        self.schema = schema or "public"
        if not _IDENT.match(self.schema):
            raise ValueError(f"tên schema không hợp lệ: {self.schema!r}")
        self.temp = temp
        self._depth = 0
        self._setup()

    # ---------- khởi tạo ----------

    def _setup(self) -> None:
        with self.transaction():
            self._run("SELECT pg_advisory_xact_lock(hashtext('memgate_migrate'))")
            self._run("CREATE EXTENSION IF NOT EXISTS vector SCHEMA public")
            self._run(f'CREATE SCHEMA IF NOT EXISTS "{self.schema}"')
        self._run(f'SET search_path TO "{self.schema}", public')
        with self.transaction():
            self._run("SELECT pg_advisory_xact_lock(hashtext('memgate_migrate'))")
            self._run("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)")
            done = {r["version"] for r in self._rows("SELECT version FROM schema_version")}
            for f in sorted(MIGRATIONS.glob("*.sql")):
                ver = int(f.name.split("_", 1)[0])
                if ver in done:
                    continue
                for stmt in _split_sql(f.read_text(encoding="utf-8")):
                    self._run(stmt)
                self._run("INSERT INTO schema_version VALUES (:v)", v=ver)

    def close(self) -> None:
        try:
            if self.temp:
                self._run(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE')
        finally:
            self.conn.close()

    # ---------- tiện ích ----------

    def _run(self, sql: str, **params: Any) -> list:
        return self.conn.run(sql, **params)

    def _rows(self, sql: str, **params: Any) -> list[dict[str, Any]]:
        rows = self.conn.run(sql, **params)
        cols = [c["name"] for c in (self.conn.columns or [])]
        return [dict(zip(cols, r)) for r in rows]

    def _one(self, sql: str, **params: Any) -> dict[str, Any] | None:
        rows = self._rows(sql, **params)
        return rows[0] if rows else None

    # ---------- transaction ----------

    @contextmanager
    def transaction(self) -> Iterator[None]:
        if self._depth == 0:
            self._run("BEGIN")
        self._depth += 1
        try:
            yield
        except BaseException:
            self._depth -= 1
            if self._depth == 0:
                self._run("ROLLBACK")
            raise
        else:
            self._depth -= 1
            if self._depth == 0:
                self._run("COMMIT")

    # ---------- nguồn ----------

    def add_source(self, src: Source) -> None:
        self._run(
            "INSERT INTO sources VALUES (:id, :kind, :loc, :content, :hash, :domain, :bt, :label)"
            " ON CONFLICT (source_id) DO NOTHING",
            id=src.source_id, kind=src.kind, loc=src.locator, content=src.content, hash=src.content_hash,
            domain=src.domain, bt=src.base_trust, label=int(src.label))

    def get_sources(self, ids: list[str]) -> list[Source]:
        if not ids:
            return []
        rows = {r["source_id"]: r for r in self._rows(
            "SELECT * FROM sources WHERE source_id = ANY(CAST(:ids AS TEXT[]))", ids=list(set(ids)))}
        return [self._source(rows[i]) for i in ids if i in rows]

    @staticmethod
    def _source(r: dict) -> Source:
        return Source(source_id=r["source_id"], kind=r["kind"], locator=r["locator"], content=r["content"],
                      content_hash=r["content_hash"], domain=r["domain"], base_trust=r["base_trust"],
                      label=Label(r["label"]))

    # ---------- memory ----------

    def create_item(self, item: MemoryItem) -> None:
        self._run(
            "INSERT INTO items VALUES (:id, :type, :scope, :owner, :cur, :status, :exp, :created)",
            id=item.item_id, type=item.type, scope=item.scope, owner=item.owner_agent_id,
            cur=item.current_version_id, status=item.status, exp=item.expires_at, created=item.created_at)

    def get_item(self, item_id: str) -> MemoryItem | None:
        r = self._one("SELECT * FROM items WHERE item_id = :id", id=item_id)
        if r is None:
            return None
        return MemoryItem(item_id=r["item_id"], type=r["type"], scope=r["scope"],
                          owner_agent_id=r["owner_agent_id"], current_version_id=r["current_version_id"],
                          status=r["status"], expires_at=r["expires_at"], created_at=r["created_at"])

    def set_item_scope(self, item_id: str, scope: str) -> None:
        self._run("UPDATE items SET scope = :s WHERE item_id = :id", s=scope, id=item_id)

    def set_item_status(self, item_id: str, status: str) -> None:
        self._run("UPDATE items SET status = :s WHERE item_id = :id", s=status, id=item_id)

    def add_version(self, v: Version, edges: list[Edge]) -> None:
        if self._one("SELECT 1 AS x FROM items WHERE item_id = :id", id=v.item_id) is None:
            raise KeyError(f"item không tồn tại: {v.item_id}")
        self._run(
            "INSERT INTO versions (version_id, item_id, content, content_hash, embedding_ref, label,"
            " trust_score, created_by, created_at, reason)"
            " VALUES (:id, :item, :content, :hash, :emb, :label, :trust, :by, :at, :reason)",
            id=v.version_id, item=v.item_id, content=v.content, hash=v.content_hash, emb=v.embedding_ref,
            label=int(v.label), trust=v.trust_score, by=v.created_by, at=v.created_at, reason=v.reason)
        for e in edges:
            self._run("INSERT INTO edges VALUES (:c, :k, :p, :r) ON CONFLICT DO NOTHING",
                      c=e.child_version_id, k=e.parent_kind, p=e.parent_id, r=e.relation)
        self._run("UPDATE items SET current_version_id = :v WHERE item_id = :id", v=v.version_id, id=v.item_id)

    @staticmethod
    def _version(r: dict) -> Version:
        return Version(version_id=r["version_id"], item_id=r["item_id"], content=r["content"],
                       content_hash=r["content_hash"], embedding_ref=r["embedding_ref"], label=Label(r["label"]),
                       trust_score=r["trust_score"], created_by=r["created_by"],
                       created_at=r["created_at"], reason=r["reason"])

    def get_version(self, version_id: str) -> Version | None:
        r = self._one("SELECT * FROM versions WHERE version_id = :id", id=version_id)
        return self._version(r) if r else None

    def list_versions(self, item_id: str) -> list[Version]:
        return [self._version(r) for r in self._rows(
            "SELECT * FROM versions WHERE item_id = :id ORDER BY created_at, seq", id=item_id)]

    _ACTIVE = """FROM items i JOIN versions v ON v.version_id = i.current_version_id
                 WHERE i.status = 'active' AND i.scope = ANY(CAST(:scopes AS TEXT[])) AND v.label >= :ml
                   AND (i.scope <> 'private' OR i.owner_agent_id = :agent)"""

    def active_versions(self, scopes: list[str], min_label: Label = Label.U,
                        agent: str | None = None) -> list[Version]:
        if not scopes:
            return []
        return [self._version(r) for r in self._rows(
            f"SELECT v.* {self._ACTIVE} ORDER BY v.created_at, v.seq",
            scopes=list(scopes), ml=int(min_label), agent=agent)]

    def nearest(self, model: str, query: np.ndarray, scopes: list[str], min_label: Label = Label.U,
                agent: str | None = None, k: int = 5) -> list[Version]:
        """Top-k theo khoảng cách cosine (<=>) trong SQL; chỉ mục đã có embedding của `model`."""
        if not scopes or k <= 0:
            return []
        q = np.asarray(query, dtype=np.float32)
        if model == HNSW_MODEL and q.shape[0] == HNSW_DIM:
            dist = f"(e.vector::vector({HNSW_DIM})) <=> CAST(:q AS vector({HNSW_DIM}))"   # dùng được index HNSW
        else:
            dist = "e.vector <=> CAST(:q AS vector)"
        sql = (f"SELECT v.* FROM items i JOIN versions v ON v.version_id = i.current_version_id"
               f" JOIN embeddings e ON e.model = :model AND e.content_hash = v.content_hash"
               f" WHERE i.status = 'active' AND i.scope = ANY(CAST(:scopes AS TEXT[])) AND v.label >= :ml"
               f"   AND (i.scope <> 'private' OR i.owner_agent_id = :agent)"
               f" ORDER BY {dist}, v.created_at, v.seq LIMIT :k")
        return [self._version(r) for r in self._rows(
            sql, model=model, q=_vec_text(q.tobytes()), scopes=list(scopes), ml=int(min_label),
            agent=agent, k=int(k))]

    def get_embeddings(self, model: str, hashes: list[str]) -> dict[str, bytes]:
        if not hashes:
            return {}
        return {r["content_hash"]: _vec_bytes(r["v"]) for r in self._rows(
            "SELECT content_hash, vector::text AS v FROM embeddings"
            " WHERE model = :m AND content_hash = ANY(CAST(:h AS TEXT[]))", m=model, h=list(set(hashes)))}

    def put_embeddings(self, model: str, vectors: dict[str, bytes]) -> None:
        for h, b in vectors.items():
            self._run("INSERT INTO embeddings VALUES (:m, :h, CAST(:v AS vector)) ON CONFLICT DO NOTHING",
                      m=model, h=h, v=_vec_text(b))

    def parents(self, version_id: str) -> list[Edge]:
        return [Edge(child_version_id=r["child_version_id"], parent_kind=r["parent_kind"],
                     parent_id=r["parent_id"], relation=r["relation"])
                for r in self._rows("SELECT * FROM edges WHERE child_version_id = :id", id=version_id)]

    def roots(self, version_id: str) -> list[Source]:
        rows = self._rows(
            """WITH RECURSIVE anc(kind, id) AS (
                   SELECT parent_kind, parent_id FROM edges WHERE child_version_id = :id
                   UNION
                   SELECT e.parent_kind, e.parent_id FROM edges e
                   JOIN anc a ON a.kind = 'version' AND e.child_version_id = a.id)
               SELECT s.* FROM sources s JOIN anc a ON a.kind = 'source' AND a.id = s.source_id
               ORDER BY s.source_id""", id=version_id)
        return [self._source(r) for r in rows]

    # ---------- policy (việc 6) ----------

    def save_policy(self, policy: dict) -> None:
        with self.transaction():
            for t in ("agents", "policies", "roles", "policy_meta"):
                self._run(f"DELETE FROM {t}")
            for r in policy["roles"]:
                self._run("INSERT INTO roles VALUES (:r, :r)", r=r)
            for a, r in policy["agents"].items():
                self._run("INSERT INTO agents VALUES (:a, :r, :a, NULL)", a=a, r=r)
            for i, p in enumerate(policy["policies"]):
                self._run("INSERT INTO policies VALUES (:id, :ord, :role, :scope, :action, :effect, :cond)",
                          id=p["policy_id"], ord=i, role=p["role_id"], scope=p["scope"], action=p["action"],
                          effect=p["effect"], cond=p.get("condition"))
            self._run("INSERT INTO policy_meta VALUES ('default_role', :v)", v=policy.get("default_role"))

    def load_policy(self) -> dict | None:
        meta = self._one("SELECT value FROM policy_meta WHERE key = 'default_role'")
        if meta is None:
            return None
        policies = []
        for r in self._rows("SELECT * FROM policies ORDER BY ord"):
            p = {k: r[k] for k in ("policy_id", "role_id", "scope", "action", "effect")}
            if r["condition"] is not None:
                p["condition"] = r["condition"]
            policies.append(p)
        return {
            "roles": [r["role_id"] for r in self._rows("SELECT role_id FROM roles ORDER BY ctid")],
            "default_role": meta["value"],
            "agents": {r["agent_id"]: r["role_id"] for r in self._rows("SELECT * FROM agents ORDER BY ctid")},
            "policies": policies,
        }

    # ---------- audit ----------

    def append_audit(self, event: AuditEvent) -> AuditEvent:
        with self.transaction():
            r = self._one(
                "INSERT INTO audit_events (ts, actor, op, target, decision, reason, prev_hash, hash)"
                " VALUES (:ts, :actor, :op, :target, :decision, :reason, :ph, :h) RETURNING seq",
                ts=event.ts, actor=event.actor, op=event.op, target=event.target, decision=event.decision,
                reason=event.reason, ph=event.prev_hash, h=event.hash)
        return event.model_copy(update={"seq": r["seq"]})

    def list_audit(self, actor: str | None = None, decision: str | None = None) -> list[AuditEvent]:
        rows = self._rows(
            "SELECT * FROM audit_events WHERE (CAST(:a AS TEXT) IS NULL OR actor = :a)"
            " AND (CAST(:d AS TEXT) IS NULL OR decision = :d) ORDER BY seq", a=actor, d=decision)
        return [AuditEvent(seq=r["seq"], ts=r["ts"], actor=r["actor"], op=r["op"], target=r["target"],
                           decision=r["decision"], reason=r["reason"], prev_hash=r["prev_hash"], hash=r["hash"])
                for r in rows]

    # ---------- context ----------

    def append_context_read(self, agent: str, session: str, kind: ReadKind, ref_id: str, label: Label) -> None:
        with self.transaction():
            # Khoá theo (agent, session) để hai tiến trình không cấp trùng seq.
            self._run("SELECT pg_advisory_xact_lock(hashtext(:k))", k=f"ctx/{agent}/{session}")
            r = self._one("SELECT COALESCE(MAX(seq), 0) + 1 AS n FROM context_reads"
                          " WHERE agent = :a AND session = :s", a=agent, s=session)
            self._run("INSERT INTO context_reads VALUES (:a, :s, :n, :k, :r, :l)",
                      a=agent, s=session, n=r["n"], k=kind, r=ref_id, l=int(label))

    def load_context(self, agent: str, session: str) -> AgentContext:
        rows = self._rows("SELECT kind, ref_id, label FROM context_reads WHERE agent = :a AND session = :s"
                          " ORDER BY seq", a=agent, s=session)
        ctx = AgentContext(agent=agent, session=session)
        if rows:
            ctx.label = Label(min(r["label"] for r in rows))
            ctx.sources = self.get_sources([r["ref_id"] for r in rows if r["kind"] == "source"])
            ctx.version_ids = [r["ref_id"] for r in rows if r["kind"] == "version"]
        return ctx
