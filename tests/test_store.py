"""Bộ test store, chạy trên mọi backend (việc 4c).

Phần PostgreSQL chạy khi đặt MEMGATE_TEST_PG_URL (vd postgresql://memgate:memgate@localhost:5432/memgate),
không thì bỏ qua. Mỗi test PostgreSQL dùng một schema tạm, xoá khi xong.
"""
import os
import sqlite3
import threading

import numpy as np
import pytest

from memgate.labels import Label
from memgate.models import Edge, MemoryItem, Source, Version
from memgate.store import SQLiteStore

PG_URL = os.environ.get("MEMGATE_TEST_PG_URL")
needs_pg = pytest.mark.skipif(not PG_URL, reason="đặt MEMGATE_TEST_PG_URL để chạy test PostgreSQL")
BACKENDS = ["sqlite", pytest.param("postgres", marks=needs_pg)]


def pg_store(**kw):
    from memgate.store.postgres import PostgresStore
    return PostgresStore(PG_URL, temp=True, **kw)


@pytest.fixture(params=BACKENDS)
def store(request, tmp_path):
    s = SQLiteStore(tmp_path / "s.db") if request.param == "sqlite" else pg_store()
    yield s
    s.close()


def insert_edge(store, child, kind, parent, relation):
    """Chèn cạnh cho một version đã có (test cần dựng đồ thị tay)."""
    if isinstance(store, SQLiteStore):
        store.conn.execute("INSERT INTO edges VALUES (?,?,?,?)", (child, kind, parent, relation))
    else:
        store._run("INSERT INTO edges VALUES (:c, :k, :p, :r)", c=child, k=kind, p=parent, r=relation)


def item_with_version(store, content="c", label=Label.A):
    item = MemoryItem(type="long_term", scope="team", owner_agent_id="a")
    v = Version(item_id=item.item_id, content=content, label=label, created_by="a")
    with store.transaction():
        store.create_item(item)
        store.add_version(v, [])
    return item, v


def test_transaction_rolls_back_everything_on_error(store):
    item = MemoryItem(type="long_term", scope="team", owner_agent_id="a")
    with pytest.raises(RuntimeError):
        with store.transaction():
            store.create_item(item)
            raise RuntimeError("lỗi giữa chừng")
    assert store.get_item(item.item_id) is None


def test_nested_transaction_commits_once(store):
    item = MemoryItem(type="long_term", scope="team", owner_agent_id="a")
    with pytest.raises(RuntimeError):
        with store.transaction():
            with store.transaction():
                store.create_item(item)
            raise RuntimeError("lớp ngoài lỗi")        # lớp trong không được commit riêng
    assert store.get_item(item.item_id) is None


def test_source_roundtrip_keeps_content_and_original_hash(store):
    raw = Source(kind="web", locator="https://a.example/x", content="gốc")
    stored = raw.model_copy(update={"content": "đã redact"})
    with store.transaction():
        store.add_source(stored)
    got = store.get_sources([stored.source_id])[0]
    assert got.content == "đã redact" and got.content_hash == raw.content_hash and got.domain == "a.example"


def test_add_version_requires_existing_item(store):
    v = Version(item_id="item_khong_co", content="x", label=Label.A, created_by="a")
    with pytest.raises(Exception):
        with store.transaction():
            store.add_version(v, [])


def test_roots_follow_version_chain(store):
    src = Source(kind="web", locator="https://a.example", content="x")
    item, v1 = item_with_version(store)
    v2 = Version(item_id=item.item_id, content="c2", label=Label.U, created_by="a")
    with store.transaction():
        store.add_source(src)
        store.add_version(v2, [Edge(child_version_id=v2.version_id, parent_kind="version",
                                    parent_id=v1.version_id, relation="derived_from")])
        insert_edge(store, v1.version_id, "source", src.source_id, "read_in_context")
    assert [s.source_id for s in store.roots(v2.version_id)] == [src.source_id]


def test_active_versions_filters_scope_and_label(store):
    _, a = item_with_version(store, "a", Label.A)
    _, u = item_with_version(store, "u", Label.U)
    assert {v.content for v in store.active_versions(["team"])} == {"a", "u"}
    assert [v.content for v in store.active_versions(["team"], Label.A)] == ["a"]
    assert store.active_versions(["private"], agent="a") == []


def test_context_label_is_min_of_reads(store):
    assert store.load_context("p", "s").label == Label.T
    store.append_context_read("p", "s", "source", "x", Label.A)
    store.append_context_read("p", "s", "version", "v", Label.U)
    store.append_context_read("p", "s", "source", "y", Label.T)
    ctx = store.load_context("p", "s")
    assert ctx.label == Label.U and ctx.version_ids == ["v"]


