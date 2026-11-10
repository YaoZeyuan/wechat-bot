"""Versioned phone tasks and their durable results."""
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/sender")
root = Path(__file__).resolve().parents[1] / "local-data" / "sender"
MAX_ASSET = 20 * 1024 * 1024


class Target(BaseModel):
    kind: Literal["contact", "group", "file_helper"]
    id: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=256)


class TextMessage(BaseModel):
    type: Literal["text"]
    text: str = Field(min_length=1, max_length=1800)


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
    return conn


def recover_jobs():
    with closing(database()) as conn, conn:
        for key, raw in conn.execute("SELECT id, result FROM jobs WHERE json_extract(result, '$.status') IN ('queued', 'running')"):
            result = json.loads(raw)
            result["status"] = "uncertain" if result["status"] == "running" else "failed"
            result["steps"].append("服务已重启，任务中断。请先在手机核对发送结果")
            conn.execute("UPDATE jobs SET result = ? WHERE id = ?", (json.dumps(result), key))

def save(key, result):
    with closing(database()) as conn, conn:
        conn.execute("UPDATE jobs SET result = ? WHERE id = ?", (json.dumps(result), key))


@router.put("/assets/{asset_id}")
async def upload(asset_id: UUID, filename: str, request: Request):
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
    return {"asset_id": asset_id}


@router.post("/jobs", status_code=202)
def submit(job: Job, tasks: BackgroundTasks):
    key = str(job.request_id)
    payload = job.model_dump_json()
    result = {"request_id": key, "status": "queued", "steps": [], "items": ["pending"] * len(job.messages)}
    with closing(database()) as conn, conn:
        inserted = conn.execute("INSERT OR IGNORE INTO jobs VALUES (?, ?, ?)", (key, payload, json.dumps(result)))
        if not inserted.rowcount:
            stored, previous = conn.execute("SELECT payload, result FROM jobs WHERE id = ?", (key,)).fetchone()
            if stored != payload:
                raise HTTPException(409, "任务 ID 已用于另一份请求")
            return json.loads(previous)
    tasks.add_task(run, job, result)
    return result


@router.get("/jobs/{request_id}")
def get_job(request_id: UUID):
    with closing(database()) as conn:
        row = conn.execute("SELECT result FROM jobs WHERE id = ?", (str(request_id),)).fetchone()
    if row is None:
        raise HTTPException(404, "任务不存在")
    return json.loads(row[0])


def run(job, result):
    from server.main import _devices, create_sender, sender_console_lock, sender_log

    key = str(job.request_id)

    def progress(message):
        result["steps"].append(message)
        save(key, result)
        sender_log.info("sender_job_step", request_id=key, msg=message)

    with sender_console_lock:
        index = None
        sending = False
        phone = None
        try:
            result["status"] = "running"
            progress("检查任务附件和手机连接")
            for item in job.messages:
                if isinstance(item, AssetMessage):
                    extensions = {".png", ".jpg", ".jpeg"} if item.type == "image" else {".txt", ".pdf"}
                    files = list((root / "assets" / str(item.asset_id)).glob("asset.*"))
                    if len(files) != 1 or files[0].suffix not in extensions:
                        raise ValueError("附件不存在或类型与消息不匹配")
                    if job.mode == "send":
                        raise ValueError("手机端尚未接入附件实际发送")
            ready = [item for item in _devices() if item["state"] == "device"]
            if len(ready) != 1:
                raise ValueError("请连接并授权一台手机")
            phone = create_sender(serial=ready[0]["serial"], dry_run=job.mode == "dry_run")
            phone.on_progress = progress
            phone.connect()
            target = "文件传输助手" if job.target.kind == "file_helper" else job.target.name
            phone.prepare_chat(target)
            progress("已核对目标会话")
            for index, item in enumerate(job.messages):
                progress(f"处理第 {index + 1} 条消息")
                if isinstance(item, TextMessage):
                    sending = job.mode == "send"
                    if not phone.send(target, item.text, current_chat=True):
                        result["items"][index] = "uncertain"
                        result["status"] = "uncertain"
                        progress("发送结果待核对，请在手机上确认")
                        return
                result["items"][index] = "checked" if job.mode == "dry_run" else "sent"
                sending = False
            result["status"] = "completed"
            progress("任务完成")
        except Exception as exc:
            result["status"] = "uncertain" if sending else "failed"
            if index is not None:
                result["items"][index] = result["status"]
            progress(str(exc) or type(exc).__name__)
        finally:
            if phone is not None and getattr(phone, "conn", None) is not None:
                phone.conn.close()
