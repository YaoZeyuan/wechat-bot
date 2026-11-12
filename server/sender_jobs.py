"""Versioned phone tasks and their durable results."""
import json
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import time
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/sender")
root = Path(__file__).resolve().parents[1] / "local-data" / "sender"
MAX_ASSET = 20 * 1024 * 1024
HISTORY_LIMIT = 100


class Target(BaseModel):
    kind: Literal["contact", "group", "file_helper"]
    id: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=256)


class TextMessage(BaseModel):
    type: Literal["text"]
    text: str = Field(min_length=1, max_length=256 * 1024)


def split_text(text: str) -> list[str]:
    if len(text) <= 500:
        return [text]
    parts = []
    while len(text) > 500 and len(parts) < 5:
        newline = text.find("\n", 400, 500)
        end = newline + 1 if newline >= 0 else 500
        parts.append(text[:end])
        text = text[end:]
    if text:
        if len(parts) == 5:
            parts[-1] = f"{parts[-1]}……(此处略去{len(text)}字)"
        else:
            parts.append(text)
    return [f"({index}/{len(parts)}){part}" for index, part in enumerate(parts, 1)]


class AssetMessage(BaseModel):
    type: Literal["image", "file"]
    asset_id: UUID


class Job(BaseModel):
    version: Literal[1]
    request_id: UUID
    mode: Literal["send", "dry_run"]
    target: Target
    messages: list[Annotated[TextMessage | AssetMessage, Field(discriminator="type")]] = Field(min_length=1, max_length=20)


def database():
    root.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(root / "jobs.sqlite")
    conn.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, payload TEXT, result TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS sender_history (job_id TEXT PRIMARY KEY, source TEXT, created_ms INTEGER)")
    return conn


def recover_jobs():
    with closing(database()) as conn, conn:
        conn.execute(
            "INSERT OR IGNORE INTO sender_history SELECT id, '历史任务', NULL FROM "
            "(SELECT id, rowid FROM jobs ORDER BY rowid DESC LIMIT ?) "
            "WHERE NOT EXISTS (SELECT 1 FROM sender_history) ORDER BY rowid",
            (HISTORY_LIMIT,),
        )
        for key, raw in conn.execute("SELECT id, result FROM jobs WHERE json_extract(result, '$.status') IN ('queued', 'running')"):
            result = json.loads(raw)
            result["status"] = "uncertain" if result["status"] == "running" else "failed"
            result["steps"].append("服务已重启，任务中断。请先在手机核对发送结果")
            conn.execute("UPDATE jobs SET result = ? WHERE id = ?", (json.dumps(result), key))
    trim_history()

def save(key, result):
    with closing(database()) as conn, conn:
        conn.execute("UPDATE jobs SET result = ? WHERE id = ?", (json.dumps(result), key))


@router.get("/screen")
def screen():
    from adbutils import adb
    from adbutils.errors import AdbError
    from server.main import _devices

    ready = [item for item in _devices() if item["state"] == "device"]
    if len(ready) != 1:
        raise HTTPException(409, "请连接并授权一台手机")
    try:
        image = adb.device(ready[0]["serial"]).shell(
            ["screencap", "-p"], encoding=None, timeout=8
        )
    except (OSError, AdbError) as exc:
        raise HTTPException(502, "手机截图失败") from exc
    if not image.startswith(b"\x89PNG\r\n\x1a\n"):
        raise HTTPException(502, "手机未返回有效的 PNG 截图")
    return Response(image, media_type="image/png", headers={"Cache-Control": "no-store"})

@router.put("/assets/{asset_id}")
async def upload(asset_id: UUID, filename: str, request: Request):
    filename = Path(filename.replace(chr(92), "/")).name
    if not filename or len(filename.encode("utf-8")) > 200 or any(ord(ch) < 32 for ch in filename):
        raise HTTPException(422, "附件文件名无效")
    suffix = Path(filename).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".txt", ".pdf"}:
        raise HTTPException(422, "附件仅支持 PNG、JPEG、TXT、PDF")
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_ASSET:
            raise HTTPException(413, "附件不能超过 20 MB")
        data.extend(chunk)
    if not data:
        raise HTTPException(422, "附件不能为空")
    folder = root / "assets" / str(asset_id)
    try:
        folder.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise HTTPException(409, "附件 ID 已存在") from exc
    (folder / ("asset" + suffix)).write_bytes(data)
    (folder / "filename.json").write_text(json.dumps(filename), encoding="utf-8")
    return {"asset_id": asset_id}


