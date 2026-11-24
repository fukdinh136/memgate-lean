"""LLM giả lập: mỗi agent có một danh sách câu trả lời viết sẵn.

Phần tử là str, hoặc hàm nhận messages và trả str (vd để chép lại kết quả tool).
"""
import json
import re

from harness.llm import LLM

_NAME = re.compile(r"Tên của bạn: (\w+)")


def tool(name, **args):
    return json.dumps({"tool": name, "args": args}, ensure_ascii=False)


def final(text):
    return json.dumps({"final": text}, ensure_ascii=False)


def echo_last_result(messages):
    """Báo lại nguyên văn kết quả tool gần nhất, như một agent tóm tắt trung thực."""
    return final(messages[-1]["content"].removeprefix("Kết quả: "))


def scripted_llm(scripts):
    queues = {k: list(v) for k, v in scripts.items()}

    def call(model, messages, params):
        agent = _NAME.search(messages[0]["content"]).group(1)
        q = queues.get(agent)
        if not q:
            raise AssertionError(f"{agent} hết câu trả lời viết sẵn")
        item = q.pop(0)
        return {"text": item(messages) if callable(item) else item, "usage": {}}

    return LLM(cache_path=":memory:", mode="off", call_fn=call), queues
