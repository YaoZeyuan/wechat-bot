"""Per-group built-in plugin configuration and reply generation."""
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock

from pydantic import BaseModel, Field

from .reply import MessageEvent
from .plugin import ai_chat
from .plugin import chat_summary
from .ciphertalk import SourceError
from .plugin import self_intro
from .plugins import PLUGINS

PATH = Path(__file__).resolve().parents[1] / "local-data" / "group-plugins.json"
LOCK = Lock()
BEIJING = timezone(timedelta(hours=8))
RANKINGS = {"daily_activity": "活跃日榜", "total_activity": "活跃总榜", "total_lurkers": "潜水总榜"}
ACTIVITY_STATS = "activity_stats"
BUILTINS = (ACTIVITY_STATS, self_intro.PLUGIN_ID, ai_chat.PLUGIN_ID, chat_summary.PLUGIN_ID)


class Settings(BaseModel):
    enabled: bool
    assistant_name: str | None = Field(default=None, max_length=80)
    intro_reply: str | None = Field(default=None, max_length=2500)


class Defaults(Settings):
    enabled: bool = False
    global_enabled: bool = True


def defaults(plugin_id, data=None):
    if data is None:
        with LOCK:
            data = read_all()
    return Defaults.model_validate({
        "enabled": PLUGINS[plugin_id]["default_enabled"],
        **data.get("_defaults", {}).get(plugin_id, {}),
    })


def save_defaults(plugin_id, settings):
    with LOCK:
        data = read_all()
        data.setdefault("_defaults", {})[plugin_id] = settings.model_dump()
        write_all(data)


def ai_routes(group_id):
    with LOCK:
        return read_all().get(group_id, {}).get("ai_chat_routes", {})


def save_ai_route(group_id, route_id, patch):
    with LOCK:
        data = read_all()
        data.setdefault(group_id, {}).setdefault("ai_chat_routes", {})[route_id] = patch.model_dump(mode="json")
        write_all(data)


def read_all():
    if not PATH.exists():
        return {}
    return json.loads(PATH.read_text(encoding="utf-8"))


def config(group_id, plugin_id, legacy, data=None):
    if data is None:
        with LOCK:
            data = read_all()
    stored = data.get(group_id, {})
    value = stored.get(plugin_id)
    inherited = defaults(plugin_id, data).model_dump(exclude={"global_enabled"})
    if value is not None and any(key in value for key in ("assistant_name", "intro_reply")):
        inherited.update(assistant_name=value.get("assistant_name"), intro_reply=value.get("intro_reply"))
    if plugin_id == ACTIVITY_STATS and any(key in stored or key in legacy for key in RANKINGS):
        inherited["enabled"] = any(stored.get(key, {}).get("enabled", legacy.get(key, False)) for key in RANKINGS)
    inherited["enabled"] = (value or {}).get("enabled", legacy.get(plugin_id, inherited["enabled"]))
    return Settings.model_validate(inherited)


def save(group_id, plugin_id, settings):
    with LOCK:
        data = read_all()
        fields = None if plugin_id == self_intro.PLUGIN_ID else {"enabled"}
        current = data.setdefault(group_id, {}).setdefault(plugin_id, {})
        current.update(settings.model_dump(include=fields, exclude_unset=True))
        write_all(data)


def write_all(data):
    PATH.parent.mkdir(parents=True, exist_ok=True)
    pending = PATH.with_suffix(".tmp")
    pending.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pending.replace(PATH)


def delete_group_settings(group_id):
    with LOCK:
        data = read_all()
        if group_id in data:
            del data[group_id]
            write_all(data)


def bot_replies(group_id, start, end):
    from contextlib import closing

    from .sender_jobs import database

    with closing(database()) as conn:
        rows = conn.execute(
            "SELECT payload, result FROM jobs WHERE json_extract(payload, '$.target.id')=? "
            "AND json_type(result, '$.text_sends')='array'", (group_id,),
        ).fetchall()
    replies = []
    for payload, result in rows:
        job = json.loads(payload)
        for index, earliest, latest, *sent_text in json.loads(result)["text_sends"]:
            if latest >= start and earliest <= end:
                text = sent_text[0] if sent_text else job["messages"][index]["text"]
                replies.append((text, earliest - 999, latest + 1000))
    return replies

