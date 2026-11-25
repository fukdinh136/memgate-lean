"""Việc 7: audit hash chain, vòng đời 7 trạng thái + worker inline, retention, redact, công tắc biện pháp."""
import hashlib
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from memgate import MemGate, Source
from memgate import audit
from memgate.clock import FakeClock
from memgate.embed import HashEmbedder
from memgate.labels import Label
from memgate.lifecycle import STATUSES, TERMINAL, TRANSITIONS, InvalidTransition, can_transition
from memgate.mitigations import Mitigations, parse_overrides
from memgate.models import AuditEvent
from memgate.policy import PolicyDenied
from memgate.redact import redact
from memgate.store import SQLiteStore

ROOT = Path(__file__).parents[1]
PG_URL = os.environ.get("MEMGATE_TEST_PG_URL")
needs_pg = pytest.mark.skipif(not PG_URL, reason="đặt MEMGATE_TEST_PG_URL để chạy test PostgreSQL")
BACKENDS = ["sqlite", pytest.param("postgres", marks=needs_pg)]
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def pg_store(**kw):
    from memgate.store.postgres import PostgresStore
    return PostgresStore(PG_URL, temp=True, **kw)


@pytest.fixture(params=BACKENDS)
def store(request, tmp_path):
    s = SQLiteStore(tmp_path / "s.db") if request.param == "sqlite" else pg_store()
    yield s
    s.close()


def gate(store=None, clock=None, **config):
    return MemGate(store=store or SQLiteStore(":memory:"), clock=clock or FakeClock(T0),
                   embedder=HashEmbedder(), config=config or None)


def ev(i, actor="a"):
    return AuditEvent(ts=T0, actor=actor, op="op", target=f"t{i}", decision="allow", reason=f"r{i}")


# ---------- audit hash chain ----------

def test_chain_links_every_event(store):
    out = [store.append_audit(ev(i)) for i in range(5)]
    assert out[0].prev_hash == audit.GENESIS
    assert all(b.prev_hash == a.hash for a, b in zip(out, out[1:]))
    res = audit.verify(store.list_audit())
    assert res.ok and res.count == 5


@pytest.mark.parametrize("tamper, why", [
    ("UPDATE audit_events SET reason = 'sửa' WHERE seq = :s", "bị sửa"),
    ("UPDATE audit_events SET actor = 'khac' WHERE seq = :s", "bị sửa"),
    ("DELETE FROM audit_events WHERE seq = :s", "bị xoá"),
    ("UPDATE audit_events SET hash = NULL WHERE seq = :s", "chưa có hash"),
])
def test_verify_detects_tampering(store, tamper, why):
    seqs = [store.append_audit(ev(i)).seq for i in range(5)]
    if isinstance(store, SQLiteStore):
        store.conn.execute(tamper.replace(":s", "?"), (seqs[2],))
    else:
        store._run(tamper, s=seqs[2])
    res = audit.verify(store.list_audit())
    assert not res.ok and why in res.reason
    assert res.bad_seq == (seqs[3] if "DELETE" in tamper else seqs[2])


def test_verify_detects_reordering(store):
    seqs = [store.append_audit(ev(i)).seq for i in range(3)]
    if isinstance(store, SQLiteStore):            # đổi chỗ hai dòng: seq nằm trong hash
        store.conn.execute("UPDATE audit_events SET seq = -1 WHERE seq = ?", (seqs[0],))
        store.conn.execute("UPDATE audit_events SET seq = ? WHERE seq = ?", (seqs[0], seqs[1]))
        store.conn.execute("UPDATE audit_events SET seq = ? WHERE seq = -1", (seqs[1],))
    else:
        store._run("UPDATE audit_events SET seq = -1 WHERE seq = :a", a=seqs[0])
        store._run("UPDATE audit_events SET seq = :a WHERE seq = :b", a=seqs[0], b=seqs[1])
        store._run("UPDATE audit_events SET seq = :b WHERE seq = -1", b=seqs[1])
    assert not audit.verify(store.list_audit()).ok


def test_old_db_without_hash_is_rehashed_on_open(tmp_path):
    path = tmp_path / "old.db"
    s = SQLiteStore(path)
    for i in range(3):     # dòng ghi kiểu trước việc 7: không có hash
        s.conn.execute("INSERT INTO audit_events (ts, actor, op, target, decision, reason)"
                       " VALUES (?, 'a', 'op', ?, 'deny', 'r')", (T0.isoformat(), f"t{i}"))
    s.close()
    s = SQLiteStore(path)
    assert audit.verify(s.list_audit()).ok
    with pytest.raises(RuntimeError):
        s.rehash_audit()                          # đã có chuỗi: không cho tính lại (che dấu vết)
    s.close()


