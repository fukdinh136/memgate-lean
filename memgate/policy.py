"""Policy engine (việc 6, FR7), theo thực thể Role, Agent, Policy ở mục 6 của đặc tả.

- Mỗi agent có đúng một vai trò (Agent.role_id). Agent không có trong danh sách nhận
  `default_role`; không có default_role thì agent lạ không có vai trò và bị deny.
- Luật Policy(policy_id, role_id, scope, action, effect, condition); role_id "*" khớp mọi vai trò.
- Deny thắng. Không luật nào khớp → deny (fail-closed, NFR2).
- Bất biến cứng, luật không ghi đè được: mục private chỉ chủ sở hữu được đọc, ghi, promote.
- `condition` chưa có ngữ nghĩa trong đặc tả: chỉ nhận null; khác null thì báo lỗi khi nạp.
- Viết policy bằng YAML (configs/policy.yaml); MemGate lưu nó vào các bảng roles, agents, policies.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

SCOPES = ("private", "team", "global")
ACTIONS = ("read", "write", "promote")
EFFECTS = ("allow", "deny")
SCOPE_RANK = {s: i for i, s in enumerate(SCOPES)}

# Bản mặc định; configs/policy.yaml phải giống hệt (tests/test_policy.py kiểm tra).
DEFAULT_POLICY: dict[str, Any] = {
    "roles": ["member", "admin"],
    "default_role": "member",
    "agents": {"planner": "member", "coder": "member", "researcher": "member"},
    "policies": [
        {"policy_id": "p01", "role_id": "*", "scope": "private", "action": "read", "effect": "allow"},
        {"policy_id": "p02", "role_id": "*", "scope": "private", "action": "write", "effect": "allow"},
        {"policy_id": "p03", "role_id": "*", "scope": "team", "action": "read", "effect": "allow"},
        {"policy_id": "p04", "role_id": "*", "scope": "team", "action": "write", "effect": "allow"},
        {"policy_id": "p05", "role_id": "*", "scope": "team", "action": "promote", "effect": "allow"},
        {"policy_id": "p06", "role_id": "*", "scope": "global", "action": "read", "effect": "allow"},
        {"policy_id": "p07", "role_id": "admin", "scope": "global", "action": "write", "effect": "allow"},
        {"policy_id": "p08", "role_id": "admin", "scope": "global", "action": "promote", "effect": "allow"},
    ],
}


class PolicyError(ValueError):
    """Policy sai cấu trúc (báo ngay khi nạp)."""


class PolicyDenied(PermissionError):
    def __init__(self, agent: str, action: str, target: str, reason: str) -> None:
        super().__init__(f"{agent} không được {action} {target}: {reason}")
        self.agent, self.action, self.target, self.reason = agent, action, target, reason


@dataclass(frozen=True)
class Policy:
    policy_id: str
    role_id: str              # vai trò, hoặc "*"
    scope: str                # private | team | global | *
    action: str               # read | write | promote | *
    effect: str               # allow | deny
    condition: str | None = None

    def __post_init__(self) -> None:
        for name in ("policy_id", "role_id"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise PolicyError(f"{name} không hợp lệ: {getattr(self, name)!r}")
        if self.scope not in (*SCOPES, "*"):
            raise PolicyError(f"{self.policy_id}: scope không hợp lệ: {self.scope!r}")
        if self.action not in (*ACTIONS, "*"):
            raise PolicyError(f"{self.policy_id}: action không hợp lệ: {self.action!r}")
        if self.effect not in EFFECTS:
            raise PolicyError(f"{self.policy_id}: effect không hợp lệ: {self.effect!r}")
        if self.condition is not None:
            raise PolicyError(f"{self.policy_id}: condition chưa được hỗ trợ")

    def matches(self, role: str, scope: str, action: str) -> bool:
        return self.role_id in ("*", role) and self.scope in ("*", scope) and self.action in ("*", action)


@dataclass(frozen=True)
class Check:
    allowed: bool
    reason: str


_POLICY_KEYS = {"policy_id", "role_id", "scope", "action", "effect", "condition"}


class PolicyEngine:
    def __init__(self, roles: list[str], agents: dict[str, str], policies: list[Policy],
                 default_role: str | None = None) -> None:
        self.roles = list(dict.fromkeys(roles))
        known = set(self.roles)
        for a, r in agents.items():
            if r not in known:
                raise PolicyError(f"agent {a}: vai trò {r!r} không có trong roles")
        if default_role is not None and default_role not in known:
            raise PolicyError(f"default_role {default_role!r} không có trong roles")
        ids = [p.policy_id for p in policies]
        if len(ids) != len(set(ids)):
            raise PolicyError("policy_id bị trùng")
        for p in policies:
            if p.role_id != "*" and p.role_id not in known:
                raise PolicyError(f"{p.policy_id}: vai trò {p.role_id!r} không có trong roles")
        self.agents = dict(agents)
        self.policies = list(policies)
        self.default_role = default_role

    # ---------- nạp ----------

    @classmethod
    def from_dict(cls, d: Any) -> "PolicyEngine":
        if not isinstance(d, dict):
            raise PolicyError("policy phải là một mapping")
        unknown = set(d) - {"roles", "default_role", "agents", "policies"}
        if unknown:
            raise PolicyError(f"khoá không hợp lệ: {sorted(unknown)}")
        roles = d.get("roles")
        if not isinstance(roles, list) or not all(isinstance(r, str) and r for r in roles):
            raise PolicyError("roles phải là danh sách tên vai trò")
        agents = d.get("agents") or {}
        if not isinstance(agents, dict) or not all(isinstance(v, str) for v in agents.values()):
            raise PolicyError("agents phải là mapping agent -> một vai trò")
        raw = d.get("policies")
        if not isinstance(raw, list):
            raise PolicyError("thiếu danh sách policies")
        policies = []
        for i, p in enumerate(raw):
            if not isinstance(p, dict) or not ({"policy_id", "role_id", "scope", "action", "effect"}
                                               <= set(p) <= _POLICY_KEYS):
                raise PolicyError(f"policies[{i}] phải có policy_id, role_id, scope, action, effect"
                                  " (condition tuỳ chọn)")
            policies.append(Policy(**p))
        default_role = d.get("default_role")
        if default_role is not None and not isinstance(default_role, str):
            raise PolicyError("default_role phải là chuỗi")
        return cls(roles, agents, policies, default_role)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "PolicyEngine":
        with open(path, encoding="utf-8") as f:
            return cls.from_dict(yaml.safe_load(f))

    @classmethod
    def default(cls) -> "PolicyEngine":
        return cls.from_dict(DEFAULT_POLICY)

    def to_dict(self) -> dict[str, Any]:
        return {
            "roles": list(self.roles),
            "default_role": self.default_role,
            "agents": dict(self.agents),
            "policies": [{k: v for k, v in asdict(p).items() if not (k == "condition" and v is None)}
                         for p in self.policies],
        }

    # ---------- đánh giá ----------

    def role_of(self, agent: str) -> str | None:
        return self.agents.get(agent, self.default_role)

    def check(self, agent: str, action: str, scope: str, owner: str | None = None) -> Check:
        if action not in ACTIONS or scope not in SCOPES:
            return Check(False, f"hành động hoặc scope không hợp lệ: {action}/{scope}")
        if scope == "private" and owner is not None and owner != agent:
            return Check(False, f"mục private của {owner}")
        role = self.role_of(agent)
        if role is None:
            return Check(False, "agent chưa có vai trò")
        matched = [p for p in self.policies if p.matches(role, scope, action)]
        deny = next((p for p in matched if p.effect == "deny"), None)
        if deny:
            return Check(False, f"policy {deny.policy_id} deny")
        allow = next((p for p in matched if p.effect == "allow"), None)
        if allow:
            return Check(True, f"policy {allow.policy_id} allow")
        return Check(False, "không có policy allow (mặc định deny)")
