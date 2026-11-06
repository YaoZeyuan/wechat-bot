"""Desktop message-source setup endpoints."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, SecretStr

from .ciphertalk import CipherTalk, SourceError
from .sqlcipher_reader import SqlCipherReader
from .automation import Automation, Rule
from .plugins import HANDLERS
from .polling import poll_group
from .reply import MessageEvent

router = APIRouter(prefix="/api/reader", tags=["desktop reader"])
log = logging.getLogger("uvicorn.error")
automation = Automation(Path(__file__).resolve().parent.parent / "automation.db")
reader_status = {"enabled": False, "running": False, "error": None}


def poll_once():
    groups = {rule.group_id for rule in automation.rules() if rule.enabled}
    if not groups:
        return
    source = SqlCipherReader.discover()
    errors = []
    for group_id in sorted(groups):
        if not reader_status["enabled"]:
            break
        try:
            poll_group(source, automation, group_id)
        except SourceError as exc:
            errors.append(str(exc))
            log.warning("Desktop polling failed for group=%s: %s", group_id, exc)
    if errors:
        raise SourceError(errors[0])


@asynccontextmanager
async def reader_lifespan(app):
    await asyncio.to_thread(automation.recover_interrupted)
    enabled = await asyncio.to_thread(automation.monitoring_enabled, os.getenv("WECHAT_BOT_POLL") == "1")
    reader_status.update(enabled=enabled, running=False, error=None)
    stop = asyncio.Event()

    async def worker():
        while not stop.is_set():
            if reader_status["enabled"]:
                reader_status["running"] = True
                try:
                    await asyncio.to_thread(poll_once)
                    reader_status["error"] = None
                except Exception as exc:
                    error = str(exc) if isinstance(exc, SourceError) else type(exc).__name__
                    if reader_status["error"] != error:
                        log.warning("Desktop polling unavailable: %s", error)
                    reader_status["error"] = error
                finally:
                    reader_status["running"] = False
            try:
                await asyncio.wait_for(stop.wait(), timeout=5)
            except TimeoutError:
                pass

    task = asyncio.create_task(worker())
    try:
        yield
    finally:
        stop.set()
        await task


@router.get("/status")
def status() -> dict:
    return dict(reader_status)


class MonitoringSettings(BaseModel):
    enabled: bool


@router.put("/status")
async def set_status(settings: MonitoringSettings) -> dict:
    if settings.enabled and not any(rule.enabled for rule in await asyncio.to_thread(automation.rules)):
        raise HTTPException(409, "请先启用至少一条关键词规则")
    await asyncio.to_thread(automation.set_monitoring, settings.enabled)
    reader_status.update(enabled=settings.enabled, error=None)
    log.info("Desktop polling enabled=%s", settings.enabled)
    return status()


@router.get("/rules")
def rules() -> dict:
    return {"items": automation.rules(), "handlers": [*HANDLERS, "webhook"]}


@router.put("/rules")
def save_rule(rule: Rule) -> Rule:
    automation.save_rule(rule)
    return rule


class RulePreview(BaseModel):
    rule: Rule
    text: str = Field(max_length=4000)


@router.post("/rules/preview")
def preview_rule(request: RulePreview) -> dict:
    rule = request.rule
    matched = rule.enabled and request.text.strip() == rule.keyword
    reply = None
    if matched and rule.handler != "webhook":
        event = MessageEvent(
            group_id=rule.group_id, message_id="preview", sender_id="preview",
            timestamp_ms=1, text=request.text,
        )
        reply = HANDLERS[rule.handler](event)
    return {"matched": matched, "handler": rule.handler, "reply": reply}


@router.get("/deliveries")
def deliveries() -> dict:
    return {"items": automation.deliveries()}


@router.post("/deliveries/{delivery_id}/retry")
def retry_delivery(delivery_id: int) -> dict:
    result = automation.retry(delivery_id)
    if result is None:
        raise HTTPException(409, "此投递没有可重试的失败记录")
    return {"status": result}


class SourceSettings(BaseModel):
    port: int | None = Field(default=None, ge=1, le=65535)
    token: SecretStr = SecretStr("")
    query: str = Field(default="", max_length=100)
    session_id: str | None = Field(default=None, max_length=256)
    database_path: str | None = Field(default=None, max_length=1024)
    account: str = Field(default="", max_length=256)
    database_key: SecretStr = SecretStr("")


@router.post("/probe")
def probe(settings: SourceSettings) -> dict:
    """Check a source without exposing its token or returning chat content."""
    try:
        if settings.database_path:
            if not settings.account:
                raise HTTPException(422, "请填写微信账号 ID")
            source = SqlCipherReader(
                Path(settings.database_path).expanduser().resolve(),
                settings.database_key.get_secret_value(),
                settings.account,
            )
        elif settings.port is None:
            source = SqlCipherReader.discover()
        else:
            source = CipherTalk(settings.port, settings.token.get_secret_value())
        result = source.sessions(q=settings.query, limit=50)
        groups = [
            {"id": item["sessionId"], "name": item["displayName"]}
            for item in result["items"]
            if item["kind"] == "group"
        ]
        message_readable = None
        has_messages = None
        if settings.session_id:
            if not settings.session_id.endswith("@chatroom"):
                raise HTTPException(422, "请选择微信群")
            messages = source.messages(settings.session_id, limit=1, includeRaw=False, includeMediaPaths=False)
            has_messages = bool(messages.get("items"))
            message_readable = True
    except SourceError as exc:
        log.warning("Desktop reader probe failed: %s", exc)
        raise HTTPException(502, str(exc)) from None
    except (KeyError, TypeError):
        log.warning("Desktop reader received an unexpected CipherTalk response")
        raise HTTPException(502, "密语代理返回了不兼容的会话数据") from None
    log.info("Desktop reader probe succeeded; groups=%d, message_readable=%s", len(groups), message_readable)
    return {
        "groups": groups, "has_more": bool(result.get("hasMore")),
        "message_readable": message_readable, "has_messages": has_messages,
    }
