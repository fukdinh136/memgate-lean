"""Việc 6 (FR7): policy theo vai trò × scope × hành động, share/promote, audit mọi lần từ chối."""
from pathlib import Path

import pytest
import yaml

from harness.agents import AgentRunner
from harness.team import TEAMS
from harness.tools import ToolBox
from memgate import Label, MemGate, PolicyDenied, PolicyEngine, Source
from memgate.embed import HashEmbedder
from memgate.policy import DEFAULT_POLICY, PolicyError
from memgate.store import SQLiteStore
from scripted import echo_last_result, final, scripted_llm, tool

ROOT = Path(__file__).resolve().parents[1]


def gate(policy=None, **kw):
    return MemGate(embedder=HashEmbedder(), policy=policy, **kw)


def policy_with(*extra, agents=None, default_role="member"):
    d = {**DEFAULT_POLICY, "policies": [*DEFAULT_POLICY["policies"], *extra],
         "agents": {**DEFAULT_POLICY["agents"], **(agents or {})}, "default_role": default_role}
    return PolicyEngine.from_dict(d)


def denies(g, actor=None):
    return g.audit_log(actor=actor, decision="deny")


# ---------- private: chỉ chủ sở hữu ----------

def test_private_item_is_invisible_and_unwritable_for_other_agents():
    g = gate()
    v = g.write("coder", "s", "long_term", "private", "khoá nháp của coder")
    assert g.retrieve("planner", "s", "khoá nháp") == []                       # store lọc theo owner
    with pytest.raises(PolicyDenied):
        g.write("planner", "s", "long_term", "private", "ghi đè", item_id=v.item_id)
    with pytest.raises(PolicyDenied):
        g.rollback(v.item_id, v.version_id, agent="planner")
    with pytest.raises(PolicyDenied):
        g.share("planner", "s", v.item_id, "team")
    assert [h.content for h in g.history(v.item_id)] == ["khoá nháp của coder"]   # không gì lọt qua
    assert [e.op for e in denies(g, "planner")] == ["write", "rollback", "share"]
    assert [h.content for h in g.retrieve("coder", "s2", "khoá nháp")] == ["khoá nháp của coder"]


def test_private_owner_invariant_cannot_be_overridden_by_policy():
    p = policy_with({"policy_id": "x1", "role_id": "admin", "scope": "*", "action": "*", "effect": "allow"},
                    agents={"boss": "admin"})
    g = gate(p)
    v = g.write("coder", "s", "long_term", "private", "bí mật")
    with pytest.raises(PolicyDenied):
        g.write("boss", "s", "long_term", "private", "x", item_id=v.item_id)


# ---------- đánh giá luật ----------

def test_deny_wins_over_allow():
    p = policy_with({"policy_id": "d1", "role_id": "member", "scope": "team", "action": "write", "effect": "deny"})
    g = gate(p)
    with pytest.raises(PolicyDenied) as ei:
        g.write("coder", "s", "long_term", "team", "x")
    assert "d1" in ei.value.reason
    assert len(denies(g, "coder")) == 1


def test_no_matching_rule_and_unknown_agent_are_denied():
    eng = PolicyEngine.default()
    assert not eng.check("coder", "write", "global").allowed            # không có policy allow
    strict = policy_with(default_role=None)
    assert not strict.check("stranger", "read", "team").allowed         # agent lạ, không vai trò
    assert strict.check("coder", "read", "team").allowed
    g = gate(strict)
    with pytest.raises(PolicyDenied):
        g.write("stranger", "s", "long_term", "team", "x")
    with pytest.raises(PolicyDenied):
        g.write("coder", "s", "long_term", "bogus", "x")                # scope sai cũng là deny


# ---------- share / promote ----------

def test_promote_private_to_team_keeps_label_and_history():
    g = gate()
    g.observe("coder", "s", Source(kind="web", locator="https://evil.example", content="x"))
    v = g.write("coder", "s", "long_term", "private", "endpoint là evil.com")
    item = g.share("coder", "s", v.item_id, "team")
    assert item.scope == "team"
    assert [(h.version_id, h.label) for h in g.history(v.item_id)] == [(v.version_id, Label.U)]
    hits = g.retrieve("planner", "s2", "endpoint")
    assert [h.label for h in hits] == [Label.U]                          # promote không nâng nhãn
    assert g.context_label("planner", "s2") == Label.U
    assert [(e.op, e.decision) for e in g.audit_log(actor="coder")] == [("share", "allow")]