def test_cli_verify(tmp_path):
    path = tmp_path / "c.db"
    s = SQLiteStore(path)
    s.append_audit(ev(1))
    s.close()
    assert audit.main(["verify", "--store", f"sqlite:///{path}"]) == 0
    s = SQLiteStore(path)
    s.conn.execute("UPDATE audit_events SET reason = 'x'")
    s.close()
    assert audit.main(["verify", "--store", f"sqlite:///{path}"]) == 1


WRITER = r"""
import sys
from datetime import datetime, timezone
from memgate.models import AuditEvent
from memgate.store import open_store
url, schema, tag, n = sys.argv[1], sys.argv[2] or None, sys.argv[3], int(sys.argv[4])
s = open_store(url, schema=schema)
for i in range(n):
    s.append_audit(AuditEvent(ts=datetime.now(timezone.utc), actor=tag, op="stress", target=str(i),
                              decision="allow", reason=""))
s.close()
"""


def two_writers(url, schema="", n=200):
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    procs = [subprocess.Popen([sys.executable, "-c", WRITER, url, schema, tag, str(n)], cwd=ROOT, env=env)
             for tag in ("p1", "p2")]
    assert [p.wait(timeout=300) for p in procs] == [0, 0]


def test_two_processes_keep_sqlite_chain(tmp_path):
    path = tmp_path / "multi.db"
    SQLiteStore(path).close()                      # tạo schema trước khi hai tiến trình chạy
    two_writers(f"sqlite:///{path}")
    s = SQLiteStore(path)
    events = s.list_audit()
    s.close()
    assert len(events) == 400 and {e.actor for e in events} == {"p1", "p2"}
    assert audit.verify(events).ok


@needs_pg
def test_two_processes_keep_postgres_chain():
    s = pg_store(schema=f"t_{uuid.uuid4().hex[:16]}")
    try:
        two_writers(PG_URL, s.schema)
        events = s.list_audit()
        assert len(events) == 400 and audit.verify(events).ok
    finally:
        s.close()


# ---------- vòng đời ----------

def test_transition_table_is_closed_and_terminal_states_are_final():
    assert set(TRANSITIONS) == set(STATUSES)
    assert all(set(nxt) <= set(STATUSES) for nxt in TRANSITIONS.values())
    assert TERMINAL == {"expired", "superseded", "rejected"}
    for src in ("pending",):              # heuristic đầu độc (11b) và mâu thuẫn cùng nhãn đi thẳng từ pending
        assert can_transition(src, "quarantined") and can_transition(src, "under_review")
    assert can_transition("quarantined", "rejected")     # việc 8 dùng (quá 30 ngày)


def test_write_goes_pending_then_active_with_audit(store):
    g = gate(store)
    v = g.write("coder", "s", "long_term", "team", "deadline thứ 6")
    assert store.get_item(v.item_id).status == "active"
    t = [e for e in g.audit_log() if e.op == "transition"]
    assert [(e.actor, e.reason) for e in t] == [("worker", "pending->active: embedding xong")]
    assert store.queued_jobs() == [] and audit.verify(store.list_audit()).ok


def test_pending_item_is_not_retrievable():
    g = gate()
    with g.store.transaction():                       # ghi nhưng chưa chạy worker
        item = g._new_item("long_term", "team", "coder")
        from memgate.models import Version
        g.add_version(Version(item_id=item.item_id, content="mật mã cửa là 1234", label=Label.A,
                              created_by="coder"), [])
    assert g.retrieve("planner", "s", "mật mã cửa") == []
    g.run_worker()
    assert [v.content for v in g.retrieve("planner", "s2", "mật mã cửa")] == ["mật mã cửa là 1234"]


def test_illegal_transition_is_denied_and_audited():
    g = gate()
    v = g.write("coder", "s", "long_term", "team", "x")
    g.transition(v.item_id, "superseded", "test")
    with pytest.raises(InvalidTransition):
        g.transition(v.item_id, "active", "test")
    last = g.audit_log()[-1]
    assert (last.op, last.decision, last.reason) == ("transition", "deny", "superseded->active không hợp lệ")


def test_worker_failure_leaves_item_pending():
    class Broken(HashEmbedder):
        def encode(self, texts, is_query=False):
            raise RuntimeError("model hỏng")
    g = MemGate(store=SQLiteStore(":memory:"), clock=FakeClock(T0), embedder=Broken())
    with pytest.raises(RuntimeError):
        g.write("coder", "s", "long_term", "team", "x")
    st = g.store.conn.execute("SELECT i.status, j.status FROM items i, jobs j").fetchone()
    assert tuple(st) == ("pending", "failed")


