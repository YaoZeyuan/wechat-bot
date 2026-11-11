"""Local handlers and the HTTP callback transport for matching rules."""

import structlog
import webbrowser
from collections.abc import Callable
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from pydantic import HttpUrl

from .ciphertalk import NoRedirect
from .plugin import ai_chat
from .reply import MessageEvent, Reply, Text

log = structlog.get_logger("wechat_bot.plugins")
MAX_REPLY_BYTES = 256 * 1024


class WebhookError(RuntimeError):
    """A diagnostic safe to show in the console."""


class PluginActionError(RuntimeError):
    pass


def fixed_reply(event: MessageEvent) -> Reply:
    return Reply(content=Text(text="测试成功"))


def open_baidu(event: MessageEvent) -> None:
    if not webbrowser.open("https://www.baidu.com/", new=2):
        raise PluginActionError("无法打开电脑默认浏览器")


HANDLERS: dict[str, Callable[[MessageEvent], Reply | None]] = {
    "fixed_reply": fixed_reply,
    "open_baidu": open_baidu,
}

# Stable plugin IDs are shared by rules, the catalog and group overrides.
PLUGINS = {
    ai_chat.PLUGIN_ID: ai_chat.METADATA,
    "open_baidu": {"name": "打开百度", "description": "收到配置的关键词后，在电脑默认浏览器打开百度", "default_enabled": False, "version": "1.0.0", "capability": "浏览器"},
    "fixed_reply": {"name": "测试", "description": "文本与配置关键词完全一致时，发送指定回复", "default_enabled": True, "version": "1.0.0", "capability": "手机发送"},
    "daily_activity": {"name": "活跃日榜", "description": "统计北京时间当天本群发言数，回复前十名", "default_enabled": False, "version": "1.0.0", "capability": "手机发送"},
    "total_activity": {"name": "活跃总榜", "description": "按本地历史累计发言数从高到低排列，最多展示 20 人", "default_enabled": False, "version": "1.0.0", "capability": "手机发送"},
    "total_lurkers": {"name": "潜水总榜", "description": "仅统计当前群成员，累计发言数从低到高排列，含零发言，最多展示 20 人", "default_enabled": False, "version": "1.0.0", "capability": "手机发送"},
    "webhook": {"name": "HTTP 回调", "description": "把命中的消息交给独立 HTTP 服务", "default_enabled": False, "version": "1.0.0", "capability": "HTTP"},
}


def call_webhook(url: HttpUrl, event: MessageEvent, delivery_id: str) -> Reply | None:
    """POST a matched event; 204 acknowledges it without creating a reply."""
    request = Request(
        str(url),
        data=event.model_dump_json().encode("utf-8"),
        headers={"Content-Type": "application/json", "Idempotency-Key": delivery_id},
        method="POST",
    )
    opener = build_opener(ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=10) as response:
            if response.status == 204:
                return None
            data = response.read(MAX_REPLY_BYTES + 1)
        if len(data) > MAX_REPLY_BYTES:
            raise WebhookError("回调响应超过 256 KiB")
        reply = Reply.model_validate_json(data)
    except HTTPError as exc:
        exc.close()
        raise WebhookError(f"回调服务返回 HTTP {exc.code}") from None
    except (URLError, OSError):
        raise WebhookError("无法连接回调服务或读取响应超时") from None
    except ValueError:
        raise WebhookError("回调响应不符合回复协议") from None
    except Exception as exc:
        log.warning("webhook_delivery_failed", delivery_id=delivery_id, error=type(exc).__name__)
        raise
    log.info("webhook_delivery_succeeded", delivery_id=delivery_id)
    return reply
