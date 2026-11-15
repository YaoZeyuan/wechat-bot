"""Generate a local trial preview with one model response reused for three judges."""
import argparse
import re
from contextlib import closing
from html import escape
from time import time
from pathlib import Path
from uuid import uuid4
from string import Template
from unittest.mock import patch

from markdown_it import MarkdownIt

from ...global_ai import generate

TRANSCRIPT = """M001 @U01：这份统计只覆盖一所学校，能代表整个城市吗？
M002 @U02：我觉得能。你要反对就拿全市数据来。
M003 @U01：是你先说它有代表性，可以先给出抽样依据吗？
M004 @U02：你他妈就是个蠢货，别在这儿丢人了。"""
NAMES = {"@U01": "林同学", "@U02": "周同学"}
PROMPT = "根据上下文判断发言是否违反就事论事、谁主张谁举证、禁止人身攻击的群规，引用消息编号并给出理由。"
MARKDOWN = MarkdownIt("commonmark", {"html": False}).enable("table").disable("image")


def render(text, names):
    text = re.sub(r"@U[0-9]+", lambda match: names.get(match[0], match[0]), text)
    return MARKDOWN.render(text)


def preview(output, transcript=TRANSCRIPT, names=NAMES, *, real_group=False, rules=PROMPT):
    original = f"【群规】\n{rules}\n\n【原始发言】\n{transcript}"
    verdict, judge, elapsed = generate(original, str(uuid4()))
    summary, clerk, summary_elapsed = generate(
        "汇总以下三位法官的裁决。用 Markdown 表格对照，保留成员代号，给出处理建议。\n\n"
        + "\n\n".join(f"法官 {i}\n{verdict}" for i in range(1, 4)),
        str(uuid4()),
    )
    cards = "".join(
        f"<details><summary>裁决 {i}<small>{escape(judge)} · {int(elapsed)} 秒</small></summary>"
        f"<article>{render(verdict, names)}</article></details>" for i in range(1, 4)
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(Template(Path(__file__).with_name("template.html").read_text(encoding="utf-8")).substitute(
        source_label='真实群聊' if real_group else '示例聊天', original=escape(original), cards=cards, clerk=escape(clerk),
        seconds=int(summary_elapsed), summary=render(summary, names),
    ), encoding="utf-8")


def read_group(group_id, hours, limit):
    from ...reader import history_source

    source = history_source()
    now = int(time())
    with closing(source.iter_messages(group_id, limit=limit, time_range=(now - hours * 3600, now + 1))) as messages:
        from itertools import islice

        rows = list(islice(messages, limit))
    if not rows:
        raise ValueError("指定范围内没有群聊记录")
    rows.reverse()
    source.populate_sender_names(rows, include_remarks=False, group_id=group_id)
    ids = {}
    names = {}
    lines = []
    for index, row in enumerate(rows, 1):
        sender = row["sender"]
        user_id = sender.get("username") or ""
        token = ids.setdefault(user_id, f"@U{len(ids) + 1:02}")
        names[token] = sender.get("display_name") or "未知成员"
        body = row.get("text") or row.get("file_name") or (row.get("link") or {}).get("title") or f"[{row.get('message_label') or '非文本消息'}]"
        lines.append(f"M{index:03} {token}：{body}")
    return "\n".join(lines), names


if __name__ == "__main__":
    from ...logging_setup import configure_logging

    parser = argparse.ArgumentParser(description="在本地生成群聊审判 HTML")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--sample", action="store_true")
    mode.add_argument("--group", metavar="群ID")
    parser.add_argument("--hours", type=int, choices=range(1, 25), metavar="1–24")
    parser.add_argument("--limit", type=int, choices=range(1, 5001), metavar="1–5000")
    args = parser.parse_args()
    configure_logging()
    output = Path("local-data/chat-trial-preview/index.html")
    if args.group:
        from ...reader import automation
        from ...group_plugins import config
        from . import PLUGIN_ID

        settings = config(args.group, PLUGIN_ID, automation.group_plugins(args.group))
        transcript, names = read_group(args.group, args.hours or settings.trial_hours, args.limit or settings.trial_limit)
        preview(output, transcript, names, real_group=True, rules=settings.trial_prompt or PROMPT)
    elif args.sample:
        with patch(__name__ + ".generate", side_effect=[
            ("M004 对 @U01 使用辱骂，违反禁止人身攻击的群规。", "示例法官", 2),
            ("| 消息 | 裁决 | 建议 |\n| --- | --- | --- |\n| M004 | 人身攻击 | 提醒 @U02 就事论事 |", "示例书记官", 1),
        ]):
            preview(output)
    else:
        preview(output)
