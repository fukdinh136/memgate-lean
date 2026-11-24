import pytest

from harness.llm import LLM, CacheMiss, cache_key


def fake_provider():
    calls = []

    def call(model, messages, params):
        calls.append((model, messages, params))
        return {"text": f"reply #{len(calls)}", "usage": {"input": 10, "output": 5}}

    return call, calls


def test_second_call_hits_cache(tmp_path):
    call, calls = fake_provider()
    llm = LLM(cache_path=tmp_path / "c.sqlite", mode="readwrite", call_fn=call)
    msgs = [{"role": "user", "content": "xin chào"}]
    a = llm.chat("m", msgs, temperature=0, seed=1)
    b = llm.chat("m", msgs, temperature=0, seed=1)
    assert a == b and len(calls) == 1 and llm.stats["hits"] == 1


def test_cache_survives_restart(tmp_path):
    call, calls = fake_provider()
    msgs = [{"role": "user", "content": "x"}]
    LLM(cache_path=tmp_path / "c.sqlite", mode="readwrite", call_fn=call).chat("m", msgs)
    LLM(cache_path=tmp_path / "c.sqlite", mode="readonly", call_fn=call).chat("m", msgs)
    assert len(calls) == 1


def test_different_params_or_seed_miss(tmp_path):
    call, calls = fake_provider()
    llm = LLM(cache_path=tmp_path / "c.sqlite", mode="readwrite", call_fn=call)
    msgs = [{"role": "user", "content": "x"}]
    llm.chat("m", msgs, seed=1)
    llm.chat("m", msgs, seed=2)
    llm.chat("m", msgs, seed=1, temperature=0.5)
    assert len(calls) == 3


def test_readonly_miss_raises(tmp_path):
    call, _ = fake_provider()
    llm = LLM(cache_path=tmp_path / "c.sqlite", mode="readonly", call_fn=call)
    with pytest.raises(CacheMiss):
        llm.chat("m", [{"role": "user", "content": "chưa có"}])


def test_off_mode_always_calls(tmp_path):
    call, calls = fake_provider()
    llm = LLM(cache_path=tmp_path / "c.sqlite", mode="off", call_fn=call)
    msgs = [{"role": "user", "content": "x"}]
    llm.chat("m", msgs)
    llm.chat("m", msgs)
    assert len(calls) == 2


def test_key_independent_of_param_order():
    msgs = [{"role": "user", "content": "x"}]
    assert cache_key("p", "m", msgs, {"a": 1, "b": 2}) == cache_key("p", "m", msgs, {"b": 2, "a": 1})


def test_gemini_request_mapping():
    from harness.llm import _gemini_request

    contents, config = _gemini_request(
        [{"role": "system", "content": "sys"}, {"role": "user", "content": "hỏi"},
         {"role": "assistant", "content": "đáp"}],
        {"temperature": 0, "seed": 1, "max_tokens": 64},
    )
    assert [c["role"] for c in contents] == ["user", "model"]
    assert config == {"temperature": 0, "seed": 1, "max_output_tokens": 64, "system_instruction": "sys"}


class FakeClock:
    def __init__(self):
        self.t, self.slept = 0.0, []

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def test_rate_limit_waits_after_rpm_calls(tmp_path):
    clk = FakeClock()
    llm = LLM(cache_path=tmp_path / "c.sqlite", mode="off", rpm=2, sleep=clk.sleep, clock=clk.now,
              call_fn=lambda m, msgs, p: {"text": "ok", "usage": {}})
    for i in range(3):
        llm.chat("m", [{"role": "user", "content": str(i)}])
    assert len(clk.slept) == 1 and clk.slept[0] >= 60


def test_cache_hits_do_not_count_against_rpm(tmp_path):
    clk = FakeClock()
    llm = LLM(cache_path=tmp_path / "c.sqlite", mode="readwrite", rpm=1, sleep=clk.sleep, clock=clk.now,
              call_fn=lambda m, msgs, p: {"text": "ok", "usage": {}})
    for _ in range(5):
        llm.chat("m", [{"role": "user", "content": "same"}])
    assert clk.slept == []


def test_retries_on_429_using_suggested_delay(tmp_path):
    clk, calls = FakeClock(), []

    class Quota(Exception):
        code = 429

    def call(m, msgs, p):
        calls.append(1)
        if len(calls) < 3:
            raise Quota("429 RESOURCE_EXHAUSTED ... Please retry in 8.8s.")
        return {"text": "ok", "usage": {}}

    llm = LLM(cache_path=tmp_path / "c.sqlite", mode="off", sleep=clk.sleep, clock=clk.now, call_fn=call)
    assert llm.chat("m", [{"role": "user", "content": "x"}]) == "ok"
    assert len(calls) == 3 and clk.slept == [9.8, 9.8] and llm.stats["retries"] == 2


def test_other_errors_are_not_retried(tmp_path):
    def call(m, msgs, p):
        raise ValueError("404 NOT_FOUND")

    llm = LLM(cache_path=tmp_path / "c.sqlite", mode="off", sleep=lambda s: None, call_fn=call)
    with pytest.raises(ValueError):
        llm.chat("m", [{"role": "user", "content": "x"}])


