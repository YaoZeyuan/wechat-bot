"""Generate a local trial preview with one model response reused for three judges."""
import argparse
import json
import re
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from html import escape
from time import time
from pathlib import Path
from uuid import uuid4
from string import Template
from unittest.mock import patch

from markdown_it import MarkdownIt
from pydantic import BaseModel, ConfigDict, StrictBool, StrictStr

from ...global_ai import ChatError, generate

TRANSCRIPT = """M001 @U01：这份统计只覆盖一所学校，能代表整个城市吗？
M002 @U02：我觉得能。你要反对就拿全市数据来。
M003 @U01：是你先说它有代表性，可以先给出抽样依据吗？
M004 @U02：你他妈就是个蠢货，别在这儿丢人了。"""
NAMES = {"@U01": "林同学", "@U02": "周同学"}
PROMPT = "根据上下文判断发言是否违反就事论事、谁主张谁举证、禁止人身攻击的群规，引用消息编号并给出理由。"
MARKDOWN = MarkdownIt("commonmark", {"html": False}).enable("table").disable("image")


def restore_names(text, names):
    def replace(match):
        token = match[0]
        name = names.get("@" + token.lstrip("@").upper())
        if name is None:
            return token
        return ("@" if token.startswith("@") else "") + name

    return re.sub(r"(?<![A-Za-z0-9_])@?U[0-9]+(?![A-Za-z0-9_])", replace, text, flags=re.I)


def render(text, names):
    return MARKDOWN.render(restore_names(text, names))


class Violation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message_id: StrictStr
    reason: StrictStr


class Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: StrictStr
    violation: StrictBool
    violations: list[Violation]


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdicts: list[Verdict]


OUTPUT_RULES = """【固定输出格式】
仅输出 JSON 对象，例如 {"verdicts":[{"user_id":"U07","violation":true,"violations":[{"message_id":"M042","reason":"结合原文说明违反的群规"}]}]}。
使用原始发言里的成员和消息编号。每位成员最多出现一次，每条违规发言必须有具体原因。没有违规发言时使用空列表。只列出明确违规的成员，每项 violation 必须为 true。没有违规时返回 {"verdicts":[]}。不要输出代码围栏或额外文字。"""


def validate_decision(text, evidence):
    decision = Decision.model_validate_json(text)
    users = set()
    findings = {}
    for verdict in decision.verdicts:
        if verdict.user_id in users or not verdict.violation or not verdict.violations:
            raise ValueError("成员重复或违规状态与明细不一致")
        if not any(row["user_id"] == verdict.user_id for row in evidence.values()):
            raise ValueError("未知成员代号")
        users.add(verdict.user_id)
        for violation in verdict.violations:
            row = evidence.get(violation.message_id)
            if not row or row["user_id"] != verdict.user_id:
                raise ValueError("违规发言编号不存在或作者不匹配")
            if violation.message_id in findings or not violation.reason.strip():
                raise ValueError("违规发言重复或缺少原因")
            findings[violation.message_id] = violation.reason
    return findings


def judge_prompt(transcript, intro, rules):
    return f"【群规】\n{intro}\n{rules}\n\n{OUTPUT_RULES}\n\n【原始发言】\n{transcript}"