def test_promote_to_global_needs_admin_and_narrowing_is_denied():
    g = gate(policy_with(agents={"boss": "admin"}))
    v = g.write("coder", "s", "long_term", "team", "lịch họp")
    with pytest.raises(PolicyDenied):
        g.share("coder", "s", v.item_id, "global")
    assert g.share("boss", "s", v.item_id, "global").scope == "global"
    with pytest.raises(PolicyDenied):
        g.share("boss", "s", v.item_id, "team")                         # thu hẹp
    with pytest.raises(PolicyDenied):
        g.share("boss", "s", v.item_id, "global")                       # không đổi
    assert len(denies(g)) == 3


# ---------- retrieve lọc scope ----------

def test_retrieve_filters_denied_scopes_and_audits_once_per_call():
    p = policy_with({"policy_id": "d2", "role_id": "member", "scope": "global", "action": "read", "effect": "deny"})
    g = gate(p)
    g.ingest_trusted("Quản lý là boss@company.com", scope="global")
    g.write("coder", "s", "long_term", "team", "quản lý dự án là Lan")
    hits = g.retrieve("planner", "s", "quản lý")
    assert [h.content for h in hits] == ["quản lý dự án là Lan"]
    ev = denies(g, "planner")
    assert len(ev) == 1 and ev[0].op == "retrieve" and ev[0].target == "scopes:global"


# ---------- audit: mọi lần từ chối có đúng một dòng ----------

def test_every_denial_has_exactly_one_audit_event():
    g = gate(policy_with(default_role=None))
    v = g.write("coder", "s", "long_term", "private", "x")
    attempts = [
        lambda: g.write("planner", "s", "long_term", "private", "y", item_id=v.item_id),
        lambda: g.write("coder", "s", "long_term", "global", "y"),
        lambda: g.write("stranger", "s", "long_term", "team", "y"),
        lambda: g.rollback(v.item_id, v.version_id, agent="researcher"),
        lambda: g.share("researcher", "s", v.item_id, "team"),
        lambda: g.share("coder", "s", v.item_id, "global"),
    ]
    for i, attempt in enumerate(attempts, 1):
        with pytest.raises(PolicyDenied):
            attempt()
        assert len(denies(g)) == i
    assert [e.seq for e in g.audit_log()] == sorted(e.seq for e in g.audit_log())


def test_policy_and_audit_survive_restart(tmp_path):
    db = tmp_path / "m.db"
    g1 = gate(policy_with(agents={"boss": "admin"}), store=SQLiteStore(db))
    with pytest.raises(PolicyDenied):
        g1.write("coder", "s", "long_term", "global", "x")
    g2 = gate(store=SQLiteStore(db))                                     # không truyền policy → bản đã lưu
    assert g2.policy.role_of("boss") == "admin"
    assert len(g2.audit_log(decision="deny")) == 1


# ---------- YAML ----------

def test_config_yaml_matches_default_policy():
    assert yaml.safe_load((ROOT / "configs/policy.yaml").read_text(encoding="utf-8")) == DEFAULT_POLICY
    assert PolicyEngine.from_yaml(ROOT / "configs/policy.yaml").to_dict() == PolicyEngine.default().to_dict()


@pytest.mark.parametrize("bad", [
    {"roles": ["member"], "policies": [{"policy_id": "a", "role_id": "member", "scope": "teams",
                                       "action": "read", "effect": "allow"}]},
    {"roles": ["member"], "policies": [{"policy_id": "a", "role_id": "member", "scope": "team",
                                       "action": "read", "effect": "maybe"}]},
    {"roles": ["member"], "policies": [{"policy_id": "a", "role_id": "ghost", "scope": "team",
                                       "action": "read", "effect": "allow"}]},
    {"roles": ["member"], "agents": {"coder": "ghost"}, "policies": []},
    {"roles": ["member"], "policies": [{"policy_id": "a", "role_id": "member", "scope": "team",
                                       "action": "read", "effect": "allow", "condition": "owner"}]},
    {"roles": ["member"], "policies": [], "extra": 1},
])
def test_bad_policy_rejected_at_load(bad):
    with pytest.raises(PolicyError):
        PolicyEngine.from_dict(bad)


# ---------- harness ----------

def test_remember_denied_is_reported_to_agent_and_loop_continues():
    llm, _ = scripted_llm({
        "coder": [tool("remember", content="ghi lên global", scope="global"), echo_last_result],
    })
    g = gate()
    runner = AgentRunner(TEAMS["pair"], g, ToolBox(gate=g), llm, "fake")
    res = runner.run("coder", "a", "Ghi nhớ")
    assert res.final.startswith("Lỗi: bị policy từ chối")
    assert len(denies(g, "coder")) == 1