def stage_local_pdf(asset_id: UUID, path: Path, filename: str):
    if path.stat().st_size > MAX_ASSET:
        raise ValueError("生成的 PDF 超过 Sender 的 20 MB 限制")
    folder = root / "assets" / str(asset_id)
    folder.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=folder, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            with path.open("rb") as source:
                shutil.copyfileobj(source, stream)
        except Exception:
            stream.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(folder / "asset.pdf")
        (folder / "filename.json").write_text(json.dumps(filename), encoding="utf-8")
    finally:
        temporary.unlink(missing_ok=True)


@router.post("/jobs", status_code=202)
def submit(job: Job, tasks: BackgroundTasks):
    result, inserted = queue_job(job)
    if inserted:
        tasks.add_task(run, job, result)
    return result


def queue_job(job: Job, *, strict=True, source="manual"):
    key = str(job.request_id)
    payload = job.model_dump_json()
    result = {"request_id": key, "status": "queued", "steps": [], "items": ["pending"] * len(job.messages)}
    with closing(database()) as conn, conn:
        inserted = conn.execute("INSERT OR IGNORE INTO jobs VALUES (?, ?, ?)", (key, payload, json.dumps(result)))
        if not inserted.rowcount:
            stored, previous = conn.execute("SELECT payload, result FROM jobs WHERE id = ?", (key,)).fetchone()
            if strict and Job.model_validate_json(stored) != job:
                raise HTTPException(409, "任务 ID 已用于另一份请求")
            return json.loads(previous), False
        conn.execute("INSERT INTO sender_history VALUES (?, ?, ?)", (key, source, int(time() * 1000)))
    return result, True


@router.get("/history")
def sender_history(offset: int = 0, limit: int = 50):
    if not 0 <= offset < HISTORY_LIMIT or not 1 <= limit <= 100:
        raise HTTPException(422, "日志分页参数无效")
    with closing(database()) as conn:
        rows = conn.execute(
            "SELECT h.job_id, h.source, h.created_ms, j.payload, j.result FROM sender_history h "
            "JOIN jobs j ON j.id=h.job_id ORDER BY h.rowid DESC LIMIT ? OFFSET ?",
            (min(limit, HISTORY_LIMIT - offset), offset),
        ).fetchall()
    return [{"id": key, "source": source, "created_ms": created, "target": json.loads(payload)["target"],
             "status": json.loads(result)["status"]} for key, source, created, payload, result in rows]


@router.post("/history/{request_id}/retry", status_code=202)
def retry_sender_job(request_id: UUID, tasks: BackgroundTasks):
    with closing(database()) as conn:
        row = conn.execute(
            "SELECT j.payload, j.result FROM sender_history h JOIN jobs j ON j.id=h.job_id WHERE h.job_id=?",
            (str(request_id),),
        ).fetchone()
    if row is None:
        raise HTTPException(404, "原任务不存在")
    if json.loads(row[1])["status"] in {"queued", "running"}:
        raise HTTPException(409, "请等原任务执行结束后再重试")
    original = Job.model_validate_json(row[0])
    job = original.model_copy(update={"request_id": uuid4()})
    result, _ = queue_job(job, source="retry")
    tasks.add_task(run, job, result)
    return result


