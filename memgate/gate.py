"""Interface mà harness dùng. Đổi cấu hình phòng thủ = đổi object Gate."""
from __future__ import annotations

from typing import Any, Callable, Protocol

from memgate.labels import Label
from memgate.models import Decision, Message, Source, Version, new_id


class Gate(Protocol):
    def observe(self, agent: str, session: str, source: Source) -> Label: ...

    def write(self, agent: str, session: str, type: str, scope: str, content: str) -> Version: ...

    def retrieve(
        self, agent: str, session: str, query: str, k: int = 5, min_label: Label = Label.U
    ) -> list[Version]: ...

    def send_message(self, sender: str, receiver: str, session: str, content: str) -> Message: ...

    def share(self, agent: str, session: str, item_id: str, to_scope: str) -> Any: ...

    def ingest_trusted(self, content: str, scope: str = "global") -> Version: ...

    def consolidate(self, scope: str, mode: str, summarize: Callable[[list[str]], str],
                    rederiver: Any = None) -> Any: ...

    def authorize(self, agent: str, session: str, tool: str, args: dict[str, Any]) -> Decision: ...

    def context_label(self, agent: str, session: str) -> Label: ...


class NullGate:
    """Không phòng thủ: lưu và trả mọi thứ, luôn allow.

    Message mang nhãn T để mô phỏng framework relay kết quả agent con dưới vai user (K1).
    """

    def __init__(self) -> None:
        self._versions: list[Version] = []

    def observe(self, agent: str, session: str, source: Source) -> Label:
        return Label.T

    def write(self, agent: str, session: str, type: str, scope: str, content: str) -> Version:
        v = Version(item_id=new_id("item"), content=content, label=Label.T, created_by=agent)
        self._versions.append(v)
        return v

    def retrieve(
        self, agent: str, session: str, query: str, k: int = 5, min_label: Label = Label.U
    ) -> list[Version]:
        # Xếp hạng thô theo số từ trùng; đủ cho baseline không phòng thủ.
        q = set(query.lower().split())
        scored = sorted(self._versions, key=lambda v: -len(q & set(v.content.lower().split())))
        return scored[:k]

    def share(self, agent: str, session: str, item_id: str, to_scope: str) -> None:
        return None  # không phòng thủ: không kiểm quyền, không có scope

    def ingest_trusted(self, content: str, scope: str = "global") -> Version:
        return self.write("system", "-", "long_term", scope, content)

    def consolidate(self, scope: str, mode: str, summarize: Callable[[list[str]], str],
                    rederiver: Any = None) -> Version | None:
        """Không phòng thủ: gộp mọi mục thành một bản tóm tắt nhãn T, nội dung bẩn được "rửa" (K3)."""
        if not self._versions:
            return None
        v = Version(item_id=new_id("item"), content=summarize([x.content for x in self._versions]),
                    label=Label.T, created_by="consolidator", reason="consolidate")
        self._versions = [v]
        return v

    def send_message(self, sender: str, receiver: str, session: str, content: str) -> Message:
        return Message(sender=sender, receiver=receiver, session=session, content=content, label=Label.T)

    def authorize(self, agent: str, session: str, tool: str, args: dict[str, Any]) -> Decision:
        return Decision(decision="allow", reason="no defense")

    def context_label(self, agent: str, session: str) -> Label:
        return Label.T
