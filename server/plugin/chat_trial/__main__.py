"""Generate a local trial preview with one model response reused for three judges."""
from html import escape
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


def render(text):
    for token, name in NAMES.items():
        text = text.replace(token, name)
    return MARKDOWN.render(text)


def preview(output):
    original = f"【群规】\n{PROMPT}\n\n【原始发言】\n{TRANSCRIPT}"
    verdict, judge, elapsed = generate(original, str(uuid4()))
    summary, clerk, summary_elapsed = generate(
        "汇总以下三位法官的裁决。用 Markdown 表格对照，保留成员代号，给出处理建议。\n\n"
        + "\n\n".join(f"法官 {i}\n{verdict}" for i in range(1, 4)),
        str(uuid4()),
    )
    cards = "".join(
        f"<details><summary>裁决 {i}<small>{escape(judge)} · {int(elapsed)} 秒</small></summary>"
        f"<article>{render(verdict)}</article></details>" for i in range(1, 4)
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(Template(Path(__file__).with_name("template.html").read_text(encoding="utf-8")).substitute(
        original=escape(original), cards=cards, clerk=escape(clerk),
        seconds=int(summary_elapsed), summary=render(summary),
    ), encoding="utf-8")


if __name__ == "__main__":
    import sys
    from ...logging_setup import configure_logging

    configure_logging()
    output = Path("local-data/chat-trial-preview/index.html")
    if "--sample" in sys.argv:
        with patch(__name__ + ".generate", side_effect=[
            ("M004 对 @U01 使用辱骂，违反禁止人身攻击的群规。", "示例法官", 2),
            ("| 消息 | 裁决 | 建议 |\n| --- | --- | --- |\n| M004 | 人身攻击 | 提醒 @U02 就事论事 |", "示例书记官", 1),
        ]):
            preview(output)
    else:
        preview(output)
