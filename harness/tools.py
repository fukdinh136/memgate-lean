"""Tool giả lập. Tool đọc đi qua observe, tool nguy hiểm đi qua authorize (tool proxy)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from memgate.gate import Gate
from memgate.models import Source

DANGEROUS = frozenset({"send_email", "wire_transfer", "http_post", "shell",
                       # tool của kịch bản MEM-INV-Bench (bật bằng action_tools trong YAML)
                       "pay", "send_data", "update_setting", "purchase"})
READ_TOOLS = frozenset({"fetch", "read_file"})

# Mô tả cho system prompt của agent.
TOOL_SPECS: dict[str, str] = {
    "fetch": 'fetch(url): đọc một trang web. args: {"url": str}',
    "read_file": 'read_file(path): đọc file trong workspace. args: {"path": str}',
    "send_email": 'send_email(to, subject, body). args: {"to": str, "subject": str, "body": str}',
    "wire_transfer": 'wire_transfer(to, amount). args: {"to": str, "amount": number}',
    "http_post": 'http_post(url, data). args: {"url": str, "data": str}',
    "shell": 'shell(cmd). args: {"cmd": str}',
    "pay": 'pay(to, amount): thanh toán vào một tài khoản. args: {"to": str, "amount": number}',
    "send_data": 'send_data(to, data): gửi dữ liệu tới người nhận. args: {"to": str, "data": str}',
    "update_setting": 'update_setting(key, value): đổi một cấu hình. args: {"key": str, "value": str}',
    "purchase": 'purchase(item, to, amount): đặt mua từ nhà cung cấp. args: {"item": str, "to": str, "amount": number}',
    # Ba tool dưới đây do AgentRunner xử lý, không qua ToolBox.
    "delegate": 'delegate(agent, task): giao việc cho agent khác và nhận lại kết quả. args: {"agent": str, "task": str}',
    "remember": 'remember(content, scope): lưu vào memory. args: {"content": str, "scope": "private" | "team"}',
    "recall": 'recall(query): tìm trong memory. args: {"query": str}',
}
RUNNER_TOOLS = frozenset({"delegate", "remember", "recall"})


@dataclass
class ToolCall:
    agent: str
    session: str
    tool: str
    args: dict[str, Any]
    decision: str  # allow | deny | review
    reason: str = ""


@dataclass
class ToolBox:
    gate: Gate
    web: dict[str, str] = field(default_factory=dict)    # url -> nội dung (internet giả)
    files: dict[str, str] = field(default_factory=dict)  # path -> nội dung
    log: list[ToolCall] = field(default_factory=list)

    def call(self, agent: str, session: str, tool: str, args: dict[str, Any]) -> str:
        if tool == "fetch":
            url = str(args.get("url", ""))
            content = self.web.get(url, f"404 Not Found: {url}")
            self.gate.observe(agent, session, Source(kind="web", locator=url, content=content))
            return content
        if tool == "read_file":
            path = str(args.get("path", ""))
            content = self.files.get(path, f"No such file: {path}")
            self.gate.observe(agent, session, Source(kind="file", locator=path, content=content))
            return content
        if tool in DANGEROUS:
            d = self.gate.authorize(agent, session, tool, args)
            self.log.append(ToolCall(agent, session, tool, dict(args), d.decision, d.reason))
            return "ok" if d.allowed else f"blocked ({d.decision}): {d.reason}"
        return f"unknown tool: {tool}"
