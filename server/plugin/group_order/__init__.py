"""Group warning commands and local warning records."""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock

PLUGIN_ID = "group_order"
METADATA = {"name": "秩序管理", "description": "管理群警告并查询警告列表和信用分",
            "default_enabled": False, "version": "1.0.0", "capability": "手机发送",
            "group": "群聊工具"}
ROOT = Path(__file__).resolve().parents[3] / "local-data"
LOCK = Lock()
BEIJING = timezone(timedelta(hours=8))
INVALID = "警告指令格式不正确，请按警告@xxx Y天格式进行，Y 为 1 至 99 的整数"
QUERIES = {"警告列表", "信用分", "信用分列表", "党人碑"}
COMMAND = re.compile(r"(警告|撤销|以无暗者之名净化)\s*@(.+)", re.DOTALL)


def matches(text):
    text = text.strip()
    return text in QUERIES or COMMAND.fullmatch(text) is not None


def stamp(timestamp, fmt="%Y-%m-%d %H:%M:%S"):
    return datetime.fromtimestamp(timestamp, BEIJING).strftime(fmt)


def read(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(".tmp")
    pending.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pending.replace(path)


def deactivate(record, when, actor, reason):
    record.update(active=False, ended_at=when, ended_by=actor, reason=reason)


def display(person):
    return person["group_nickname"] or person["username"] or person["id"]


def warning_list(active):
    grouped = {}
    for row in sorted(active, key=lambda item: item["expires_at"]):
        grouped.setdefault(row["target"]["id"], []).append(row)
    if not grouped:
        return "当前没有生效中的警告"
    lines = ["⚠️ 累计警告列表"]
    for i, rows in enumerate(sorted(grouped.values(), key=lambda group: group[-1]["expires_at"]), 1):
        latest = stamp(rows[-1]["expires_at"])
        lines.append(f'{i}. {display(rows[-1]["target"])} > {len(rows)}次 ({latest} 结束)')
        if len(rows) > 1:
            for j, row in enumerate(rows[:3], 1):
                started = stamp(row["issued_at"], "%m-%d %H:%M")
                lines.append(f'{i}.{j}  {started}-警告{j} ({stamp(row["expires_at"])} 结束)')
        if len(rows) > 3:
            lines.append(f"{i}.4  ...(省略剩余{len(rows) - 3}条, 最晚警告至{latest} )")
    return "\n".join(lines)


def ranking(records, cutoff):
    grouped = {}
    for row in sorted(records, key=lambda item: item["issued_at"]):
        if row["issued_at"] >= cutoff and row["reason"] not in {"撤销", "被无暗者净化"}:
            grouped.setdefault(row["target"]["id"], []).append(row)
    top = sorted(grouped.values(), key=lambda rows: (-len(rows), -rows[-1]["issued_at"]))[:10]
    if not top:
        return "最近30天没有可计分的警告"
    return "\n".join(["信用分列表", *(f'{i}. {display(rows[-1]["target"])} > {len(rows)}次'
                                        for i, rows in enumerate(top, 1))])


def handle(source, event, admins, now):
    text = event.text.strip()
    match = COMMAND.fullmatch(text)
    if not re.fullmatch(r"[0-9]+@chatroom", event.group_id):
        return None
    if text not in QUERIES and (not match or len(event.mentioned_user_ids) != 1 or event.sender_id not in admins):
        return None
    members = source.group_members(event.group_id)
    if event.sender_id not in members:
        return None
    action = text
    target = actor = None
    days = 1
    if match:
        action, tail = match.groups()
        target_id = event.mentioned_user_ids[0]
        if target_id not in members:
            return None
        contacts = source.conversation_details(list({target_id, event.sender_id}))
        nicknames = source.group_nicknames(event.group_id)

        def person(user_id):
            return {"id": user_id, "username": contacts.get(user_id, {}).get("original_name") or "",
                    "group_nickname": nicknames.get(user_id) or ""}

        target, actor = person(target_id), person(event.sender_id)
        names = {display(target), target["username"], contacts.get(target_id, {}).get("remark") or ""}
        label = next((name for name in sorted(names - {""}, key=len, reverse=True)
                      if tail == name or (tail.startswith(name) and
                          (tail[len(name)].isspace() or tail[len(name)].isdigit()))), None)
        if label is None:
            return INVALID if action == "警告" else None
        suffix = tail[len(label):].strip()
        if suffix:
            if action != "警告":
                return None
            duration = re.fullmatch(r"([0-9]+)\s*天", suffix)
            digits = duration[1].lstrip("0") if duration else ""
            if not digits or len(digits) > 2:
                return INVALID
            days = int(digits)
    directory = ROOT / event.group_id
    active_path, history_path = directory / "秩序管理.json", directory / "秩序管理-history.json"
    cutoff = now - 30 * 86400
    with LOCK:
        active = read(active_path, [])
        history = read(history_path, {"last30day": [], "other": []})
        original = json.dumps([active, history], ensure_ascii=False)
        archived = {r["id"]: r for r in history["other"] + history["last30day"]}
        active = [r for r in active if r["id"] not in archived]
        previous = next((r for r in archived.values() if r.get("ended_by_message_id") == event.message_id), None)
        if previous is not None and action == "撤销":
            return f"[{display(target)}] 已撤销最近一条生效警告。"
        for row in active:
            if row["expires_at"] <= now:
                deactivate(row, row["expires_at"], "系统", "超时过期")
        if action == "警告":
            record = next((r for r in active if r["id"] == event.message_id), None) or archived.get(event.message_id)
            if record is None:
                issued = event.timestamp_ms / 1000
                record = dict(id=event.message_id, target=target, issuer=actor, issued_at=issued,
                              expires_at=issued + days * 86400, active=True,
                              ended_at=None, ended_by=None, reason="未失效", note="")
                if record["expires_at"] <= now:
                    deactivate(record, record["expires_at"], "系统", "超时过期")
                active.append(record)
            count = sum(r["active"] and r["target"]["id"] == target["id"] for r in active)
            response = f'[{display(target)}] 累计警告{count}次，本次警告{stamp(record["expires_at"])} 结束。'
        elif action in {"撤销", "以无暗者之名净化"}:
            candidates = [r for r in active if r["target"]["id"] == target["id"] and r["active"]]
            if action == "撤销":
                latest = max(reversed(candidates), key=lambda r: r["issued_at"], default=None)
                if latest:
                    deactivate(latest, now, actor, "撤销")
                    latest["ended_by_message_id"] = event.message_id
                response = f"[{display(target)}] 已撤销最近一条生效警告。" if latest else "该成员没有可撤销的警告"
            else:
                candidates = [r for r in [*active, *archived.values()]
                              if r["target"]["id"] == target["id"] and r["issued_at"] >= cutoff
                              and (r["active"] or r["reason"] == "超时过期")]
                for row in candidates:
                    deactivate(row, now, actor, "被无暗者净化")
                response = f"[{display(target)}] 已净化{len(candidates)}条警告。"
        elif action == "警告列表":
            response = warning_list([r for r in active if r["active"]])
        else:
            response = ranking([*active, *archived.values()], cutoff)
        archived.update((r["id"], r) for r in active if not r["active"])
        active = [r for r in active if r["active"]]
        ordered = sorted(archived.values(), key=lambda r: (r["issued_at"], r["id"]))
        history = {"last30day": [r for r in ordered if r["issued_at"] >= cutoff],
                   "other": [r for r in ordered if r["issued_at"] < cutoff][-1000:]}
        if json.dumps([active, history], ensure_ascii=False) != original:
            write(history_path, history)
            write(active_path, active)
        return response