def test_retries_on_503_with_backoff(tmp_path):
    clk, calls = FakeClock(), []

    class Busy(Exception):
        code = 503

    def call(m, msgs, p):
        calls.append(1)
        if len(calls) < 3:
            raise Busy("503 UNAVAILABLE. This model is currently experiencing high demand.")
        return {"text": "ok", "usage": {}}

    llm = LLM(cache_path=tmp_path / "c.sqlite", mode="off", sleep=clk.sleep, clock=clk.now, call_fn=call)
    assert llm.chat("m", [{"role": "user", "content": "x"}]) == "ok"
    assert clk.slept == [5, 10]


def test_retries_on_network_error_in_cause_chain(tmp_path):
    clk, calls = FakeClock(), []

    class ReadError(Exception):  # giống httpx2.ReadError bọc ConnectionAbortedError
        pass

    def call(m, msgs, p):
        calls.append(1)
        if len(calls) < 3:
            try:
                raise ConnectionAbortedError(10053, "An established connection was aborted")
            except ConnectionAbortedError as inner:
                raise ReadError("[WinError 10053]") from inner
        return {"text": "ok", "usage": {}}

    llm = LLM(cache_path=tmp_path / "c.sqlite", mode="off", sleep=clk.sleep, clock=clk.now, call_fn=call)
    assert llm.chat("m", [{"role": "user", "content": "x"}]) == "ok"
    assert len(calls) == 3 and clk.slept == [5, 10] and llm.stats["retries"] == 2


def test_network_error_gives_up_after_max_retries(tmp_path):
    def call(m, msgs, p):
        raise ConnectionResetError("reset")

    llm = LLM(cache_path=tmp_path / "c.sqlite", mode="off", max_retries=2, sleep=lambda s: None, call_fn=call)
    with pytest.raises(ConnectionResetError):
        llm.chat("m", [{"role": "user", "content": "x"}])
    assert llm.stats["retries"] == 2


# ---------- OpenRouter (giả lập urlopen, không cần mạng) ----------

def _fake_urlopen(monkeypatch, responses, seen):
    import io
    import json as _json
    import urllib.error
    import urllib.request

    def fake(req, timeout=None):
        seen.append((req.full_url, dict(req.header_items()), _json.loads(req.data)))
        status, payload, headers = responses.pop(0)
        body = _json.dumps(payload).encode()
        if status != 200:
            raise urllib.error.HTTPError(req.full_url, status, "err", headers or {}, io.BytesIO(body))

        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        return Resp(body)

    monkeypatch.setattr(urllib.request, "urlopen", fake)


def test_openrouter_request_and_response(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    seen = []
    _fake_urlopen(monkeypatch, [(200, {"choices": [{"message": {"content": "xin chào"}}],
                                       "usage": {"prompt_tokens": 7, "completion_tokens": 2}}, None)], seen)
    llm = LLM(provider="openrouter", cache_path=tmp_path / "c.sqlite", mode="readwrite")
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    assert llm.chat("meta-llama/llama-3.1-8b-instruct", msgs, temperature=0, seed=3) == "xin chào"
    url, headers, body = seen[0]
    assert url == "https://openrouter.ai/api/v1/chat/completions"
    assert headers["Authorization"] == "Bearer sk-test"
    assert body == {"model": "meta-llama/llama-3.1-8b-instruct", "messages": msgs, "temperature": 0, "seed": 3}
    assert llm.stats["input_tokens"] == 7
    assert llm.chat("meta-llama/llama-3.1-8b-instruct", msgs, temperature=0, seed=3) == "xin chào"  # cache
    assert len(seen) == 1


def test_openrouter_429_uses_retry_after_and_400_is_not_retried(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    seen, slept = [], []
    _fake_urlopen(monkeypatch, [
        (429, {"error": {"message": "rate limited"}}, {"Retry-After": "7"}),
        (200, {"choices": [{"message": {"content": "ok"}}]}, None),
        (400, {"error": {"message": "bad model"}}, None),
    ], seen)
    llm = LLM(provider="openrouter", cache_path=tmp_path / "c.sqlite", mode="off", sleep=slept.append)
    assert llm.chat("m", [{"role": "user", "content": "x"}]) == "ok"
    assert slept == [8.0]                      # Retry-After 7s + 1
    with pytest.raises(Exception) as ei:
        llm.chat("m", [{"role": "user", "content": "y"}])
    assert getattr(ei.value, "code", None) == 400 and len(seen) == 3


def test_openrouter_error_in_200_body_and_missing_key(monkeypatch, tmp_path):
    seen = []
    _fake_urlopen(monkeypatch, [(200, {"error": {"code": 502, "message": "upstream"}}, None),
                                (200, {"choices": [{"message": {"content": "ok"}}]}, None)], seen)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    llm = LLM(provider="openrouter", cache_path=tmp_path / "c.sqlite", mode="off", sleep=lambda s: None)
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        llm.chat("m", [{"role": "user", "content": "x"}])
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    assert llm.chat("m", [{"role": "user", "content": "x"}]) == "ok"   # 502 trong body được thử lại
