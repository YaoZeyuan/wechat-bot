"""Local handlers for matching rules."""

import webbrowser
from collections.abc import Callable

from .plugin import ai_chat
from .plugin import chat_summary
from .plugin import self_intro
from .reply import MessageEvent, Reply

class PluginActionError(RuntimeError):
    pass


def open_baidu(event: MessageEvent) -> None:
    if not webbrowser.open("https://www.baidu.com/", new=2):
        raise PluginActionError("无法打开电脑默认浏览器")


HANDLERS: dict[str, Callable[[MessageEvent], Reply | None]] = {
    "open_baidu": open_baidu,
}

# Stable plugin IDs are shared by rules, the catalog and group overrides.
PLUGINS = {
    ai_chat.PLUGIN_ID: {"group": "AI", "order": 0, **ai_chat.METADATA},
    chat_summary.PLUGIN_ID: {"group": "AI", "order": 0, **chat_summary.METADATA},
    self_intro.PLUGIN_ID: {"group": "群聊工具", "order": 0, **self_intro.METADATA},
    "activity_stats": {"name": "活跃统计", "description": "支持活跃日榜、活跃总榜和潜水总榜", "default_enabled": False, "version": "1.0.0", "capability": "手机发送", "group": "群聊工具", "order": 0},
}
