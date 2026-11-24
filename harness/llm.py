"""Gọi LLM qua một hàm duy nhất, có cache SQLite (việc 1, NFR8).

LLM_CACHE=readwrite (mặc định) | readonly (miss thì lỗi, dùng cho CI) | off
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

Messages = list[dict[str, str]]
CallFn = Callable[[str, Messages, dict[str, Any]], dict[str, Any]]


class CacheMiss(RuntimeError):
    pass


def cache_key(provider: str, model: str, messages: Messages, params: dict[str, Any]) -> str:
    payload = json.dumps(
        {"provider": provider, "model": model, "messages": messages, "params": params},
        sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class LLMCache:
    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, response TEXT NOT NULL, created_at REAL)"
        )

    def get(self, key: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT response FROM cache WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key: str, response: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO cache VALUES (?, ?, ?)",
            (key, json.dumps(response, ensure_ascii=False), time.time()),
        )
        self.conn.commit()


# Client SDK được tạo một lần và giữ lại: tạo rồi bỏ ngay (Client().x.y(...)) khiến
# google-genai đóng kết nối trước khi gửi request; giữ lại cũng tái dùng kết nối HTTP.
_CLIENTS: dict[str, Any] = {}


def _client(provider: str) -> Any:
    if provider not in _CLIENTS:
        if provider == "gemini":
            from google import genai  # pip install google-genai; key trong GEMINI_API_KEY

            _CLIENTS[provider] = genai.Client()
        elif provider == "openai":
            from openai import OpenAI

            _CLIENTS[provider] = OpenAI()
        else:
            from anthropic import Anthropic

            _CLIENTS[provider] = Anthropic()
    return _CLIENTS[provider]


def _call_openai(model: str, messages: Messages, params: dict[str, Any]) -> dict[str, Any]:
    resp = _client("openai").chat.completions.create(model=model, messages=messages, **params)
    u = resp.usage
    return {
        "text": resp.choices[0].message.content or "",
        "usage": {"input": u.prompt_tokens, "output": u.completion_tokens} if u else {},
    }


def _call_anthropic(model: str, messages: Messages, params: dict[str, Any]) -> dict[str, Any]:
    params = dict(params)
    params.pop("seed", None)  # không hỗ trợ; vẫn nằm trong khoá cache
    max_tokens = params.pop("max_tokens", 1024)
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    rest = [m for m in messages if m["role"] != "system"]
    kwargs: dict[str, Any] = {"system": system} if system else {}
    resp = _client("anthropic").messages.create(model=model, max_tokens=max_tokens, messages=rest, **kwargs, **params)
    return {
        "text": "".join(b.text for b in resp.content if b.type == "text"),
        "usage": {"input": resp.usage.input_tokens, "output": resp.usage.output_tokens},
    }


def _gemini_request(messages: Messages, params: dict[str, Any]) -> tuple[list[dict], dict[str, Any]]:
    """Chuyển messages kiểu OpenAI sang contents + config của Gemini (tách để test không cần mạng)."""
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    contents = [
        {"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
        for m in messages if m["role"] != "system"
    ]
    config = {k: v for k, v in params.items() if k in ("temperature", "seed", "max_output_tokens", "top_p")}
    if "max_tokens" in params:
        config["max_output_tokens"] = params["max_tokens"]
    if system:
        config["system_instruction"] = system
    return contents, config


def _call_gemini(model: str, messages: Messages, params: dict[str, Any]) -> dict[str, Any]:
    contents, config = _gemini_request(messages, params)
    resp = _client("gemini").models.generate_content(model=model, contents=contents, config=config)
    u = resp.usage_metadata
    return {
        "text": resp.text or "",
        "usage": {"input": u.prompt_token_count or 0, "output": u.candidates_token_count or 0} if u else {},
    }


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


class HTTPStatusError(RuntimeError):
    """Lỗi HTTP từ provider; `code` để is_retryable nhận 429/5xx, Retry-After để retry_delay đọc."""

    def __init__(self, code: int, body: str, retry_after: str | None = None) -> None:
        hint = f" Please retry in {retry_after}s." if retry_after else ""
        super().__init__(f"HTTP {code}: {body[:500]}{hint}")
        self.code = code


def _openrouter_body(model: str, messages: Messages, params: dict[str, Any]) -> dict[str, Any]:
    """Body kiểu OpenAI chat completions (tách để test không cần mạng)."""
    body: dict[str, Any] = {"model": model, "messages": messages}
    for k in ("temperature", "seed", "top_p", "max_tokens"):
        if k in params:
            body[k] = params[k]
    if "max_output_tokens" in params:
        body["max_tokens"] = params["max_output_tokens"]
    return body


def _call_openrouter(model: str, messages: Messages, params: dict[str, Any]) -> dict[str, Any]:
    """OpenRouter qua urllib của thư viện chuẩn (không cần SDK, không có DLL). Key: OPENROUTER_API_KEY."""
    import urllib.error
    import urllib.request

    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("Chưa đặt OPENROUTER_API_KEY (set OPENROUTER_API_KEY=... trong cửa sổ cmd)")
    req = urllib.request.Request(
        OPENROUTER_URL, data=json.dumps(_openrouter_body(model, messages, params)).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "X-Title": "MemGate"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise HTTPStatusError(e.code, e.read().decode("utf-8", "replace"), e.headers.get("Retry-After")) from None
    if "error" in data:   # OpenRouter có thể trả 200 kèm lỗi của nhà cung cấp phía sau
        err = data["error"]
        code = err.get("code") if isinstance(err.get("code"), int) else 502
        raise HTTPStatusError(code, json.dumps(err, ensure_ascii=False))
    u = data.get("usage") or {}
    return {
        "text": (data["choices"][0]["message"].get("content") or ""),
        "usage": {"input": u.get("prompt_tokens", 0), "output": u.get("completion_tokens", 0)} if u else {},
    }


PROVIDERS: dict[str, CallFn] = {
    "gemini": _call_gemini,
    "openrouter": _call_openrouter,
    "openai": _call_openai,
    "anthropic": _call_anthropic,
}


class LLM:
    def __init__(
        self,
        provider: str = "gemini",
        cache_path: str | Path = ".cache/llm.sqlite",
        mode: str | None = None,
        call_fn: CallFn | None = None,  # thay provider trong test
        rpm: int | None = None,         # giới hạn lời gọi thật mỗi phút (gói miễn phí Gemini: 15)
        max_retries: int = 6,           # số lần thử lại khi gặp 429, 5xx hoặc lỗi mạng
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.rpm, self.max_retries, self._sleep, self._clock = rpm, max_retries, sleep, clock
        self._recent: deque[float] = deque()
        self.provider = provider
        self.mode = mode or os.environ.get("LLM_CACHE", "readwrite")
        if self.mode not in ("readwrite", "readonly", "off"):
            raise ValueError(f"LLM_CACHE không hợp lệ: {self.mode}")
        self.cache = LLMCache(cache_path)
        self._call = call_fn or PROVIDERS[provider]
        self.stats = {"hits": 0, "misses": 0, "input_tokens": 0, "output_tokens": 0, "retries": 0}

    def chat(self, model: str, messages: Messages, **params: Any) -> str:
        key = cache_key(self.provider, model, messages, params)
        if self.mode != "off":
            hit = self.cache.get(key)
            if hit is not None:
                self.stats["hits"] += 1
                return hit["text"]
            if self.mode == "readonly":
                raise CacheMiss(key)
        response = self._call_with_limits(model, messages, params)
        self.stats["misses"] += 1
        self.stats["input_tokens"] += response.get("usage", {}).get("input", 0)
        self.stats["output_tokens"] += response.get("usage", {}).get("output", 0)
        if self.mode == "readwrite":
            self.cache.put(key, response)
        return response["text"]

    # ---------- giới hạn tốc độ và thử lại (chỉ áp cho lời gọi thật, không áp cho cache) ----------

    def _wait_for_slot(self) -> None:
        if not self.rpm:
            return
        now = self._clock()
        while self._recent and now - self._recent[0] >= 60:
            self._recent.popleft()
        if len(self._recent) >= self.rpm:
            self._sleep(60 - (now - self._recent[0]) + 0.5)
            now = self._clock()
            while self._recent and now - self._recent[0] >= 60:
                self._recent.popleft()
        self._recent.append(self._clock())

    def _call_with_limits(self, model: str, messages: Messages, params: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(self.max_retries + 1):
            self._wait_for_slot()
            try:
                return self._call(model, messages, params)
            except Exception as e:  # noqa: BLE001
                if not is_retryable(e) or attempt == self.max_retries:
                    raise
                delay = retry_delay(e) or min(2 ** attempt * 5, 60)
                self.stats["retries"] += 1
                if is_transient_network(e):
                    _CLIENTS.clear()  # bỏ client cũ: pool kết nối có thể còn giữ socket đã chết
                    reason = f"lỗi mạng ({type(e).__name__})"
                else:
                    reason = str(status_of(e))
                print(f"[llm] {reason}, chờ {delay:.0f}s rồi thử lại ({attempt + 1}/{self.max_retries})",
                      file=sys.stderr)
                self._sleep(delay)
        raise AssertionError("unreachable")


RETRYABLE = {429, 500, 502, 503, 504}


def status_of(e: Exception) -> int | None:
    code = getattr(e, "code", None) or getattr(e, "status_code", None)
    return code if isinstance(code, int) else None


# Lỗi mạng tạm thời của httpx/httpcore (bản trên máy là httpx2/httpcore2) — so theo tên lớp
# để không phụ thuộc gói nào đang cài.
TRANSIENT_NETWORK = {"ReadError", "WriteError", "ConnectError", "RemoteProtocolError", "URLError",
                     "ReadTimeout", "WriteTimeout", "ConnectTimeout", "PoolTimeout"}


def is_transient_network(e: BaseException) -> bool:
    """Kết nối bị ngắt/timeout (vd. WinError 10053), tìm dọc chuỗi __cause__/__context__."""
    seen: set[int] = set()
    cur: BaseException | None = e
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if type(cur).__name__ in TRANSIENT_NETWORK or isinstance(cur, (ConnectionError, TimeoutError)):
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def is_retryable(e: Exception) -> bool:
    """Lỗi tạm thời: quá hạn mức (429), quá tải/lỗi máy chủ (5xx) hoặc mạng bị ngắt."""
    if status_of(e) in RETRYABLE or is_transient_network(e):
        return True
    text = str(e)
    return any(k in text for k in ("RESOURCE_EXHAUSTED", "UNAVAILABLE", "rate limit", "Rate limit"))


def retry_delay(e: Exception) -> float | None:
    """Đọc thời gian chờ provider gợi ý ("retry in 8.8s" hoặc retryDelay '8s')."""
    m = re.search(r"retry in ([0-9.]+)s", str(e)) or re.search(r"retryDelay'?:?\s*'?([0-9.]+)s", str(e))
    return float(m.group(1)) + 1 if m else None
