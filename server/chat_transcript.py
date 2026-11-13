"""Read and format local chat transcripts for download or summarization."""

from datetime import date, datetime, time, timedelta, timezone

BEIJING = timezone(timedelta(hours=8))
LABELS = {3: "图片", 34: "语音", 42: "名片", 43: "视频", 47: "表情",
          48: "位置", 49: "链接或文件", 10000: "系统消息", 10002: "系统消息"}


def read_transcript(source, session_id: str, start: date, end: date) -> str:
    """Return Markdown for an inclusive range of Beijing calendar dates."""
    start_seconds = int(datetime.combine(start, time.min, BEIJING).timestamp())
    end_seconds = int(datetime.combine(end, time.min, BEIJING).timestamp()) + 86400
    rows = source.messages_in_range(session_id, start_seconds=start_seconds, end_seconds=end_seconds)
    return format_transcript(source, session_id, rows, f"北京时间 {start.isoformat()} 至 {end.isoformat()}")


def format_transcript(source, session_id, rows, scope):
    details = source.conversation_details([session_id]).get(session_id, {})
    title = details.get("original_name") or "聊天记录"
    lines = [f"# {title}", "", f"{scope} · {len(rows)} 条", ""]
    for row in rows:
        stamp = datetime.fromtimestamp(row["timestampMs"] / 1000, BEIJING)
        sender = row["sender"]
        name = sender.get("display_name") or "未知发送者"
        heading = "" if row.get("message_label") == "系统消息" else f"{name} · "
        lines.extend([f"### {heading}{stamp:%Y-%m-%d %H:%M:%S}", ""])
        if row.get("link"):
            link = row["link"]
            lines.extend([f'[链接] {link["title"] or "网页链接"}', "", link["description"], "", f'<{link["url"]}>'])
        elif row.get("file_name"):
            lines.append(f'[文件] {row["file_name"]}')
        elif row.get("message_label") is not None:
            label = row["message_label"]
            lines.append(" ".join(filter(None, (f"[{label}]" if label else "", row["text"]))) or "（空白文本）")
            if row.get("forwarded"):
                lines.append("")
                for entry in row["forwarded"]:
                    marker = ">" * entry["depth"]
                    heading = " · ".join(filter(None, (entry["sender"], entry["time"])))
                    if heading:
                        lines.append(f"{marker} {heading}")
                    lines.extend(f"{marker} {line}" for line in entry["text"].split("\n"))
                    for depth, quote in enumerate(entry["quotes"], 1):
                        sender = f'{quote["sender"]}：' if quote.get("sender") else ""
                        lines.extend(f'{marker}{">" * depth} {line}' for line in (sender + quote["text"]).split("\n"))
                    lines.append(marker)
        elif row["kind"] == "quote":
            lines.append(f'[引用消息] {row["text"]}' if row["text"] else "[引用消息]")
        elif row["kind"] == "text":
            lines.append(row["text"] or "（空白文本）")
        else:
            lines.append(f'[{LABELS.get(row["type_code"] & 0xFFFFFFFF, "其他消息")}]')
        if row.get("quotes"):
            lines.append("")
            for depth, quote in enumerate(row["quotes"][:3], 1):
                sender = f'{quote["sender"]}：' if quote.get("sender") else ""
                marker = ">" * depth
                lines.extend(f"{marker} {line}" for line in (sender + quote["text"]).split("\n"))
        lines.append("")
    return "\n".join(lines)