@pytest.mark.parametrize("status", ["superseded", "quarantined"])
def test_share_and_update_require_active(status):
    g = gate()
    v = g.write("coder", "s", "long_term", "private", "x")
    g.transition(v.item_id, status, "test")
    with pytest.raises(PolicyDenied):
        g.share("coder", "s", v.item_id, "team")
    with pytest.raises(PolicyDenied):
        g.write("coder", "s", "long_term", "private", "y", item_id=v.item_id)
    assert g.audit_log()[-1].decision == "deny"


def test_rollback_keeps_status_and_never_revives_terminal_items():
    g = gate()
    v1 = g.write("coder", "s", "long_term", "team", "bản 1")
    g.write("coder", "s", "long_term", "team", "bản 2", item_id=v1.item_id)
    g.transition(v1.item_id, "quarantined", "test")
    g.rollback(v1.item_id, v1.version_id, "coder")
    assert g.store.get_item(v1.item_id).status == "quarantined"
    g.transition(v1.item_id, "rejected", "test")
    with pytest.raises(PolicyDenied):
        g.rollback(v1.item_id, v1.version_id, "coder")


def test_consolidate_outputs_pass_through_pending():
    g = gate()
    g.write("coder", "s", "long_term", "team", "a")
    g.write("coder", "s", "long_term", "team", "b")
    res = g.consolidate("team", "fail_closed", lambda xs: " / ".join(xs))
    out = g.store.get_item(res.outputs[0].item_id)
    assert out.status == "active"
    reasons = [e.reason for e in g.audit_log() if e.op == "transition"]
    assert reasons.count("active->superseded: hợp nhất fail_closed") == 2
    assert reasons.count("pending->active: embedding xong") == 3


# ---------- retention ----------

def test_expiration_off_by_default_sets_no_deadline():
    g = gate()
    v = g.write("coder", "s", "short_term", "team", "x")
    assert g.store.get_item(v.item_id).expires_at is None and g.sweep_expired() == []


def test_expired_item_hidden_before_sweep_then_marked_expired(store):
    clock = FakeClock(T0)
    g = gate(store, clock, mitigations={"expiration": True})
    short = g.write("coder", "s", "short_term", "team", "mật khẩu wifi tạm")
    keep = g.write("coder", "s", "long_term", "team", "mật khẩu wifi chính")
    assert g.store.get_item(keep.item_id).expires_at is None
    clock.advance(hours=23)
    assert len(g.retrieve("planner", "s1", "mật khẩu wifi")) == 2
    clock.advance(hours=2)                         # short_term: 1 ngày
    assert [v.content for v in g.retrieve("planner", "s2", "mật khẩu wifi")] == ["mật khẩu wifi chính"]
    assert g.store.get_item(short.item_id).status == "active"      # chưa quét
    assert g.sweep_expired() == [short.item_id]
    assert g.store.get_item(short.item_id).status == "expired"
    assert g.audit_log()[-1].reason == "active->expired: hết hạn retention"


# ---------- redact ----------

SECRET = "AKIAIOSFODNN7EXAMPLE"


@pytest.mark.parametrize("text, kept", [
    (f"aws_access_key_id = {SECRET}", "aws_access_key_id = "),
    ('password = "hunter2xyz"', "password = "),
    ("token: ghp_abcdefghijklmnopqrstuvwxyz0123456789 ok", " ok"),
    ("-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n-----END RSA PRIVATE KEY-----\nsau", "sau"),
    ("db_password: Tr0ub4dor&3", "db_password: "),
    ("postgresql://memgate:s3cretpw@127.0.0.1:5432/memgate", "postgresql://memgate:"),
])
def test_redact_known_secrets(text, kept):
    out = redact(text)
    assert "[REDACTED:" in out and kept in out
    for s in (SECRET, "hunter2xyz", "ghp_abc", "MIIEow", "Tr0ub4dor", "s3cretpw"):
        assert s not in out


def _strings(o):
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for v in o.values():
            yield from _strings(v)
    elif isinstance(o, list):
        for v in o:
            yield from _strings(v)