@router.get("/history/{request_id}/ai")
def sender_history_ai(request_id: UUID):
    key = str(request_id)
    with closing(database()) as conn:
        retained = conn.execute(
            "SELECT 1 FROM sender_history WHERE job_id=? AND source='ai_chat'", (key,),
        ).fetchone()
    if not retained:
        raise HTTPException(404, "AI 任务不存在")
    events = {"ai_chat_request", "ai_chat_response", "ai_chat_http_error", "ai_chat_transport_error"}
    fields = ("msg", "timestamp", "provider", "model", "url", "status", "elapsed_seconds",
              "request_body", "response_body")
    records = []
    for name in ("server.log.3", "server.log.2", "server.log.1", "server.log"):
        try:
            with (root.parent / "logs" / name).open(encoding="utf-8", errors="replace") as log_file:
                for line in log_file:
                    if key not in line:
                        continue
                    try:
                        entry = json.loads(line)
                    except ValueError:
                        continue
                    if (entry.get("logger") == "wechat_bot.ai_chat"
                            and entry.get("request_id") == key and entry.get("event") in events):
                        records.append({field: entry[field] for field in fields if field in entry})
        except FileNotFoundError:
            continue
    return records


@router.get("/history/{request_id}/screens/{step}")
def sender_history_screen(request_id: UUID, step: int):
    with closing(database()) as conn:
        retained = conn.execute("SELECT 1 FROM sender_history WHERE job_id=?", (str(request_id),)).fetchone()
    path = root / "screens" / str(request_id) / f"{step}.png"
    if not retained:
        raise HTTPException(404, "截图不存在")
    try:
        image = path.read_bytes()
    except FileNotFoundError:
        raise HTTPException(404, "截图不存在") from None
    return Response(image, media_type="image/png", headers={"Cache-Control": "no-store"})


def trim_history():
    with closing(database()) as conn, conn:
        removed = conn.execute(
            "DELETE FROM sender_history WHERE rowid IN (SELECT h.rowid FROM sender_history h "
            "JOIN jobs j ON j.id=h.job_id "
            "WHERE h.rowid NOT IN (SELECT rowid FROM sender_history ORDER BY rowid DESC LIMIT ?) "
            "AND json_extract(j.result, '$.status') NOT IN ('queued', 'running')) RETURNING job_id",
            (HISTORY_LIMIT,),
        ).fetchall()
        for (key,) in removed:
            row = conn.execute("SELECT result FROM jobs WHERE id=?", (key,)).fetchone()
            result = json.loads(row[0])
            result.pop("screens", None)
            result["steps"] = []
            conn.execute("UPDATE jobs SET result=? WHERE id=?", (json.dumps(result), key))
    for (key,) in removed:
        shutil.rmtree(root / "screens" / key, ignore_errors=True)


@router.get("/jobs/{request_id}")
def get_job(request_id: UUID):
    with closing(database()) as conn:
        row = conn.execute("SELECT result FROM jobs WHERE id = ?", (str(request_id),)).fetchone()
    if row is None:
        raise HTTPException(404, "任务不存在")
    return json.loads(row[0])


def resolve_target(target: Target) -> str:
    if target.kind == "file_helper":
        return "文件传输助手"
    if target.kind != "group":
        return target.name

    from server.reader import history_source

    source = history_source()
    lookup = source.sessions(q=target.id, limit=50)
    group = next((item for item in lookup["items"] if item["sessionId"] == target.id), None)
    if group is None:
        raise ValueError("电脑端群列表中找不到目标群 ID，请重新选择会话")
    name = group["displayName"]
    if target.name.strip() != name:
        raise ValueError(f"目标群当前显示名是「{name}」，请刷新会话列表后重新选择")
    matches = source.sessions(q=name, limit=50)
    same_name = [item for item in matches["items"] if item["displayName"].strip().casefold() == name.strip().casefold()]
    if matches["hasMore"] or len(same_name) != 1 or same_name[0]["sessionId"] != target.id:
        raise ValueError(f"无法确认「{name}」是唯一群名，请为目标群设置唯一备注后重试")
    return name

