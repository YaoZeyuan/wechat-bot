"""发送前的纯文本处理，不依赖设备。"""
from __future__ import annotations

import unicodedata


def normalize_text(text: str) -> str:
    """规范化即将进入微信输入框的文本。

    CRLF / CR 统一为 LF；移除 NUL、BEL、DEL 等控制字符，保留换行与制表符。
    emoji、四字节汉字、零宽字符不受影响。
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(
        ch for ch in text
        if ch in ("\n", "\t") or unicodedata.category(ch) != "Cc"
    )


def text_probe(text: str) -> str:
    """取首个非空行的前 8 个字符，用于发送后回查聊天记录。"""
    for line in text.splitlines():
        if line.strip():
            return line.strip()[:8]
    return ""
