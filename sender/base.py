"""发送通道的公共契约。

新增发送实现（PC Hook、常驻无障碍 App 等）时，
只需提供一个 send(target, text) -> bool 的类，并在 server 里选择实现。
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


class SendError(RuntimeError):
    """发送链路中找不到控件或校验失败。"""


@runtime_checkable
class Sender(Protocol):
    def send(self, target: str, text: str) -> bool: ...
