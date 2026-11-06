"""Desktop message-source setup endpoints."""

import logging
import os
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, SecretStr

from .ciphertalk import CipherTalk, SourceError
from .ciphertalk_cli import CipherTalkCLI
from .automation import Automation, Rule
from .plugins import HANDLERS
from .polling import poll_group

router = APIRouter(prefix="/api/reader", tags=["desktop reader"])
log = logging.getLogger("uvicorn.error")
automation = Automation(Path(__file__).resolve().parent.parent / "automation.db")
reader_status = {"enabled": False, "error": None}


def poll_once():
    groups = {rule.group_id for rule in automation.rules() if rule.enabled}
    if not groups:
        return
    source = CipherTalkCLI()
    source.check_connection()
    errors = []
    for group_id in sorted(groups):
        try:
            poll_group(source, automation, group_id)
        except SourceError as exc:
            errors.append(str(exc))
            log.warning("Desktop polling failed for group=%s: %s", group_id, exc)
    if errors:
        raise SourceError(errors[0])


@asynccontextmanager
async def reader_lifespan(app):
    enabled = os.getenv("WECHAT_BOT_POLL") == "1"
    reader_status.update(enabled=enabled, error=None)
    stop = asyncio.Event()

    async def worker():
        while not stop.is_set():
            try:
                await asyncio.to_thread(poll_once)
                reader_status["error"] = None
            except Exception as exc:
                error = str(exc) if isinstance(exc, SourceError) else type(exc).__name__
                if reader_status["error"] != error:
                    log.warning("Desktop polling unavailable: %s", error)
                reader_status["error"] = error
            try:
                await asyncio.wait_for(stop.wait(), timeout=5)
            except TimeoutError:
                pass

    task = asyncio.create_task(worker()) if enabled else None
    try:
        yield
    finally:
        stop.set()
        if task:
            await task


@router.get("/status")
def status() -> dict:
    return dict(reader_status)


@router.get("/rules")
def rules() -> dict:
    return {"items": automation.rules(), "handlers": [*HANDLERS, "webhook"]}


@router.put("/rules")
def save_rule(rule: Rule) -> Rule:
    automation.save_rule(rule)
    return rule


@router.get("/deliveries")
def deliveries() -> dict:
    return {"items": automation.deliveries()}


class SourceSettings(BaseModel):
    port: int | None = Field(default=None, ge=1, le=65535)
    token: SecretStr = SecretStr("")
    query: str = Field(default="", max_length=100)
    session_id: str | None = Field(default=None, max_length=256)


@router.post("/probe")
def probe(settings: SourceSettings) -> dict:
    """Check a source without exposing its token or returning chat content."""
    try:
        if settings.port is None:
            source = CipherTalkCLI()
            source.check_connection()
        else:
            source = CipherTalk(settings.port, settings.token.get_secret_value())
        result = source.sessions(q=settings.query, limit=50)
        groups = [
            {"id": item["sessionId"], "name": item["displayName"]}
            for item in result["items"]
            if item["kind"] == "group"
        ]
        message_readable = None
        if settings.session_id:
            if not settings.session_id.endswith("@chatroom"):
                raise HTTPException(422, "请选择微信群")
            source.messages(settings.session_id, limit=1, includeRaw=False, includeMediaPaths=False)
            message_readable = True
    except SourceError as exc:
        log.warning("Desktop reader probe failed: %s", exc)
        raise HTTPException(502, str(exc)) from None
    except (KeyError, TypeError):
        log.warning("Desktop reader received an unexpected CipherTalk response")
        raise HTTPException(502, "密语代理返回了不兼容的会话数据") from None
    log.info("Desktop reader probe succeeded; groups=%d, message_readable=%s", len(groups), message_readable)
    return {"groups": groups, "has_more": bool(result.get("hasMore")), "message_readable": message_readable}
