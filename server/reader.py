"""Desktop message-source setup endpoints."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, SecretStr

from .ciphertalk import CipherTalk, SourceError
from .sqlcipher_reader import SqlCipherReader
from .automation import Automation, Rule
from .plugins import HANDLERS
from .polling import incoming_text, message_id, poll_group
from .reply import MessageEvent, SendTask

router = APIRouter(prefix="/api/reader", tags=["desktop reader"])
log = logging.getLogger("uvicorn.error")
automation = Automation(Path(__file__).resolve().parent.parent / "automation.db")
reader_status = {"enabled": False, "running": False, "error": None, "last_success_at": None}


def poll_once():
    groups = {rule.group_id for rule in automation.rules() if rule.enabled}
    if not groups:
        return False
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
    return bool(reader_status["enabled"])


@asynccontextmanager
async def reader_lifespan(app):
    await asyncio.to_thread(automation.recover_interrupted)
    enabled = await asyncio.to_thread(automation.monitoring_enabled, os.getenv("WECHAT_BOT_POLL") == "1")
    reader_status.update(enabled=enabled, running=False, error=None, last_success_at=None)
    stop = asyncio.Event()

    async def worker():
        while not stop.is_set():
            if reader_status["enabled"]:
                reader_status["running"] = True
                try:
                    if await asyncio.to_thread(poll_once):
                        reader_status["last_success_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
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
    return {**reader_status, "mock_enabled": os.getenv("WECHAT_BOT_MOCK") == "1"}


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


class MockMessage(BaseModel):
    group_id: str = Field(min_length=1, max_length=256)
    messageId: str = Field(min_length=1, max_length=256)
    timestampMs: int = Field(gt=0)
    direction: Literal["in", "out"] = "in"
    kind: Literal["text", "other"] = "text"
    sender: dict[str, str]
    text: str = Field(max_length=4000)


@router.post("/mock/messages")
def dispatch_mock(message: MockMessage) -> dict:
    """Run a fixture through the normal rule engine without accessing WeChat."""
    if os.getenv("WECHAT_BOT_MOCK") != "1":
        raise HTTPException(404)
    row = message.model_dump()
    row["messageId"] = "mock:" + message_id(row)
    try:
        event = incoming_text(message.group_id, row, row["messageId"])
    except SourceError as exc:
        raise HTTPException(422, str(exc)) from None
    return {"status": automation.dispatch(event) if event else "ignored"}


@router.get("/deliveries")
def deliveries() -> dict:
    return {"items": automation.deliveries()}


class PendingTask(BaseModel):
    delivery_id: int
    task: SendTask


@router.get("/tasks", response_model=list[PendingTask])
def pending_tasks() -> list[PendingTask]:
    """List pending actions for the phone executor without claiming them."""
    with automation.database() as db:
        rows = db.execute(
            "SELECT id, task FROM deliveries WHERE status='pending' AND task IS NOT NULL "
            "ORDER BY id LIMIT 100"
        ).fetchall()
    return [
        PendingTask(delivery_id=row["id"], task=SendTask.model_validate_json(row["task"]))
        for row in rows
    ]


@router.post("/tasks/{delivery_id}/claim", response_model=PendingTask)
def claim_task(delivery_id: int) -> PendingTask:
    with automation.database() as db:
        row = db.execute(
            "UPDATE deliveries SET status='sending' WHERE id=? AND status='pending' "
            "AND task IS NOT NULL RETURNING task", (delivery_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(409, "任务已被领取或不可领取")
        task = SendTask.model_validate_json(row["task"])
    log.info("Phone task claimed id=%s", delivery_id)
    return PendingTask(delivery_id=delivery_id, task=task)


class TaskResult(BaseModel):
    status: Literal["sent", "send_failed"]


@router.put("/tasks/{delivery_id}/result")
def finish_task(delivery_id: int, result: TaskResult) -> TaskResult:
    with automation.database() as db:
        changed = db.execute(
            "UPDATE deliveries SET status=? WHERE id=? AND status IN ('sending', ?)",
            (result.status, delivery_id, result.status),
        ).rowcount
        if not changed:
            raise HTTPException(409, "任务未领取或已有不同执行结果")
    log.info("Phone task result id=%s status=%s", delivery_id, result.status)
    return result


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
