"""FastAPI server：把第一期的发送能力包成 REST 接口，供 Vue 前端调用。

启动（在项目根目录）：
    ./.venv/Scripts/python.exe -m uvicorn server.main:app --host 127.0.0.1 --port 8000

只做「配置 / 发送 / 日志 / 设备」四件事，统计与指令识别留到下一期。
"""
from __future__ import annotations

from contextlib import asynccontextmanager
import json
import re
import shutil
import sqlite3
import subprocess
import sys
from time import perf_counter, sleep
from uuid import uuid4
from threading import Lock
import structlog
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from ipaddress import ip_address
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, SecretStr, field_validator

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

from server.sender_jobs import recover_jobs, router as sender_jobs_router


@asynccontextmanager
async def lifespan(app):
    recover_jobs()
    async with reader_lifespan(app):
        yield


app = FastAPI(title="wechat-bot", version="0.1.0", lifespan=lifespan)
app.include_router(sender_jobs_router)
app.include_router(reader_router)
request_log = structlog.get_logger("wechat_bot.http")
sender_log = structlog.get_logger("wechat_bot.sender")
sender_console_lock = Lock()
adb_status_lock = Lock()
sender_steps: list[str] = []


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
            metadata = dict(part.split(":", 1) for part in parts[2:] if ":" in part)
            items.append({"serial": parts[0], "state": parts[1], "model": metadata.get("model", "")})
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
    with adb_status_lock:
        return {"connected": sum(item["state"] == "device" for item in _devices()) == 1}


class WirelessEndpoint(BaseModel):
    address: str = Field(max_length=64)

    @field_validator("address")
    @classmethod
    def validate_address(cls, value: str) -> str:
        host, _, port = value.strip().rpartition(":")
        try:
            ip = ip_address(host.removeprefix("[").removesuffix("]"))
            if not re.fullmatch(r"[0-9]{1,5}", port) or not 1 <= int(port) <= 65535:
                raise ValueError
        except ValueError:
            raise ValueError("请输入手机显示的 IP 地址和端口") from None
        return f"[{ip}]:{int(port)}" if ip.version == 6 else f"{ip}:{int(port)}"


class WirelessPair(WirelessEndpoint):
    code: SecretStr


def wireless_device_status(address: str, service: str = "") -> dict:
    devices = [item for item in _devices() if item["state"] == "device"]
    device = next((item for item in devices if item["serial"] == address
                   or (service and item["serial"] == f"{service}._adb-tls-connect._tcp")), None)
    return {"device": device, "device_count": len(devices)}


def discover_wireless_device(adb: str, pair_address: str) -> dict:
    host = pair_address.rpartition(":")[0]
    for attempt in range(4):
        if attempt:
            sleep(0.5)
        try:
            result = subprocess.run(
                [adb, "mdns", "services"], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=2,
            )
            matches = []
            for line in result.stdout.splitlines():
                parts = line.split()
                if (len(parts) == 3 and parts[1].rstrip(".") == "_adb-tls-connect._tcp"
                        and parts[2].rpartition(":")[0] == host):
                    matches.append((parts[0], parts[2]))
            if len(matches) != 1:
                continue
            service, address = matches[0]
            status = wireless_device_status(address, service)
            if status["device"]:
                return status
            subprocess.run([adb, "connect", address], capture_output=True, timeout=5)
            status = wireless_device_status(address, service)
            if status["device"]:
                return status
        except (OSError, subprocess.TimeoutExpired):
            continue
    return {"device": None, "device_count": 0}


