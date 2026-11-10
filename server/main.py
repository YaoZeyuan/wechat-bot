"""FastAPI server：把第一期的发送能力包成 REST 接口，供 Vue 前端调用。

启动（在项目根目录）：
    ./.venv/Scripts/python.exe -m uvicorn server.main:app --host 127.0.0.1 --port 8000

只做「配置 / 发送 / 日志 / 设备」四件事，统计与指令识别留到下一期。
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
from time import perf_counter
from uuid import uuid4
import structlog
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sender import (  # noqa: E402
    ROOT as APP_ROOT,
    SendError,
    create_sender,
    load_config,
    normalize_text,
    setup_adb,
    text_probe,
)

CONFIG_PATH = APP_ROOT / "config.json"
EDITABLE_KEYS = (
    "default_target", "serial", "dry_run",
    "step_delay", "wait_timeout", "verify_timeout",
)

from server.reader import reader_lifespan, router as reader_router

app = FastAPI(title="wechat-bot", version="0.1.0", lifespan=reader_lifespan)
app.include_router(reader_router)
request_log = structlog.get_logger("wechat_bot.http")
sender_log = structlog.get_logger("wechat_bot.sender")


@app.middleware("http")
async def log_api_request(request: Request, call_next):
    started = perf_counter()
    request_id = uuid4().hex
    status = 500
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=request_id)
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        route = request.scope.get("route")
        request_log.log(
            40 if status >= 500 else 20,
            "http_request",
            method=request.method,
            route=getattr(route, "path", "<unmatched>"),
            status=status,
            duration_ms=round((perf_counter() - started) * 1000, 1),
        )
        structlog.contextvars.clear_contextvars()
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
    allow_methods=["*"],
    allow_headers=["*"],
)


class SendRequest(BaseModel):
    text: str
    target: str | None = None
    dry_run: bool = False
    serial: str | None = None


class ConfigPatch(BaseModel):
    default_target: str | None = None
    serial: str | None = None
    dry_run: bool | None = None
    step_delay: float | None = None
    wait_timeout: float | None = None
    verify_timeout: float | None = None


def _pkg(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return "未安装"


def _devices() -> list[dict[str, str]]:
    cfg = load_config()
    setup_adb(cfg)
    adb = shutil.which("adb")
    if not adb:
        return []
    try:
        proc = subprocess.run(
            [adb, "devices", "-l"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode:
        return []
    items = []
    for line in proc.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            items.append({"serial": parts[0], "state": parts[1]})
    return items


def _db_path() -> Path:
    return APP_ROOT / load_config().get("database", "bot.db")


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "python": sys.version.split()[0],
        "uiautomator2": _pkg("uiautomator2"),
        "fastapi": _pkg("fastapi"),
        "database": str(_db_path()),
        "database_exists": _db_path().exists(),
    }


@app.get("/api/devices")
def devices() -> dict[str, Any]:
    cfg = load_config()
    setup_adb(cfg)
    adb = shutil.which("adb")
    items = _devices()
    return {
        "adb": adb,
        "devices": items,
        "ready": any(d["state"] == "device" for d in items),
        "hint": "" if adb else "adb 未找到，检查 config.json 的 adb_dir",
    }


@app.get("/api/config")
def get_config() -> dict[str, Any]:
    return load_config()


@app.patch("/api/config")
def patch_config(patch: ConfigPatch) -> dict[str, Any]:
    cfg = load_config()
    changed = patch.model_dump(exclude_none=True)
    for key, value in changed.items():
        if key in EDITABLE_KEYS:
            cfg[key] = value
    try:
        CONFIG_PATH.write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"写 config.json 失败（可能被编辑器占用）：{exc}",
        ) from exc
    return {"ok": True, "changed": changed, "config": cfg}


@app.post("/api/send/preview")
def preview(req: SendRequest) -> dict[str, Any]:
    cfg = load_config()
    target = req.target or cfg.get("default_target", "文件传输助手")
    normalized = normalize_text(req.text)
    max_chars = int(cfg.get("max_chars", 1800))
    warnings = []
    if normalized != req.text:
        warnings.append("文本已规范化：CRLF 转为 LF、控制字符已移除")
    if len(normalized) > max_chars:
        warnings.append(f"{len(normalized)} 字超过上限 {max_chars}，会被拒绝发送")
    if not normalized.strip():
        warnings.append("规范化后只剩空白，会被拒绝发送")
    return {
        "target": target,
        "text": req.text,
        "normalized_text": normalized,
        "chars": len(normalized),
        "original_chars": len(req.text),
        "lines": len(normalized.splitlines()),
        "probe": text_probe(normalized),
        "dry_run": req.dry_run,
        "warnings": warnings,
    }


@app.post("/api/send")
def send(req: SendRequest) -> dict[str, Any]:
    return execute_send(req)


@app.get("/api/sender/status")
def sender_status() -> dict[str, bool]:
    return {"connected": sum(item["state"] == "device" for item in _devices()) == 1}


@app.post("/api/sender/send")
def send_from_console(req: SendRequest) -> dict[str, Any]:
    ready = [item for item in _devices() if item["state"] == "device"]
    if len(ready) != 1:
        raise HTTPException(409, "请连接并授权一台手机")
    if not req.target or not req.target.strip():
        raise HTTPException(422, "请填写联系人或群聊名称")
    return execute_send(req.model_copy(update={"serial": ready[0]["serial"], "target": req.target.strip()}), explicit_mode=True)


def execute_send(req: SendRequest, *, explicit_mode: bool = False) -> dict[str, Any]:
    cfg = load_config()
    target = req.target or cfg.get("default_target", "文件传输助手")
    if not req.text or not req.text.strip():
        raise HTTPException(status_code=400, detail="发送内容为空")
    sender = create_sender(cfg=cfg, serial=req.serial, dry_run=req.dry_run if explicit_mode or req.dry_run else None)
    try:
        sender.connect()
        ok = sender.send(target, req.text, strict_target=True) if explicit_mode else sender.send(target, req.text)
    except SendError as exc:
        sender_log.warning("sender_send_failed", error_type=type(exc).__name__)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        sender_log.error("sender_send_failed", error_type=type(exc).__name__)
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc
    sender_log.info("sender_send_finished", ok=ok, dry_run=bool(sender.dry_run), chars=len(req.text))
    return {
        "ok": ok,
        "target": target,
        "dry_run": bool(sender.dry_run),
        "chars": len(req.text),
    }


@app.get("/api/logs")
def logs(limit: int = 30) -> dict[str, Any]:
    path = _db_path()
    if not path.exists():
        return {"items": []}
    try:
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM send_log ORDER BY id DESC LIMIT ?", (max(1, min(limit, 200)),)
        ).fetchall()
        conn.close()
    except sqlite3.OperationalError:
        return {"items": []}
    return {"items": [dict(row) for row in rows]}


@app.get("/api/stats")
def stats() -> dict[str, Any]:
    path = _db_path()
    today = datetime.now().astimezone().date().isoformat()
    empty = {"today": today, "total": 0, "ok": 0, "dry_run": 0}
    if not path.exists():
        return empty
    try:
        conn = sqlite3.connect(str(path))
        row = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(ok), 0), COALESCE(SUM(dry_run), 0) "
            "FROM send_log WHERE substr(created_at, 1, 10) = ?",
            (today,),
        ).fetchone()
        conn.close()
    except sqlite3.OperationalError:
        return empty
    return {"today": today, "total": row[0], "ok": row[1], "dry_run": row[2]}
