"""Công tắc biện pháp giảm thiểu cho ablation (việc 7, FR9). Đọc từ `memgate.mitigations` trong config.

| Cờ                 | Mặc định | Tắt/bật thì                                                     |
|--------------------|----------|-----------------------------------------------------------------|
| action_gate        | bật      | tắt: authorize luôn allow (vẫn ghi audit)                       |
| boundary_labeling  | bật      | tắt: message giữa agent mang nhãn T (cấu hình TMA-NM từng agent)|
| expiration         | tắt      | bật: mục mới có expires_at theo retention, retrieve bỏ mục quá hạn|
| validation         | tắt      | việc 11b, chưa cài                                              |
| quarantine         | tắt      | việc 11b, chưa cài                                              |
| human_review       | tắt      | việc 8, chưa cài                                                |

`boundary_labeling` chỉ áp cho message: nhãn khi ghi memory vẫn là min(A, L) của người ghi.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields

NOT_YET = {"validation": "việc 11b", "quarantine": "việc 11b", "human_review": "việc 8"}


@dataclass(frozen=True)
class Mitigations:
    action_gate: bool = True
    boundary_labeling: bool = True
    validation: bool = False
    quarantine: bool = False
    expiration: bool = False
    human_review: bool = False

    @classmethod
    def from_config(cls, cfg: dict | None) -> "Mitigations":
        cfg = dict(cfg or {})
        names = {f.name for f in fields(cls)}
        unknown = set(cfg) - names
        if unknown:
            raise ValueError(f"mitigations không biết: {sorted(unknown)}")
        for k, v in cfg.items():
            if not isinstance(v, bool):
                raise ValueError(f"mitigations.{k} phải là true/false, nhận {v!r}")
        m = cls(**cfg)
        on = [k for k, task in NOT_YET.items() if getattr(m, k)]
        if on:
            raise NotImplementedError(f"chưa cài: {', '.join(f'{k} ({NOT_YET[k]})' for k in on)}")
        return m

    def off_by_default(self) -> dict[str, bool]:
        """Các cờ khác mặc định (dùng đặt tên trace)."""
        base = asdict(Mitigations())
        return {k: v for k, v in asdict(self).items() if base[k] != v}


def parse_overrides(items: list[str] | None) -> dict[str, bool]:
    """["boundary_labeling=off", "expiration=on"] -> {"boundary_labeling": False, "expiration": True}."""
    out = {}
    for it in items or []:
        k, sep, v = it.partition("=")
        val = {"on": True, "true": True, "1": True, "off": False, "false": False, "0": False}.get(v.strip().lower())
        if not sep or val is None:
            raise ValueError(f"cần dạng ten=on|off, nhận {it!r}")
        out[k.strip()] = val
    return out
