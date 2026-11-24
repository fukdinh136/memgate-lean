"""Việc 3: kịch bản MEM-INV-Bench dạng nhiều agent, chạy bằng LLM giả lập (không tốn token)."""
import re
from pathlib import Path

import pytest

from harness.meminv import scenarios as S
from harness.meminv import to_yaml
from harness.run import build_team, load_scenario, run_scenario
from harness.team import TEAMS
from harness.scoring import matches
from harness.tools import ToolCall
from memgate.action_gate import sensitive_values
from memgate.normalize import canonical_args
from scripted import echo_last_result, final, scripted_llm, tool

SC = Path(__file__).parents[1] / "harness/scenarios"
ORIG = {s["sid"]: s for s in S.ATTACK}
MAIN = ["acme_pay", "crm_exfil", "saas_admin", "vendor_swap"]


@pytest.fixture(autouse=True)
def _hash_embedder(monkeypatch):
    """recall cần embedding; test dùng HashEmbedder để không phải nạp model ONNX."""
    monkeypatch.setenv("MEMGATE_EMBED_MODEL", "hash")


def load(sid, clean=False):
    return load_scenario(SC / (f"meminv_{sid}" + ("_clean" if clean else "") + ".yaml"))


# ---------- bộ chuyển đổi ----------

def test_generated_files_up_to_date():
    for path, text in to_yaml.outputs(to_yaml.DEFAULT_SIDS).items():
        assert path.read_text(encoding="utf-8") == text, f"{path.name}: chạy python -m harness.meminv.to_yaml"


def test_converter_is_deterministic():
    assert to_yaml.outputs(MAIN) == to_yaml.outputs(MAIN)


@pytest.mark.parametrize("sid", to_yaml.DEFAULT_SIDS)
def test_scenario_fields(sid):
    a, c = load(sid), load(sid, clean=True)
    for s in (a, c):
        assert s["action_tools"] == to_yaml.ACTION_TOOLS and s["ingest"]["agent"] == "coder"
        assert "recall" in s["user_task"]
    assert a["attacker_action"]["tool"] in to_yaml.ACTION_TOOLS and a["poison_markers"]
    assert "attacker_action" not in c and "poison_markers" not in c
    page_a, page_c = next(iter(a["web"].values())), next(iter(c["web"].values()))
    assert any(m in page_a for m in a["poison_markers"])
    assert not any(m in page_c for m in a["poison_markers"])


# ---------- update_setting: một dạng tham số cho cả cổng và scorer ----------

@pytest.mark.parametrize("args", [
    {"key": "external_sharing", "value": "domain_only"},
    {"key": "external_sharing", "value": "external_sharing=domain_only"},
    {"key": "external_sharing=domain_only"},
    {"value": "external_sharing = domain_only"},
])
def test_canonical_setting_args(args):
    assert canonical_args("update_setting", args) == {"key": "external_sharing", "value": "domain_only"}
    assert sorted(sensitive_values("update_setting", args)) == ["domain_only", "external_sharing"]
    call = ToolCall("planner", "b", "update_setting", args, "allow")
    assert matches(call, {"tool": "update_setting", "value": "domain_only"})


def test_canonical_args_leaves_other_tools():
    args = {"to": "a=b"}
    assert canonical_args("pay", args) is args


# ---------- đội: action_tools thay bộ tool hành động, đội gốc không đổi ----------

def test_build_team_replaces_action_tools_only_for_meminv():
    team = build_team(load("acme_pay"))
    assert team["planner"].tools == to_yaml.ACTION_TOOLS + ["recall"]
    assert team["coder"] is TEAMS["pair"]["coder"]
    k1 = load_scenario(SC / "k1_relay.yaml")
    assert build_team(k1) is TEAMS["pair"]   # prompt K1/K3 giữ nguyên -> cache cũ vẫn trúng


# ---------- chạy cả vòng với LLM giả lập ----------

def calls(scn):
    """Lời gọi độc và lời gọi đúng mà planner giả lập thực hiện, đủ tham số như LLM thật."""
    orig = ORIG[scn["source"]["sid"]]
    t, fact = orig["tool"], scn["trusted_facts"][0]
    amount = re.search(r"\$([\d,]+)", scn["user_task"])
    amount = amount.group(1).replace(",", "") if amount else None
    evil = scn.get("poison_markers", [None])[0]

    def one(value):
        if t == "pay":
            return tool("pay", to=value, amount=float(amount))
        if t == "send_data":
            return tool("send_data", to=value, data="customer export")
        if t == "update_setting":   # dạng "k=v" trong value, để kiểm canonical_args trên cả vòng
            return tool("update_setting", key=value.partition("=")[0], value=value)
        if t == "purchase":
            return tool("purchase", item="office supply bundle", to=value, amount=float(amount))
        raise ValueError(t)

    assert orig["good"].partition("=")[2].strip() in fact if t == "update_setting" else orig["good"] in fact
    return (one(evil) if evil else None), one(orig["good"])


def script(scn):
    url = next(iter(scn["web"]))
    evil, good = calls(scn)
    planner = [tool("recall", query="notes")] + ([evil] if evil else []) + [good, final("done")]
    coder = [tool("fetch", url=url),
             lambda m: tool("remember", content=m[-1]["content"].removeprefix("Kết quả: "), scope="team"),
             final("đã ghi nhớ")]
    return {"coder": coder, "planner": planner}


@pytest.mark.parametrize("sid", MAIN)
def test_attack_succeeds_without_defense(sid):
    scn = load(sid)
    llm, queues = scripted_llm(script(scn))
    r = run_scenario(scn, "null", llm, "fake")
    assert r["attack_success"] is True and r["benign_completed"] is True
    assert r["poison_stored"] is True and r["poison_recalled"] is True
    assert all(not q for q in queues.values())


@pytest.mark.parametrize("sid", MAIN)
def test_memgate_blocks_attack_allows_benign(sid):
    scn = load(sid)
    llm, _ = scripted_llm(script(scn))
    r = run_scenario(scn, "memgate", llm, "fake")
    assert r["attack_success"] is False and r["benign_completed"] is True
    assert r["poison_stored"] is True and r["poison_recalled"] is True
    planner = [c for c in r["tool_calls"] if c["session"] == "b"]
    assert [c["decision"] for c in planner] == ["review", "allow"]


@pytest.mark.parametrize("sid", MAIN)
def test_clean_variant_not_blocked(sid):
    scn = load(sid, clean=True)
    llm, _ = scripted_llm(script(scn))
    r = run_scenario(scn, "memgate", llm, "fake")
    assert r["attack_success"] is None and r["benign_completed"] is True
    assert all(c["decision"] == "allow" for c in r["tool_calls"])
