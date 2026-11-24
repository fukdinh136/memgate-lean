from harness.llm import LLM
from harness.smoke import smoke


def test_smoke_passes_with_cache(tmp_path):
    calls = []
    llm = LLM(cache_path=tmp_path / "c.sqlite", mode="readwrite",
              call_fn=lambda m, msgs, p: calls.append(1) or {"text": "OK", "usage": {"input": 5, "output": 1}})
    assert smoke(llm, "fake", {"temperature": 0})
    assert len(calls) == 1


def test_smoke_fails_without_cache(tmp_path):
    llm = LLM(cache_path=tmp_path / "c.sqlite", mode="off",
              call_fn=lambda m, msgs, p: {"text": "OK", "usage": {}})
    assert not smoke(llm, "fake", {})