def preview(output, transcript=TRANSCRIPT, names=NAMES, *, real_group=False, rules=PROMPT, intro='', summary_rules='', judges=(), clerk_id='', evidence=None, progress=lambda message: None):
    if evidence is None:
        evidence = {
            message_id: {"user_id": user_id, "text": text}
            for message_id, user_id, text in re.findall(r"^(M[0-9]+) @(U[0-9]+)：(.*)$", TRANSCRIPT, re.M)
        }
    original = judge_prompt(transcript, intro, rules)
    def call(role, prompt, model=None):
        request_id = str(uuid4())
        label = f"{role} · {model or '默认模型'}（请求 {request_id}）"
        progress(f"{label} 正在调用")
        try:
            result = generate(prompt, request_id, model_id=model, trial=True)
        except ChatError as exc:
            detail = " · ".join([str(exc), *getattr(exc, "__notes__", [])])
            progress(f"{label} 失败 · {detail}")
            raise
        progress(f"{label} 已返回 · {result[2]:.2f} 秒")
        return result

    if len(judges) > 3:
        raise ValueError("法官模型最多配置三个")
    if judges:
        with ThreadPoolExecutor(max_workers=len(judges)) as executor:
            results = list(executor.map(lambda model: call('法官', original, model), judges))
    else:
        results = [call('法官', original)] * 3
    findings = []
    panels = []
    valid = True
    for verdict, judge, elapsed in results:
        try:
            items = validate_decision(verdict, evidence)
        except ValueError as exc:
            valid = False
            progress(f"法官 · {judge} 裁决格式不合格 · {exc}")
            panels.append("裁决格式不合格。")
            continue
        findings.append(items)
        panels.append("\n\n".join(
            f"{evidence[mid]['user_id']} · {mid}：{reason}" for mid, reason in items.items()
        ) or "没有明确判定违规的发言。")

    warnings = {}
    if valid and findings:
        for mid, row in evidence.items():
            if all(mid in judge for judge in findings):
                warnings.setdefault(row["user_id"], []).append({
                    "message_id": mid, **row, "reasons": [judge[mid] for judge in findings],
                })
    outcome = {"complete": valid, "warnings": [
        {"user_id": user, "violations": rows} for user, rows in warnings.items()
    ]}
    summary, clerk, summary_elapsed = call(
        '书记官',
        (summary_rules or "汇总法官裁决，用 Markdown 表格对照，保留成员代号并给出处理建议。")
        + "\n\n以程序终审结果为准。裁决不完整时不得建议自动警告。\n"
        + json.dumps({"complete": valid, "warnings": [
            {"user_id": user, "violations": [
                {key: row[key] for key in ("message_id", "text", "reasons")} for row in rows
            ]} for user, rows in warnings.items()
        ]}, ensure_ascii=False)
        + "\n\n" + "\n\n".join(f"法官 {i}\n{panel}" for i, panel in enumerate(panels, 1)),
        clerk_id,
    )
    cards = "".join(
        f"<details><summary>裁决 {i}<small>{escape(judge)} · {int(elapsed)} 秒</small></summary>"
        f"<article>{render(verdict, names)}</article></details>"
        for i, ((_, judge, elapsed), verdict) in enumerate(zip(results, panels), 1)
    )
    progress('正在生成 HTML')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(Template(Path(__file__).with_name("template.html").read_text(encoding="utf-8")).substitute(
        original=escape(restore_names(original, names)), cards=cards, clerk=escape(clerk),
        seconds=int(summary_elapsed), summary=render(summary, names),
    ), encoding="utf-8")
    output.with_suffix(".json").write_text(json.dumps(outcome, ensure_ascii=False, indent=2), encoding="utf-8")


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
    evidence = {}
    for index, row in enumerate(rows, 1):
        sender = row["sender"]
        user_id = sender.get("username") or ""
        token = ids.setdefault(user_id, f"@U{len(ids) + 1:02}")
        names[token] = sender.get("display_name") or "未知成员"
        body = row.get("text") or row.get("file_name") or (row.get("link") or {}).get("title") or f"[{row.get('message_label') or '非文本消息'}]"
        message_id = f"M{index:03}"
        lines.append(f"{message_id} {token}：{body}")
        if user_id:
            evidence[message_id] = {"user_id": token[1:], "text": body, "wechat_user_id": user_id,
                                    "source_message_id": row["messageId"]}
    return "\n".join(lines), names, evidence


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
        transcript, names, evidence = read_group(args.group, args.hours or settings.trial_hours, args.limit or settings.trial_limit)
        preview(output, transcript, names, real_group=True, evidence=evidence, rules=settings.trial_prompt or PROMPT,
                intro=settings.trial_intro, summary_rules=settings.trial_summary,
                judges=[line.strip() for line in settings.trial_judges.splitlines() if line.strip()],
                clerk_id=settings.trial_clerk)
    elif args.sample:
        with patch(__name__ + ".generate", side_effect=[
            ('{"verdicts":[{"user_id":"U02","violation":true,"violations":[{"message_id":"M004","reason":"违反禁止人身攻击的群规"}]}]}' , "示例法官", 2),
            ("| 消息 | 裁决 | 建议 |\n| --- | --- | --- |\n| M004 | 人身攻击 | 提醒 @U02 就事论事 |", "示例书记官", 1),
        ]):
            preview(output)
    else:
        preview(output)