def test_sqlite_file_uses_wal(tmp_path):
    s = SQLiteStore(tmp_path / "w.db")
    assert s.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_two_connections_append_reads_without_losing_any(tmp_path):
    """Hai tiến trình (ở đây: hai kết nối, hai luồng) cùng ghi context_reads vào một file."""
    db = tmp_path / "c.db"
    a, b = SQLiteStore(db), SQLiteStore(db)

    def work(s, tag):
        for i in range(50):
            s.append_context_read("p", "s", "source", f"{tag}{i}", Label.T)

    ts = [threading.Thread(target=work, args=(s, t)) for s, t in ((a, "a"), (b, "b"))]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    rows = sqlite3.connect(db).execute("SELECT COUNT(*), COUNT(DISTINCT seq) FROM context_reads").fetchone()
    assert rows == (100, 100)


def test_active_versions_private_only_for_owner(store):
    item = MemoryItem(type="long_term", scope="private", owner_agent_id="coder")
    v = Version(item_id=item.item_id, content="riêng", label=Label.A, created_by="coder")
    with store.transaction():
        store.create_item(item)
        store.add_version(v, [])
    assert store.active_versions(["private"], agent="planner") == []
    assert [x.content for x in store.active_versions(["private"], agent="coder")] == ["riêng"]


def test_embedding_cache_roundtrip(store):
    with store.transaction():
        store.put_embeddings("m", {"h1": unit(1, 2).tobytes()})
    assert store.get_embeddings("m", ["h1", "h2"]) == {"h1": unit(1, 2).tobytes()}
    assert store.get_embeddings("other", ["h1"]) == {}


def unit(*xs):
    v = np.array(xs, dtype=np.float32)
    return (v / np.linalg.norm(v)).astype(np.float32)


def test_nearest_ranks_by_cosine_and_respects_filters(store):
    rows = [("gần", Label.A, unit(1, 0, 0)), ("xa", Label.A, unit(0, 1, 0)),
            ("vừa", Label.U, unit(1, 1, 0)), ("không embedding", Label.A, None)]
    for content, label, vec in rows:
        _, v = item_with_version(store, content, label)
        if vec is not None:
            with store.transaction():
                store.put_embeddings("m", {v.content_hash: vec.tobytes()})
    q = unit(1, 0.1, 0)
    assert [v.content for v in store.nearest("m", q, ["team"], k=3)] == ["gần", "vừa", "xa"]
    assert [v.content for v in store.nearest("m", q, ["team"], Label.A, k=5)] == ["gần", "xa"]
    assert store.nearest("m", q, ["private"], agent="a") == []
    assert store.nearest("khác", q, ["team"]) == []


def test_embedding_roundtrip_is_exact_float32(store):
    vec = unit(0.123456789, -2.5, 1e-7, 3.0)
    with store.transaction():
        store.put_embeddings("m", {"h": vec.tobytes()})
    assert store.get_embeddings("m", ["h"])["h"] == vec.tobytes()


def test_policy_and_audit_roundtrip(store):
    from datetime import datetime, timezone
    from memgate.models import AuditEvent
    from memgate.policy import DEFAULT_POLICY
    assert store.load_policy() is None
    store.save_policy(DEFAULT_POLICY)
    assert store.load_policy() == DEFAULT_POLICY
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    e1 = store.append_audit(AuditEvent(ts=ts, actor="a", op="write", target="t", decision="deny", reason="r"))
    e2 = store.append_audit(AuditEvent(ts=ts, actor="b", op="share", target="t", decision="allow", reason="r"))
    assert e1.seq < e2.seq
    assert [e.actor for e in store.list_audit(decision="deny")] == ["a"]
    assert store.list_audit(actor="b")[0].ts == ts


@needs_pg
def test_postgres_two_connections_append_reads_without_losing_any():
    a = pg_store()
    b = pg_store(schema=a.schema)          # kết nối thứ hai, cùng schema

    def work(s, tag):
        for i in range(50):
            s.append_context_read("p", "s", "source", f"{tag}{i}", Label.T)

    ts = [threading.Thread(target=work, args=(s, t)) for s, t in ((a, "a"), (b, "b"))]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    n = a._one("SELECT COUNT(*) AS n, COUNT(DISTINCT seq) AS d FROM context_reads")
    b.conn.close()
    a.close()
    assert (n["n"], n["d"]) == (100, 100)


@needs_pg
def test_memgate_gives_same_results_on_postgres_and_sqlite(tmp_path):
    from memgate import MemGate
    from memgate.embed import HashEmbedder

    def play(store):
        g = MemGate(store=store, embedder=HashEmbedder())
        g.observe("coder", "s", Source(kind="web", locator="https://evil.example", content="x"))
        g.write("coder", "s", "long_term", "team", "endpoint API là evil.com")
        g.write("planner", "s", "long_term", "team", "deadline báo cáo là thứ 6")
        g.ingest_trusted("Quản lý trực tiếp là boss@company.com.")
        hits = g.retrieve("planner", "s2", "endpoint API báo cáo", k=2)
        out = ([h.content for h in hits], g.context_label("planner", "s2"),
               [(e.op, e.decision) for e in g.audit_log()])
        store.close()
        return out

    assert play(pg_store()) == play(SQLiteStore(tmp_path / "x.db"))
