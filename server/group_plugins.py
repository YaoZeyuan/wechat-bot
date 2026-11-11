"""Per-group built-in plugin configuration and reply generation."""
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock

from pydantic import BaseModel

from .reply import MessageEvent
from .plugin import ai_chat

PATH = Path(__file__).resolve().parents[1] / "local-data" / "group-plugins.json"
LOCK = Lock()
BEIJING = timezone(timedelta(hours=8))
RANKINGS = {"daily_activity": "活跃日榜", "total_activity": "活跃总榜", "total_lurkers": "潜水总榜"}
BUILTINS = tuple(RANKINGS)


class Settings(BaseModel):
    enabled: bool


def read_all():
    if not PATH.exists():
        return {}
    return json.loads(PATH.read_text(encoding="utf-8"))


def config(group_id, plugin_id, legacy):
    with LOCK:
        value = read_all().get(group_id, {}).get(plugin_id)
    if value is not None:
        return Settings.model_validate(value)
    return Settings(enabled=legacy.get(plugin_id, False))


def save(group_id, plugin_id, settings):
    with LOCK:
        data = read_all()
        data.setdefault(group_id, {})[plugin_id] = settings.model_dump()
        PATH.parent.mkdir(parents=True, exist_ok=True)
        pending = PATH.with_suffix(".tmp")
        pending.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        pending.replace(PATH)


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
        lines.append(f"{index}. 【{nickname}】({name})-{counts[user]}条")
    return "\n".join(lines)


def dispatch(source, automation, event, *, quotes=None):
    from uuid import NAMESPACE_URL, uuid5

    from fastapi import HTTPException

    from .sender_jobs import Job, get_job, queue_job, run

    legacy = automation.group_plugins(event.group_id)
    replies = []
    for plugin_id, keyword in RANKINGS.items():
        if quotes is not None or event.text != keyword or not config(event.group_id, plugin_id, legacy).enabled:
            continue
        if plugin_id == "daily_activity":
            text = daily_activity(source, event)
        else:
            from .ciphertalk import SourceError

            try:
                text = total_activity(source, event, lurkers=plugin_id == "total_lurkers")
            except SourceError as exc:
                text = f"{keyword}暂不可用：{exc}"
        replies.append((plugin_id, text))
    question = ai_chat.trigger(event.text) if legacy.get(ai_chat.PLUGIN_ID, False) else None
    if question:
        replies.append((ai_chat.PLUGIN_ID, ""))
    if not replies:
        return
    identities = [event.group_id, event.sender_id] if question else [event.group_id]
    contacts = source.conversation_details(identities)
    details = contacts.get(event.group_id, {})
    name = details.get("remark") or details.get("original_name") or event.group_id
    for plugin_id, text in replies:
        request_id = uuid5(NAMESPACE_URL, f"wechat-bot:{event.group_id}:{event.message_id}:{plugin_id}")
        if plugin_id == ai_chat.PLUGIN_ID and question:
            try:
                get_job(request_id)
            except HTTPException as exc:
                if exc.status_code != 404:
                    raise
            else:
                continue
            try:
                author = contacts.get(event.sender_id, {})
                name_hint = author.get("remark") or author.get("original_name") or ""
                prompt = ai_chat.format_question(event.text, name_hint, quotes or [])
                text = ai_chat.answer(question[0], prompt, request_id=str(request_id))
            except ai_chat.ChatError as exc:
                text = f"AI 问答暂不可用：{exc}"
        job = Job(
            version=1, request_id=request_id,
            mode="send", target={"kind": "group", "id": event.group_id, "name": name},
            messages=[{"type": "text", "text": text}],
        )
        result, inserted = queue_job(job, strict=False, source=plugin_id)
        if inserted:
            run(job, result)
