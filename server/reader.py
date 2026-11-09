"""Desktop message-source setup endpoints."""

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
import structlog
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from threading import Lock
from tempfile import NamedTemporaryFile

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, SecretStr

from .ciphertalk import CipherTalk, SourceError
from .sqlcipher_reader import SqlCipherReader
from .ciphertalk_cli import CipherTalkCLI
from .automation import Automation, Rule
from .plugins import HANDLERS
from .polling import incoming_text, message_id, poll_group
from .reply import MessageEvent, SendTask

router = APIRouter(prefix="/api/reader", tags=["desktop reader"])
log = structlog.get_logger("wechat_bot.reader")
automation = Automation(Path(__file__).resolve().parent.parent / "automation.db")
reader_status = {"enabled": False, "running": False, "error": None, "last_success_at": None}
key_extraction_lock = Lock()
key_progress = {"stage": "idle", "message": "", "steps": []}
profile_path = Path(__file__).resolve().parent.parent / "local-data" / "reader-profile.json"


def report_key_progress(stage: str, message: str):
    steps = [] if stage == "preparing" else key_progress["steps"]
    key_progress.update(stage=stage, message=message, steps=[*steps, {"stage": stage, "message": message}])
    log.info("wechat_key_stage", stage=stage, message=message)


@router.get("/key/progress")
def get_key_progress() -> dict:
    return {**key_progress, "enabled": os.getenv("WECHAT_BOT_KEY_EXTRACTION") == "1"}


def poll_once():
    groups = {rule.group_id for rule in automation.rules() if rule.enabled}
    if not groups:
        return False
    profile = load_profile()
    source = (
        SqlCipherReader(Path(profile.database_path), profile.database_key.get_secret_value(), profile.account)
        if profile else SqlCipherReader.discover()
    )
    source_id = (
        hashlib.sha256(f"{profile.account}\0{profile.database_path}".encode()).hexdigest()[:24]
        if profile else ""
    )
    errors = []
    for group_id in sorted(groups):
        if not reader_status["enabled"]:
            break
        try:
            poll_group(source, automation, group_id, source_id)
        except SourceError as exc:
            errors.append(str(exc))
            log.warning("desktop_group_poll_failed", group_id=group_id, error=str(exc))
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
                        log.warning("desktop_poll_unavailable", error=error)
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
    log.info("desktop_polling_changed", enabled=settings.enabled)
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
    log.info("phone_task_claimed", delivery_id=delivery_id)
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
    log.info("phone_task_result", delivery_id=delivery_id, status=result.status)
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
    image_xor_key: SecretStr = SecretStr("")
    image_aes_key: SecretStr = SecretStr("")
    save_profile: bool = False


class ReaderProfile(BaseModel):
    database_path: str
    account: str


class StoredReaderProfile(ReaderProfile):
    database_key: SecretStr
    image_xor_key: SecretStr = SecretStr("")
    image_aes_key: SecretStr = SecretStr("")


def load_profile() -> StoredReaderProfile | None:
    try:
        return StoredReaderProfile.model_validate_json(profile_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        log.warning("reader_profile_load_failed")
        raise SourceError("无法读取本机 reader-profile.json") from None


def history_source() -> SqlCipherReader:
    profile = load_profile()
    if profile is None:
        raise SourceError("请先在微信初始化页验证并保存数据库配置")
    return SqlCipherReader(Path(profile.database_path), profile.database_key.get_secret_value(), profile.account)


@router.get("/history/conversations")
def browse_conversations(
    q: str = Query("", max_length=100), kind: Literal["all", "private", "group"] = "all",
    offset: int = Query(0, ge=0, le=100000), limit: int = Query(50, ge=1, le=100),
) -> dict:
    try:
        result = history_source().conversations(q=q, kind=kind, offset=offset, limit=limit)
    except SourceError as exc:
        log.warning("chat_history_read_failed", area="conversations", error=str(exc))
        raise HTTPException(502, str(exc)) from None
    log.info("chat_history_read", area="conversations", count=len(result["items"]))
    return result


@router.get("/history/messages")
def browse_messages(
    session_id: str = Query(min_length=1, max_length=256),
    offset: int = Query(0, ge=0, le=100000), limit: int = Query(50, ge=1, le=100),
) -> dict:
    try:
        result = history_source().history(session_id, offset=offset, limit=limit)
    except SourceError as exc:
        log.warning("chat_history_read_failed", area="messages", error=str(exc))
        raise HTTPException(502, str(exc)) from None
    log.info("chat_history_read", area="messages", count=len(result["items"]))
    return result


@router.get("/profile", response_model=ReaderProfile | None)
def get_profile():
    try:
        return load_profile()
    except SourceError as exc:
        raise HTTPException(500, str(exc)) from None


def save_local_json(path: Path, data: dict):
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=path.parent, delete=False, mode="w", encoding="utf-8") as file:
            temporary = Path(file.name)
            file.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        temporary.replace(path)
    except OSError:
        log.warning("reader_profile_save_failed")
        raise HTTPException(500, "无法保存本机配置") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def save_profile(profile: StoredReaderProfile):
    secrets = ("database_key", "image_xor_key", "image_aes_key")
    data = profile.model_dump(exclude=set(secrets))
    data.update({field: getattr(profile, field).get_secret_value() for field in secrets})
    save_local_json(profile_path, data)


