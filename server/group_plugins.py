"""Per-group built-in plugin configuration and reply generation."""
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock

from pydantic import BaseModel, Field

from .reply import MessageEvent

PATH = Path(__file__).resolve().parents[1] / "local-data" / "group-plugins.json"
LOCK = Lock()
BEIJING = timezone(timedelta(hours=8))
RANKINGS = {"daily_activity": "活跃日榜", "total_activity": "活跃总榜", "total_lurkers": "潜水总榜"}
BUILTINS = ("fixed_reply", *RANKINGS)


class Settings(BaseModel):
    enabled: bool
    keyword: str = Field(default="测试", min_length=1, max_length=200)
    reply: str = Field(default="测试成功", min_length=1, max_length=1800)


def read_all():
    if not PATH.exists():
        return {}
    return json.loads(PATH.read_text(encoding="utf-8"))


def config(group_id, plugin_id, legacy):
    with LOCK:
        value = read_all().get(group_id, {}).get(plugin_id)
    if value is not None:
        return Settings.model_validate(value)
    return Settings(enabled=legacy.get(plugin_id, plugin_id == "fixed_reply"))


def save(group_id, plugin_id, settings):
    with LOCK:
        data = read_all()
        data.setdefault(group_id, {})[plugin_id] = settings.model_dump(
            include={"enabled"} if plugin_id in RANKINGS else None
        )
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
        for index, earliest, latest in json.loads(result)["text_sends"]:
            if latest >= start and earliest <= end:
                replies.append((job["messages"][index]["text"], earliest - 999, latest + 1000))
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


def split_reply(text, limit=1800):
    if len(text) <= limit:
        return [text]
    parts = []
    while len(text) > limit:
        split = text.rfind("\n", 0, limit + 1)
        if split <= 0:
            split = limit
        parts.append(text[:split])
        text = text[split:].removeprefix("\n")
    if text:
        parts.append(text)
    return parts

def dispatch(source, automation, event):
    from uuid import NAMESPACE_URL, uuid5

    from .sender_jobs import Job, queue_job, run

    legacy = automation.group_plugins(event.group_id)
    replies = []
    test = config(event.group_id, "fixed_reply", legacy)
    if test.enabled and event.text == test.keyword:
        replies.append(("fixed_reply", test.reply))
    for plugin_id, keyword in RANKINGS.items():
        if event.text != keyword or not config(event.group_id, plugin_id, legacy).enabled:
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
    if not replies:
        return False
    details = source.conversation_details([event.group_id]).get(event.group_id, {})
    name = details.get("remark") or details.get("original_name") or event.group_id
    for plugin_id, text in replies:
        job = Job(
            version=1, request_id=uuid5(NAMESPACE_URL, f"wechat-bot:{event.group_id}:{event.message_id}:{plugin_id}"),
            mode="send", target={"kind": "group", "id": event.group_id, "name": name},
            messages=[{"type": "text", "text": part} for part in split_reply(text)],
        )
        result, inserted = queue_job(job, strict=False, source=plugin_id)
        if inserted:
            run(job, result)
    return test.enabled and event.text == test.keyword