def daily_activity(source, event: MessageEvent) -> str:
    local = datetime.fromtimestamp(event.timestamp_ms / 1000, BEIJING)
    start = int(local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
    def daily_rows():
        offset = 0
        while True:
            rows = source.messages(event.group_id, offset=offset, limit=100, order="desc")["items"]
            yield from rows
            if len(rows) < 100 or any(row.get("timestampMs", 0) < start for row in rows):
                break
            offset += len(rows)

    return activity_board(source, event, daily_rows(), start=start, limit=10)


def total_activity(source, event: MessageEvent, *, lurkers=False) -> str:
    from contextlib import closing

    members = source.group_members(event.group_id) if lurkers else None
    with closing(source.iter_messages(event.group_id)) as rows:
        return activity_board(source, event, rows, start=0, limit=20, members=members)


def activity_board(source, event, rows, *, start, limit, members=None):
    counts = Counter(dict.fromkeys(members, 0)) if members is not None else Counter()
    automated = bot_replies(event.group_id, start, event.timestamp_ms)
    group_names = source.group_nicknames(event.group_id) if hasattr(source, "group_nicknames") else {}
    for row in rows:
        stamp = row.get("timestampMs", 0)
        sender = row.get("sender") or {}
        username = sender.get("username")
        code = int(row.get("type_code") or 0) & 0xFFFFFFFF
        if not username or not start <= stamp <= event.timestamp_ms or code in {10000, 10002}:
            continue
        if members is not None and username not in members:
            continue
        if row.get("direction") == "out" and row.get("kind") == "text":
            match = next((i for i, (text, earliest, latest) in enumerate(automated)
                          if row.get("text") == text and earliest <= stamp <= latest), None)
            if match is not None:
                automated.pop(match)
                continue
        counts[username] += 1
        if sender.get("group_nickname"):
            group_names.setdefault(username, sender["group_nickname"])
    direction = 1 if members is not None else -1
    top = sorted(counts, key=lambda user: (direction * counts[user], user))[:limit]
    if not top:
        return "今天还没有可统计的发言。" if limit == 10 else "本群没有可统计的发言。"
    details = source.conversation_details(top)
    lines = []
    for index, user in enumerate(top, 1):
        name = details.get(user, {}).get("original_name") or user
        nickname = group_names.get(user) or name
        display = nickname if nickname == name else f"{nickname}-{name}"
        lines.append(f"{index}. {display}-{counts[user]}条")
    return "\n".join(lines)


def dispatch(source, automation, event, *, quotes=None):
    from uuid import NAMESPACE_URL, uuid5

    from fastapi import HTTPException

    from .sender_jobs import Job, get_job, queue_job, run, save

    legacy = automation.group_plugins(event.group_id)
    with LOCK:
        data = read_all()
    enabled = {
        plugin_id: config(event.group_id, plugin_id, legacy, data)
        for plugin_id in BUILTINS if defaults(plugin_id, data).global_enabled
    }
    replies = []
    summary = None
    if quotes is None and chat_summary.PLUGIN_ID in enabled and enabled[chat_summary.PLUGIN_ID].enabled:
        try:
            summary = chat_summary.command(event.text, event.timestamp_ms)
        except ValueError:
            replies.append((chat_summary.PLUGIN_ID, "日期无效，请使用 YYYYMMDD 格式，例如 梳理20261111。"))
        if summary and isinstance(summary[0], int) and summary[0] < 50:
            replies.append((chat_summary.PLUGIN_ID, "最小梳理50条，请检查后重试"))
            summary = None
        if summary:
            replies.append((chat_summary.PLUGIN_ID, ""))
    if quotes is None and self_intro.PLUGIN_ID in enabled:
        intro = enabled[self_intro.PLUGIN_ID]
        if intro.enabled and intro.intro_reply and self_intro.match(event.text, (intro.assistant_name or "").strip()):
            replies.append((self_intro.PLUGIN_ID, intro.intro_reply))
    for behavior, keyword in RANKINGS.items():
        if quotes is not None or event.text != keyword or ACTIVITY_STATS not in enabled or not enabled[ACTIVITY_STATS].enabled:
            continue
        if behavior == "daily_activity":
            text = daily_activity(source, event)
        else:
            try:
                text = total_activity(source, event, lurkers=behavior == "total_lurkers")
            except SourceError as exc:
                text = f"{keyword}暂不可用：{exc}"
        replies.append((ACTIVITY_STATS, text))
    overrides = data.get(event.group_id, {}).get("ai_chat_routes", {})
    question = ai_chat.trigger(event.text, overrides=overrides) if ai_chat.PLUGIN_ID in enabled and enabled[ai_chat.PLUGIN_ID].enabled else None
    if question:
        replies.append((ai_chat.PLUGIN_ID, ""))
    if not replies:
        return
    identities = [event.group_id, event.sender_id] if question else [event.group_id]
    contacts = source.conversation_details(identities)
    details = contacts.get(event.group_id, {})
    name = details.get("remark") or details.get("original_name") or event.group_id
    for plugin_id, text in replies:
        job_key = next(key for key, keyword in RANKINGS.items() if keyword == event.text) if plugin_id == ACTIVITY_STATS else plugin_id
        request_id = uuid5(NAMESPACE_URL, f"wechat-bot:{event.group_id}:{event.message_id}:{job_key}")
        if plugin_id in {ai_chat.PLUGIN_ID, chat_summary.PLUGIN_ID}:
            try:
                get_job(request_id)
            except HTTPException as exc:
                if exc.status_code != 404:
                    raise
            else:
                continue
        if plugin_id == ai_chat.PLUGIN_ID and question:
            try:
                author = contacts.get(event.sender_id, {})
                name_hint = author.get("remark") or author.get("original_name") or ""
                prompt = ai_chat.format_question(question[1], name_hint, quotes or [])
                text = ai_chat.answer(question[0], prompt, request_id=str(request_id), overrides=overrides)
            except ai_chat.ChatError as exc:
                text = f"AI 问答暂不可用：{exc}"
        messages = [{"type": "text", "text": text}]
        if plugin_id == chat_summary.PLUGIN_ID and summary:
            job = Job(version=1, request_id=request_id, mode="send",
                      target={"kind": "group", "id": event.group_id, "name": name},
                      messages=[{"type": "file", "asset_id": request_id}])
            result, inserted = queue_job(job, strict=False, source=plugin_id)
            if not inserted:
                continue
            result["status"] = "running"

            def progress(message):
                result["steps"].append(message)
                save(str(request_id), result)

            scope = f"最近 {summary[0]} 条" if isinstance(summary[0], int) else f"{summary[0]} 的"
            progress(f"开始梳理{scope}聊天记录")
            try:
                chat_summary.make_report(source, event, request_id, *summary, progress=progress)
            except (OSError, RuntimeError, ValueError, SourceError, chat_summary.subprocess.SubprocessError) as exc:
                chat_summary.log.warning("chat_summary_failed", msg="聊天梳理失败",
                                         request_id=str(request_id), error_type=type(exc).__name__)
                reason = str(exc) if isinstance(exc, (ai_chat.ChatError, SourceError)) else "无法生成或准备 PDF，请查看 Server 日志"
                result.update(status="failed", items=["failed"])
                progress(f"聊天梳理失败：{reason}")
            else:
                run(job, result)
            continue
        job = Job(
            version=1, request_id=request_id,
            mode="send", target={"kind": "group", "id": event.group_id, "name": name},
            messages=messages,
        )
        result, inserted = queue_job(job, strict=False, source=plugin_id)
        if inserted:
            run(job, result)