@router.get("/account-selection", response_model=ReaderProfile | None)
def get_account_selection():
    path = profile_path.with_name("account-selection.json")
    try:
        return ReaderProfile.model_validate_json(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return get_profile()
    except (OSError, ValueError):
        raise HTTPException(500, "无法读取已选择的账号") from None


@router.put("/account-selection")
def save_account_selection(selection: ReaderProfile) -> ReaderProfile:
    if not selection.database_path.strip() or not selection.account.strip():
        raise HTTPException(422, "请填写数据库目录和账号 ID")
    save_local_json(profile_path.with_name("account-selection.json"), selection.model_dump())
    log.info("reader_account_selection_saved")
    return selection


class AccountSearch(BaseModel):
    directory: str = Field(default="", max_length=1024)


@router.post("/accounts/pick-directory")
def pick_directory() -> dict:
    log.info("reader_directory_picker_opening")
    script = (
        "import tkinter as tk; from tkinter import filedialog; "
        "root=tk.Tk(); root.withdraw(); root.attributes('-topmost', True); "
        "print(filedialog.askdirectory(parent=root, title='Choose WeChat data folder', mustexist=True)); "
        "root.destroy()"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-X", "utf8", "-c", script],
            capture_output=True, encoding="utf-8", timeout=180, check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        log.warning("reader_directory_picker_timeout")
        raise HTTPException(408, "目录选择超时，请重新打开选择窗口") from None
    except (OSError, subprocess.CalledProcessError):
        log.warning("reader_directory_picker_failed")
        raise HTTPException(503, "无法打开本机目录选择窗口，可使用自动查找或展开手动输入") from None
    directory = result.stdout.strip()
    log.info("reader_directory_picker_finished", selected=bool(directory))
    return {"directory": directory}


@router.post("/key/extract")
def extract_key() -> dict:
    if os.getenv("WECHAT_BOT_KEY_EXTRACTION") != "1":
        log.warning("wechat_key_extraction_disabled")
        raise HTTPException(403, "未启用从微信进程获取密钥，请手动提供已有密钥")
    if not key_extraction_lock.acquire(blocking=False):
        raise HTTPException(409, "已经在获取密钥，请等待当前操作结束")
    try:
        report_key_progress("preparing", "正在检查本地密钥工具和原生库")
        tool = CipherTalkCLI()
        pid = tool.restart_wechat(report_key_progress)
        report_key_progress("wechat_started", "微信已重新启动，正在加载密钥 DLL")
        key = tool.extract_with_progress(pid, report_key_progress)
        report_key_progress("complete", "已取得密钥，请验证数据库连接")
        return {"key": key}
    except SourceError as exc:
        report_key_progress("failed", str(exc))
        log.warning("wechat_key_extraction_failed", error=str(exc))
        raise HTTPException(502, str(exc)) from None
    finally:
        key_extraction_lock.release()


@router.post("/key/scan/{kind}")
def scan_key(kind: Literal["database", "image"], selection: ReaderProfile, restart: bool = False) -> dict:
    if os.getenv("WECHAT_BOT_KEY_EXTRACTION") != "1":
        log.warning("wechat_key_extraction_disabled")
        raise HTTPException(403, "后端未开启密钥提取")
    if sys.platform != "win32":
        raise HTTPException(501, "此密钥扫描工具只支持 Windows")
    if restart and kind != "database":
        raise HTTPException(422, "仅数据库密钥扫描支持重启")
    if not key_extraction_lock.acquire(blocking=False):
        raise HTTPException(409, "已有密钥提取正在执行")
    try:
        report_key_progress("preparing", "正在检查所选账号和本机扫描工具")
        path = Path(selection.database_path).expanduser().resolve()
        account_root = path.parent if path.name == "db_storage" else path
        storage = path if path.name == "db_storage" else path / "db_storage"
        if not selection.account.strip() or not (storage / "contact" / "contact.db").is_file():
            raise SourceError("请先选择有效的微信账号和数据库目录")
        dll = Path(os.getenv("WECHAT_BOT_SCANNER_DLL") or profile_path.parent / "tools" / "wechat_key_tool.dll")
        if not dll.is_file():
            raise SourceError("缺少本机扫描库，请把 wechat_key_tool.dll 放到 local-data/tools")
        report_key_progress("scanning", "正在查找图片模板并扫描密钥" if kind == "image" else "正在读取已登录微信的数据库密钥")
        if restart:
            CipherTalkCLI.restart_wechat(report_key_progress)
            report_key_progress("wechat_started", "微信已重新启动，请登录目标账号；后端正在等待密钥加载")
        deadline = time.monotonic() + 60
        for attempt in range(1, 61):
            if restart:
                log.info("wechat_login_scan_attempt", attempt=attempt)
            result = subprocess.run(
                [sys.executable, "-m", "server.wechat_scan_worker", str(dll.resolve()), kind, str(account_root)],
                capture_output=True, encoding="utf-8",
                timeout=min(30, max(1, deadline - time.monotonic())),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode:
                raise SourceError(f"扫描工具异常退出（退出码 {result.returncode}）")
            data = json.loads(result.stdout)
            if restart and data.get("key"):
                try:
                    if data.get("account") and data["account"] != selection.account:
                        raise SourceError("候选密钥属于其他账号")
                    SqlCipherReader(storage, data["key"], selection.account).sessions(limit=1)
                except SourceError:
                    log.info("wechat_login_candidate_rejected", attempt=attempt)
                    data.pop("key", None)
                    data["error"] = "no_key"
            diag = data.get("diagnostics") or {}
            retry = data.get("error") == "no_key" and diag.get("auth") is not False and diag.get("db_ok") is not False
            if not restart or not retry or time.monotonic() + 1 >= deadline:
                break
            time.sleep(1)
        if data.get("dll_loaded"):
            report_key_progress("dll_loaded", "扫描 DLL 已加载")
        if data.get("challenge_created"):
            report_key_progress("challenge_created", "扫描库已生成挑战，已提交签名并发起扫描")
        diagnostics = data.get("diagnostics") or {}
        if diagnostics:
            log.info("wechat_key_scan_diagnostics", kind=kind, **diagnostics)
            report_key_progress(
                "diagnostics",
                f"找到进程 {diagnostics.get('pids', '未知')} 个，打开 {diagnostics.get('opened', '未知')} 个，"
                f"读取 {diagnostics.get('bytes', '未知')} 字节，找到 {diagnostics.get('candidates', '未知')} 个候选。",
            )
        if data.get("error"):
            errors = {
                "image_template_missing": "所选目录没有可用图片缓存，尚未加载 DLL。若选的是离线副本，微信打开的新图片不会写到该副本。",
                "no_key": "已调用图片扫描，但没有返回 AES 密钥。请确认查看的图片缓存位于所选账号目录。",
                "dll_load_failed": "扫描 DLL 未能加载，请检查文件和 Python 架构",
                "challenge_failed": "扫描库初始化失败，请检查扫描库版本",
                "diagnostics_unavailable": "扫描未返回数据库密钥，且扫描库未提供诊断结果。",
            }
            if kind == "database" and data["error"] == "no_key":
                if diagnostics.get("auth") is False:
                    errors["no_key"] = "扫描库验签失败，请检查 DLL 和扫描协议版本是否一致。"
                elif diagnostics.get("db_ok") is False:
                    errors["no_key"] = "扫描库无法用所选 contact.db 校验密钥，请核对目录和数据库文件。"
                elif diagnostics.get("pids") == 0:
                    errors["no_key"] = "扫描库未找到微信进程。请打开电脑版微信并登录目标账号。"
                elif diagnostics.get("opened") == 0 or diagnostics.get("bytes") == 0:
                    errors["no_key"] = "扫描库找到了微信，但未读取到进程数据。请确认后端和微信由同一 Windows 用户、以同一权限级别启动。"
                else:
                    errors["no_key"] = "已读取微信进程数据，但未找到能解开所选数据库的密钥。先核对账号和目录；如需重试，可手动完全退出微信、重新打开并登录，再点扫描。"
            raise SourceError(errors.get(data["error"], "扫描库加载或调用失败，请检查扫描库与系统架构"))
        if kind == "database":
            if data.get("account") and data["account"] != selection.account:
                raise SourceError("已登录账号与所选目录不一致，请先切换账号")
            report_key_progress("validating", "已取得候选密钥，正在验证所选数据库")
            SqlCipherReader(storage, data["key"], selection.account).sessions(limit=1)
            data = {"key": data["key"]}
        report_key_progress("complete", "已取得密钥，可验证并保存配置")
        return data
    except (SourceError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        message = str(exc) if isinstance(exc, SourceError) else "密钥扫描未完成，请检查本机工具或稍后重试"
        report_key_progress("failed", message)
        log.warning("wechat_key_extraction_failed", error=message, kind=kind, error_type=type(exc).__name__)
        raise HTTPException(502, message) from None
    finally:
        key_extraction_lock.release()


@router.post("/accounts")
def find_accounts(settings: AccountSearch) -> dict:
    """Discover account folders from directory metadata alone."""
    documents = Path.home() / "Documents"
    roots = [Path(settings.directory).expanduser()] if settings.directory else [
        documents / "xwechat_files", documents / "WeChat Files",
    ]
    accounts = {}
    try:
        for root in roots:
            if not root.is_dir():
                continue
            if root.name == "db_storage":
                candidates = [root.parent]
            elif (root / "db_storage").is_dir():
                candidates = [root]
            else:
                candidates = [root, *root.iterdir()]
            for folder in candidates:
                storage = folder / "db_storage"
                if not (storage / "contact" / "contact.db").is_file():
                    continue
                path = str(storage.resolve())
                account = folder.name
                prefix, separator, suffix = account.rpartition("_")
                if separator and prefix.startswith("wxid_") and len(suffix) == 4 and all(
                    char in "0123456789abcdefABCDEF" for char in suffix
                ):
                    account = prefix
                accounts[path] = {"account": account, "database_path": path}
    except OSError:
        log.warning("reader_account_search_failed", custom_directory=bool(settings.directory))
        raise HTTPException(422, "无法访问该目录，请检查路径和读取权限") from None
    log.info("reader_account_search_finished", count=len(accounts), custom_directory=bool(settings.directory))
    return {"items": sorted(accounts.values(), key=lambda item: item["database_path"])}


@router.post("/probe")
def probe(settings: SourceSettings) -> dict:
    """Check a source without exposing its token or returning chat content."""
    if settings.save_profile and not settings.database_path:
        raise HTTPException(422, "请先选择数据库目录")
    if settings.save_profile and (reader_status["enabled"] or reader_status["running"]):
        raise HTTPException(409, "请先停止后台监听，再保存账号配置")
    try:
        if settings.database_path:
            if not settings.account:
                raise HTTPException(422, "请填写微信账号 ID")
            path = Path(settings.database_path).expanduser().resolve()
            key = settings.database_key.get_secret_value()
            if not key:
                profile = load_profile()
                if profile and Path(profile.database_path) == path and profile.account == settings.account:
                    key = profile.database_key.get_secret_value()
            source = SqlCipherReader(path, key, settings.account)
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
        if settings.save_profile:
            previous = load_profile()
            same_account = previous and previous.account == settings.account and Path(previous.database_path) == source.root
            save_profile(StoredReaderProfile(
                database_path=str(source.root), account=settings.account, database_key=SecretStr(key),
                image_xor_key=settings.image_xor_key if settings.image_xor_key.get_secret_value() or not same_account else previous.image_xor_key,
                image_aes_key=settings.image_aes_key if settings.image_aes_key.get_secret_value() or not same_account else previous.image_aes_key,
            ))
            save_account_selection(ReaderProfile(database_path=str(source.root), account=settings.account))
    except SourceError as exc:
        log.warning("desktop_probe_failed", error=str(exc))
        raise HTTPException(502, str(exc)) from None
    except (KeyError, TypeError):
        log.warning("desktop_probe_invalid_response")
        raise HTTPException(502, "密语代理返回了不兼容的会话数据") from None
    log.info("desktop_probe_succeeded", group_count=len(groups), message_readable=message_readable)
    return {
        "groups": groups, "has_more": bool(result.get("hasMore")),
        "message_readable": message_readable, "has_messages": has_messages,
    }
