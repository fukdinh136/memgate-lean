"""Bất biến nhãn (spec 8.1, NFR2)."""
import random

from memgate import Label, MemGate, Source, output_label


def src(kind, label=None, content="x"):
    return Source(kind=kind, locator=f"{kind}://x", content=content, label=label)


def test_source_labels_by_channel():
    assert src("user").label == Label.T
    assert src("tool").label == Label.T
    for kind in ("web", "mcp", "file"):
        assert src(kind).label == Label.U


def test_cannot_upgrade_untrusted_source():
    assert src("web", label=Label.T).label == Label.U


def test_agent_source_without_label_is_untrusted():
    assert src("agent").label == Label.U


def test_output_never_trusted():
    assert all(output_label(l) <= Label.A for l in Label)


def test_new_context_starts_trusted_and_sessions_are_isolated():
    g = MemGate()
    g.observe("coder", "s1", src("web"))
    assert g.context_label("coder", "s1") == Label.U
    assert g.context_label("coder", "s2") == Label.T


def test_context_label_never_increases_random_ops():
    rng = random.Random(0)
    kinds = ["user", "tool", "web", "mcp", "file"]
    agents = ["planner", "coder", "reviewer"]
    for trial in range(50):
        g = MemGate()
        prev = {a: Label.T for a in agents}
        for _ in range(40):
            a = rng.choice(agents)
            if rng.random() < 0.5:
                g.observe(a, "s", src(rng.choice(kinds)))
            else:
                g.send_message(a, rng.choice(agents), "s", "hi")
            for x in agents:
                cur = g.context_label(x, "s")
                assert cur <= prev[x], f"trial {trial}: nhãn của {x} tăng"
                prev[x] = cur


def test_message_label_bounded_by_sender():
    g = MemGate()
    m = g.send_message("planner", "coder", "s", "việc cần làm")
    assert m.label == Label.A  # sạch nhưng vẫn là output của agent
    g.observe("coder", "s", src("web"))
    m2 = g.send_message("coder", "planner", "s", "kết quả")
    assert m2.label == Label.U


def test_k1_relay_lowers_receiver():
    """Kết quả của agent con đã đọc web không thể quay về với nhãn user."""
    g = MemGate()
    g.observe("planner", "s", src("user"))
    g.observe("coder", "s", src("web"))
    g.send_message("coder", "planner", "s", "gửi tới evil@x")
    assert g.context_label("planner", "s") == Label.U
