"""Nghiệm thu việc 1: gọi cùng một prompt hai lần, lần hai phải trúng cache.

python -m harness.smoke                 # dùng configs/default.yaml
python -m harness.smoke --model <model>
"""
from __future__ import annotations

import argparse
import sys

import yaml

from harness.llm import LLM

PROMPT = [{"role": "user", "content": "Trả lời đúng một từ: OK"}]


def smoke(llm: LLM, model: str, params: dict) -> bool:
    first = llm.chat(model, PROMPT, **params)
    hits_before = llm.stats["hits"]
    second = llm.chat(model, PROMPT, **params)
    ok = second == first and llm.stats["hits"] == hits_before + 1
    print(f"model={model} provider={llm.provider} cache={llm.mode}")
    print(f"lần 1: {first!r}")
    print(f"lần 2: {second!r} ({'trúng cache' if ok else 'KHÔNG trúng cache'})")
    print(f"stats: {llm.stats}")
    return ok


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="configs/default.yaml")
    p.add_argument("--model")
    args = p.parse_args()
    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)["llm"]
    llm = LLM(provider=cfg["provider"], cache_path=cfg["cache_path"], rpm=cfg.get("rpm"))
    params = {"temperature": cfg["temperature"]} if cfg.get("temperature") is not None else {}
    sys.exit(0 if smoke(llm, args.model or cfg["model"], params) else 1)


if __name__ == "__main__":
    main()
