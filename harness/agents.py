"""Vòng lặp agent đơn + tool delegate (việc 3).

Mỗi agent là một vòng lặp: gọi LLM -> parse một action JSON -> thực thi -> đưa kết quả
lại vào hội thoại. Nhiều agent = một agent gọi `delegate`, giống cách OpenHands trả kết
quả child conversation về agent cha. Nhãn do Gate theo dõi, harness không tự xử lý nhãn.

Định dạng action:
  {"tool": "<tên>", "args": {...}}
  {"final": "<kết quả>"}
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from harness.tools import RUNNER_TOOLS, TOOL_SPECS, ToolBox
from memgate.gate import Gate
from memgate.policy import PolicyDenied
from memgate.models import Source

ACTION_FORMAT = """Mỗi lượt, trả lời DUY NHẤT một object JSON theo một trong hai dạng:
{"tool": "<tên tool>", "args": {...}}
{"final": "<kết quả cuối cùng>"}
Sau mỗi lần gọi tool bạn sẽ nhận được kết quả. Khi xong việc, trả về "final"."""


@dataclass
class AgentSpec:
    name: str
    system_prompt: str
    tools: list[str] = field(default_factory=list)
    can_delegate_to: list[str] = field(default_factory=list)

    @property
    def all_tools(self) -> list[str]:
        return self.tools + (["delegate"] if self.can_delegate_to else [])

    def system_message(self) -> str:
        specs = "\n".join(f"- {TOOL_SPECS[t]}" for t in self.all_tools)
        delegate = (
            f"\nCó thể giao việc cho: {', '.join(self.can_delegate_to)}." if self.can_delegate_to else ""
        )
        return (
            f"Tên của bạn: {self.name}.\n{self.system_prompt}\n\n"
            f"Tool được dùng:\n{specs}{delegate}\n\n{ACTION_FORMAT}"
        )


_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_JSON_OBJ = re.compile(r"\{.*\}", re.DOTALL)


def parse_action(text: str) -> dict[str, Any]:
    """Lấy object JSON đầu tiên hợp lệ trong câu trả lời; lỗi thì trả {"error": ...}."""
    text = text.strip()
    candidates = [m.group(1) for m in _FENCE.finditer(text)]
    m = _JSON_OBJ.search(text)
    if m:
        candidates.append(m.group(0))
    for c in candidates:
        try:
            obj = json.loads(c)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and ({"tool", "final"} & obj.keys()):
            return obj
    return {"error": "không parse được action", "raw": text[:500]}


@dataclass
class RunResult:
    agent: str
    final: str | None
    stopped: Literal["final", "max_steps"]
    steps: int


class AgentRunner:
    def __init__(
        self,
        team: dict[str, AgentSpec],
        gate: Gate,
        toolbox: ToolBox,
        llm: Any,  # harness.llm.LLM hoặc object có .chat(model, messages, **params)
        model: str,
        params: dict[str, Any] | None = None,
        max_steps: int = 10,
        max_depth: int = 3,
    ) -> None:
        self.team = team
        self.gate = gate
        self.toolbox = toolbox
        self.llm = llm
        self.model = model
        self.params = params or {}
        self.max_steps = max_steps
        self.max_depth = max_depth
        self.trace: list[dict[str, Any]] = []

    def run(self, agent: str, session: str, task: str, *, delegated: bool = False, depth: int = 0) -> RunResult:
        """Chạy một agent tới khi trả final hoặc hết bước.

        delegated=False: task đến từ người dùng -> observe với nhãn T.
        delegated=True: task đã được observe qua gate.send_message trong _delegate.
        """
        spec = self.team[agent]
        if not delegated:
            self.gate.observe(agent, session, Source(kind="user", locator="user_task", content=task))
        messages = [
            {"role": "system", "content": spec.system_message()},
            {"role": "user", "content": task},
        ]
        for step in range(1, self.max_steps + 1):
            text = self.llm.chat(self.model, messages, **self.params)
            messages.append({"role": "assistant", "content": text})
            action = parse_action(text)
            if "final" in action:
                self._log(agent, session, depth, step, action, None)
                return RunResult(agent, str(action["final"]), "final", step)
            observation = self._execute(spec, session, action, depth)
            self._log(agent, session, depth, step, action, observation)
            messages.append({"role": "user", "content": f"Kết quả: {observation}"})
        return RunResult(agent, None, "max_steps", self.max_steps)

    # ---------- thực thi action ----------

    def _execute(self, spec: AgentSpec, session: str, action: dict[str, Any], depth: int) -> str:
        if "error" in action:
            return f"Lỗi: {action['error']}. Trả lời đúng định dạng JSON."
        tool = action.get("tool")
        args = action.get("args") or {}
        if not isinstance(args, dict):
            return "Lỗi: args phải là object JSON."
        if tool not in spec.all_tools:
            return f"Lỗi: bạn không có tool '{tool}'."
        if tool == "delegate":
            return self._delegate(spec, session, args, depth)
        if tool == "remember":
            try:
                v = self.gate.write(spec.name, session, "long_term", str(args.get("scope", "team")),
                                    str(args.get("content", "")))
            except PolicyDenied as e:
                return f"Lỗi: bị policy từ chối ({e.reason})."
            # Không đưa item_id (sinh ngẫu nhiên) vào prompt: prompt phải giống nhau giữa các lần chạy
            # để cache LLM trúng và kết quả tái lập được (NFR8). item_id vẫn có trong store.
            return "Đã lưu."
        if tool == "recall":
            try:
                hits = self.gate.retrieve(spec.name, session, str(args.get("query", "")), k=int(args.get("k", 5)))
            except PolicyDenied as e:
                return f"Lỗi: bị policy từ chối ({e.reason})."
            return "\n".join(f"- {v.content}" for v in hits) or "(không có kết quả)"
        return self.toolbox.call(spec.name, session, tool, args)

    def _delegate(self, spec: AgentSpec, session: str, args: dict[str, Any], depth: int) -> str:
        target = str(args.get("agent", ""))
        task = str(args.get("task", ""))
        if target not in spec.can_delegate_to:
            return f"Lỗi: không được giao việc cho '{target}'."
        if depth + 1 > self.max_depth:
            return "Lỗi: quá độ sâu giao việc cho phép."
        self.gate.send_message(spec.name, target, session, task)
        result = self.run(target, session, task, delegated=True, depth=depth + 1)
        content = result.final if result.final is not None else f"({target} không hoàn thành: {result.stopped})"
        self.gate.send_message(target, spec.name, session, content)
        return content

    def _log(self, agent, session, depth, step, action, observation) -> None:
        self.trace.append(
            {"agent": agent, "session": session, "depth": depth, "step": step,
             "action": action, "observation": observation,
             "context_label": int(self.gate.context_label(agent, session))}
        )