def wireless_adb(command: str, address: str, code: str | None = None) -> dict:
    if not sender_console_lock.acquire(blocking=False):
        raise HTTPException(409, "正在执行手机操作，请稍后重试")
    try:
        with adb_status_lock:
            setup_adb(load_config())
            adb = shutil.which("adb")
            if not adb:
                raise HTTPException(503, "找不到 ADB，请检查本机 ADB 路径配置")
            try:
                proc = subprocess.run(
                    [adb, command, address], input=code + "\n" if code else "",
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
                )
            except subprocess.TimeoutExpired:
                sender_log.warning("adb_wireless_timeout", msg="无线调试操作超时", command=command)
                raise HTTPException(504, "ADB 操作超时，请确认手机与电脑在同一网络，且端口仍有效") from None
            except OSError:
                raise HTTPException(503, "无法启动 ADB，请检查 Server 的 ADB 配置") from None
            output = proc.stdout.lower()
            if command == "pair":
                ok = "successfully paired" in output
                failure = "配对失败，请在手机重新打开配对弹窗，核对配对端口和六位配对码"
            else:
                ok = any(line.startswith(("connected to ", "already connected to ")) for line in output.splitlines())
                failure = "连接失败，请核对无线调试主页面的连接端口，并确认手机已配对"
            ok = proc.returncode == 0 and ok
            sender_log.info("adb_wireless_result", msg="无线调试操作完成", command=command, ok=ok)
            if not ok:
                raise HTTPException(502, failure)
            if command == "pair":
                return discover_wireless_device(adb, address)
            return wireless_device_status(address)
    finally:
        sender_console_lock.release()


@app.post("/api/sender/adb/pair")
def pair_wireless(req: WirelessPair):
    code = req.code.get_secret_value()
    if not re.fullmatch(r"[0-9]{6}", code):
        raise HTTPException(422, "配对码应为六位数字")
    return wireless_adb("pair", req.address, code)


@app.post("/api/sender/adb/connect")
def connect_wireless(req: WirelessEndpoint):
    return wireless_adb("connect", req.address)


@app.post("/api/sender/adb/restart")
def restart_adb() -> dict[str, bool]:
    if not sender_console_lock.acquire(blocking=False):
        raise HTTPException(409, "正在执行手机操作，请稍后重启 ADB")
    try:
        with adb_status_lock:
            setup_adb(load_config())
            adb = shutil.which("adb")
            if not adb:
                raise HTTPException(503, "找不到 ADB，请检查本机 ADB 路径配置")
            sender_log.info("adb_restart_started", msg="正在重启 ADB 服务")
            for command in ("kill-server", "start-server"):
                try:
                    subprocess.run(
                        [adb, command], capture_output=True, check=True,
                        text=True, encoding="utf-8", errors="replace", timeout=10,
                    )
                except (OSError, subprocess.SubprocessError) as exc:
                    sender_log.warning("adb_restart_failed", msg="ADB 重启失败",
                                       command=command, error=str(exc), stderr=getattr(exc, "stderr", None))
                    raise HTTPException(503, f"ADB 重启失败（{command}），请查看 Server 日志") from exc
            connected = sum(item["state"] == "device" for item in _devices()) == 1
            sender_log.info("adb_restart_finished", msg="ADB 服务已重启", connected=connected)
            return {"connected": connected}
    finally:
        sender_console_lock.release()


@app.post("/api/sender/send")
def send_from_console(req: SendRequest) -> dict[str, Any]:
    if not sender_console_lock.acquire(blocking=False):
        raise HTTPException(409, "已有发送操作正在执行")
    sender_steps.clear()
    try:
        sender_steps.append("检查手机连接")
        return run_console_send(req)
    except HTTPException as exc:
        sender_steps.append(str(exc.detail))
        raise
    finally:
        sender_console_lock.release()


@app.get("/api/sender/progress")
def sender_progress() -> dict:
    return {"running": sender_console_lock.locked(), "steps": list(sender_steps)}


def run_console_send(req: SendRequest) -> dict[str, Any]:
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
    if explicit_mode:
        sender.on_progress = sender_steps.append
    try:
        if explicit_mode:
            sender_steps.append("正在连接手机")
        sender.connect()
        ok = sender.send(target, req.text, strict_target=True) if explicit_mode else sender.send(target, req.text)
    except SendError as exc:
        sender_log.warning("sender_send_failed", error_type=type(exc).__name__)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        sender_log.error("sender_send_failed", error_type=type(exc).__name__)
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc
    sender_log.info("sender_send_finished", ok=ok, dry_run=bool(sender.dry_run), chars=len(req.text))
    if explicit_mode:
        sender_steps.append("操作完成" if ok else "未能确认发送结果，请先在手机核对")
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