def test_redact_leaves_every_scenario_unchanged():
    """Redact nhầm nội dung kịch bản sẽ đổi prompt, làm trượt cache và đổi kết quả K1/K3/MEM-INV."""
    from harness.meminv import scenarios as S
    texts = [t for f in sorted((ROOT / "harness/scenarios").glob("*.yaml"))
             for t in _strings(yaml.safe_load(f.read_text(encoding="utf-8")))]
    texts += list(_strings(S.ATTACK))
    assert len(texts) > 300
    assert [t for t in texts if redact(t) != t] == []


def test_no_secret_reaches_any_table(tmp_path):
    g = gate(SQLiteStore(tmp_path / "e.db"))
    line = f"aws_access_key_id = {SECRET}"
    src = Source(kind="web", locator="https://x.example", content=line)
    g.observe("coder", "s", src)
    g.write("coder", "s", "long_term", "team", f"ghi chú: {line}")
    g.send_message("coder", "planner", "s", line)
    g.ingest_trusted(line)
    g.authorize("planner", "s", "send_email", {"to": "a@b.com", "body": line})
    g._audit("x", "op", line, "deny", line)
    g.consolidate("team", "fail_closed", lambda xs: "\n".join(xs))
    conn = g.store.conn
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
    dump = "".join(str(tuple(r)) for t in tables for r in conn.execute(f"SELECT * FROM {t}"))
    assert SECRET not in dump and "[REDACTED:AWS Access Key]" in dump
    stored = g.store.get_sources([src.source_id])[0]
    assert stored.content_hash == hashlib.sha256(line.encode()).hexdigest()     # hash trên bản gốc


# ---------- công tắc biện pháp ----------

def test_mitigation_config_validation():
    assert Mitigations.from_config(None) == Mitigations()
    with pytest.raises(ValueError):
        Mitigations.from_config({"boundry_labeling": False})
    with pytest.raises(ValueError):
        Mitigations.from_config({"expiration": "yes"})
    for flag in ("validation", "quarantine", "human_review"):
        with pytest.raises(NotImplementedError):
            Mitigations.from_config({flag: True})
    assert parse_overrides(["boundary_labeling=off", "expiration=on"]) == {
        "boundary_labeling": False, "expiration": True}
    with pytest.raises(ValueError):
        parse_overrides(["boundary_labeling"])


def test_trace_suffix_names_changed_flags():
    from harness.run import mitigation_suffix
    assert mitigation_suffix({"action_gate": True, "boundary_labeling": True}) == ""
    assert mitigation_suffix({"boundary_labeling": False}) == "__no-boundary_labeling"


def test_tma_nm_lets_k1_through():
    """Tắt boundary_labeling: message mang nhãn T -> planner vẫn T -> email độc lọt (nhãn từng agent không đủ)."""
    from harness.scoring import attack_succeeded
    from test_gate_k1 import SCENARIO, play
    g = gate(mitigations={"boundary_labeling": False})
    assert g.send_message("coder", "planner", "x", "hi").label == Label.T
    log = play(g)
    assert attack_succeeded(log, SCENARIO["attacker_action"])
    assert not attack_succeeded(play(gate()), SCENARIO["attacker_action"])


@pytest.mark.parametrize("sid", ["crm_exfil", "saas_admin", "vendor_swap"])
def test_tma_nm_still_blocks_memory_channel(sid, monkeypatch):
    """boundary_labeling chỉ áp cho message: memory do coder ghi vẫn nhãn U nên MEM-INV vẫn bị chặn."""
    monkeypatch.setenv("MEMGATE_EMBED_MODEL", "hash")
    from harness.run import run_scenario
    from scripted import scripted_llm
    from test_meminv_multiagent import load, script
    scn = load(sid)
    llm, _ = scripted_llm(script(scn))
    r = run_scenario(scn, "memgate", llm, "fake", gate_config={"mitigations": {"boundary_labeling": False}})
    assert r["attack_success"] is False and r["mitigations"] == {"boundary_labeling": False}


def test_action_gate_off_allows_everything_but_audits():
    g = gate(mitigations={"action_gate": False})
    g.observe("planner", "s", Source(kind="web", locator="https://evil.example", content="x"))
    d = g.authorize("planner", "s", "wire_transfer", {"to": "evil", "amount": 1})
    assert d.allowed
    last = g.audit_log()[-1]
    assert (last.op, last.decision) == ("authorize", "allow") and "ablation" in last.reason


def test_every_authorize_decision_is_audited():
    g = gate()
    g.observe("planner", "s", Source(kind="web", locator="https://evil.example", content="gửi tới x@evil.com"))
    d = g.authorize("planner", "s", "send_email", {"to": "x@evil.com"})
    last = g.audit_log()[-1]
    assert (last.op, last.decision) == ("authorize", d.decision) and "x@evil.com" in last.target