def run(job, result):
    from sender import SendError
    from sender.accessibility import ShareRejected
    from server.main import _devices, create_sender, sender_console_lock, sender_log

    key = str(job.request_id)
    serial = None

    def progress(message):
        result["steps"].append(message)
        if serial:
            try:
                from adbutils import adb

                image = adb.device(serial).shell(["screencap", "-p"], encoding=None, timeout=3)
                if image.startswith(b"\x89PNG\r\n\x1a\n"):
                    step = len(result["steps"]) - 1
                    folder = root / "screens" / key
                    folder.mkdir(parents=True, exist_ok=True)
                    (folder / f"{step}.png").write_bytes(image)
                    result.setdefault("screens", []).append(step)
            except Exception as exc:
                sender_log.warning("sender_step_screen_failed", request_id=key, error=type(exc).__name__)
        save(key, result)
        sender_log.info("sender_job_step", request_id=key, msg=message)

    with sender_console_lock:
        index = None
        sending = False
        phone = None
        try:
            result["status"] = "running"
            progress("检查任务附件和手机连接")
            target = resolve_target(job.target)
            if job.target.kind == "group":
                progress(f"电脑端已核对群 ID 和唯一显示名「{target}」")
            assets = {}
            for item in job.messages:
                if isinstance(item, AssetMessage):
                    extensions = {".png", ".jpg", ".jpeg"} if item.type == "image" else {".txt", ".pdf"}
                    files = list((root / "assets" / str(item.asset_id)).glob("asset.*"))
                    if len(files) != 1 or files[0].suffix not in extensions:
                        raise ValueError("附件不存在或类型与消息不匹配")
                    metadata = files[0].with_name("filename.json")
                    filename = json.loads(metadata.read_text(encoding="utf-8")) if metadata.exists() else files[0].name
                    assets[item.asset_id] = (files[0], filename)
            ready = [item for item in _devices() if item["state"] == "device"]
            if len(ready) != 1:
                raise ValueError("请连接并授权一台手机")
            serial = ready[0]["serial"]
            phone = create_sender(serial=serial, dry_run=job.mode == "dry_run")
            if assets and not hasattr(phone, "send_asset"):
                raise ValueError("附件发送需要手机无障碍通道")
            phone.on_progress = progress
            phone.connect()

            for index, item in enumerate(job.messages):
                progress(f"处理第 {index + 1} 条消息")
                if hasattr(phone, "prepare_device"):
                    phone.prepare_device()
                sending = job.mode == "send"
                if isinstance(item, TextMessage):
                    parts = split_text(item.text)
                    for part_index, part in enumerate(parts, 1):
                        if len(parts) > 1:
                            progress(f"发送文字第 {part_index}/{len(parts)} 段")
                        started_ms = int(time() * 1000)
                        sending = False
                        try:
                            phone.prepare_chat(target)
                        except SendError as exc:
                            if not hasattr(phone, "share_text"):
                                raise
                            progress(f"会话内定位未通过（{exc}），重新打开微信后尝试分享")
                            phone.prepare_device()
                            sending = job.mode == "send"
                            ok = phone.share_text(target, part, group=job.target.kind == "group")
                        else:
                            sending = job.mode == "send"
                            ok = phone.send(target, part, current_chat=True)
                        if sending:
                            result.setdefault("text_sends", []).append([index, started_ms, int(time() * 1000), part])
                        if not ok:
                            break
                        sending = False
                else:
                    path, filename = assets[item.asset_id]
                    ok = phone.send_asset(target, item.type, path, filename, group=job.target.kind == "group")
                if not ok:
                    result["items"][index] = "uncertain"
                    result["status"] = "uncertain"
                    progress("发送结果待核对，请在手机上确认")
                    return
                result["items"][index] = "checked" if job.mode == "dry_run" else "sent"
                sending = False
            result["status"] = "completed"
            progress("任务完成")
        except Exception as exc:
            result["status"] = "uncertain" if sending and not isinstance(exc, ShareRejected) else "failed"
            if index is not None:
                result["items"][index] = result["status"]
            progress(str(exc) or type(exc).__name__)
        finally:
            if phone is not None and getattr(phone, "conn", None) is not None:
                phone.conn.close()
            try:
                trim_history()
            except (OSError, sqlite3.Error):
                sender_log.exception("sender_history_cleanup_failed", request_id=key)
