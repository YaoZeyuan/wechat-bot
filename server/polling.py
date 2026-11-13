"""Read new group messages in source order and checkpoint completed batches."""

import structlog

from .automation import Automation
from .ciphertalk import SourceError
from .reply import MessageAuthor, MessageEvent
from .group_plugins import dispatch as dispatch_builtin

log = structlog.get_logger("wechat_bot.polling")
PAGE_SIZE = 50
MAX_PAGES = 100


def message_id(row: dict) -> str:
    if row.get("messageId"):
        return str(row["messageId"])
    # Node may round 64-bit server IDs when serializing JSON. Prefer local IDs
    # combined with sort sequence to distinguish messages across shards.
    if row.get("localId") and row.get("sortSeq"):
        return f'{row["sortSeq"]}:{row["localId"]}'
    raise SourceError("消息源未返回稳定的消息标识")


def incoming_text(group_id: str, row: dict, identity: str) -> MessageEvent | None:
    if row.get("direction") != "in":
        return None
    if row.get("kind", str(row.get("type"))) not in {"text", "1", "quote"}:
        return None
    sender_info = row.get("sender") or {}
    sender = sender_info.get("username") or row.get("senderUsername")
    timestamp = row.get("timestampMs") or int(row.get("createTime") or 0) * 1000
    if not sender or not timestamp:
        raise SourceError("文本消息缺少发送者或时间戳")
    return MessageEvent(
        group_id=group_id, message_id=identity, sender_id=sender,
        author=MessageAuthor(user_id=sender, group_nickname=sender_info.get("group_nickname"),
                             username=sender_info.get("original_name"), remark=sender_info.get("remark")),
        timestamp_ms=timestamp, text=row.get("text", row.get("content", "")),
    )


def page(source, group_id: str, offset: int, limit: int) -> list[dict]:
    result = source.messages(
        group_id, offset=offset, limit=limit, order="desc",
        includeRaw=False, includeMediaPaths=False,
    )
    rows = result.get("items", result.get("messages"))
    if not isinstance(rows, list):
        raise SourceError("消息源返回了不兼容的消息列表")
    return rows


def poll_group(source, automation: Automation, group_id: str, source_id: str = "") -> int:
    cursor_key = f"{source_id}:{group_id}" if source_id else group_id
    checkpoint = automation.cursor(cursor_key)
    if checkpoint is None:
        rows = page(source, group_id, 0, 1)
        automation.save_cursor(cursor_key, message_id(rows[0]) if rows else "")
        return 0

    newer: dict[str, dict] = {}
    reached_boundary = False
    for offset in range(0, PAGE_SIZE * MAX_PAGES, PAGE_SIZE):
        rows = page(source, group_id, offset, PAGE_SIZE)
        for row in rows:
            identity = message_id(row)
            if identity == checkpoint:
                reached_boundary = True
                break
            newer.setdefault(identity, row)
        if reached_boundary or len(rows) < PAGE_SIZE:
            break
    if not reached_boundary and (checkpoint or len(rows) == PAGE_SIZE):
        raise SourceError("未找到上次读取位置，已保留游标以避免漏读或重复触发")
    if not newer:
        return 0
    events = [(incoming_text(group_id, row, f"{source_id}:{identity}" if source_id else identity), row)
              for identity, row in reversed(newer.items())]
    sender_ids = list(dict.fromkeys(event.sender_id for event, _ in events if event is not None))
    contacts = {}
    if sender_ids and hasattr(source, "conversation_details"):
        for start in range(0, len(sender_ids), 100):
            contacts.update(source.conversation_details(sender_ids[start:start + 100]))
    nicknames = source.group_nicknames(group_id) if sender_ids and hasattr(source, "group_nicknames") else {}
    for event, row in events:
        if event is not None:
            contact = contacts.get(event.sender_id, {})
            author = event.author.model_copy(update={
                "group_nickname": nicknames.get(event.sender_id) or event.author.group_nickname,
                "username": contact.get("original_name") or event.author.username,
                "remark": contact.get("remark") or event.author.remark,
            })
            event = event.model_copy(update={"author": author})
            if row.get("kind") == "quote":
                dispatch_builtin(source, automation, event, quotes=row.get("quotes") or [])
            else:
                dispatch_builtin(source, automation, event)
    automation.save_cursor(cursor_key, next(iter(newer)))
    log.info("desktop_messages_processed", count=len(newer))
    return len(newer)
