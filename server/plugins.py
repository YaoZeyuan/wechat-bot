"""Local handlers and the HTTP callback transport for matching rules."""

import logging
from collections.abc import Callable
from urllib.request import ProxyHandler, Request, build_opener

from pydantic import HttpUrl

from .ciphertalk import NoRedirect
from .reply import MessageEvent, Reply, Text

log = logging.getLogger("uvicorn.error")
MAX_REPLY_BYTES = 256 * 1024


def fixed_reply(event: MessageEvent) -> Reply:
    return Reply(content=Text(text="测试成功"))


HANDLERS: dict[str, Callable[[MessageEvent], Reply | None]] = {
    "fixed_reply": fixed_reply,
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
            raise ValueError("Webhook response too large")
        reply = Reply.model_validate_json(data)
    except Exception as exc:
        log.warning("Webhook delivery failed id=%s error=%s", delivery_id, type(exc).__name__)
        raise
    log.info("Webhook delivery succeeded id=%s", delivery_id)
    return reply
