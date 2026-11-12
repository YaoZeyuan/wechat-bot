"""Local handlers for matching rules."""

import webbrowser
from collections.abc import Callable

from .plugin import ai_chat
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
    ai_chat.PLUGIN_ID: ai_chat.METADATA,
    self_intro.PLUGIN_ID: self_intro.METADATA,
    "open_baidu": {"name": "打开百度", "description": "收到配置的关键词后，在电脑默认浏览器打开百度", "default_enabled": False, "version": "1.0.0", "capability": "浏览器"},
    "daily_activity": {"name": "活跃日榜", "description": "统计北京时间当天本群发言数，回复前十名", "default_enabled": False, "version": "1.0.0", "capability": "手机发送"},
    "total_activity": {"name": "活跃总榜", "description": "按本地历史累计发言数从高到低排列，最多展示 20 人", "default_enabled": False, "version": "1.0.0", "capability": "手机发送"},
    "total_lurkers": {"name": "潜水总榜", "description": "仅统计当前群成员，累计发言数从低到高排列，含零发言，最多展示 20 人", "default_enabled": False, "version": "1.0.0", "capability": "手机发送"},
}
