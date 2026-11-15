"""Generate a local trial preview with one model response reused for three judges."""
import argparse
import json
import re
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from html import escape
from time import perf_counter, time
from math import ceil
from threading import Event
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
    excerpts: list[StrictStr]


class Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: StrictStr
    violation: StrictBool
    violations: list[Violation]


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdicts: list[Verdict]


OUTPUT_RULES = """【固定输出格式】
仅输出 JSON 对象，例如 {"verdicts":[{"user_id":"U07","violation":true,"violations":[{"message_id":"M042","reason":"结合原文说明违反的群规","excerpts":["法官判词中的原文摘录"]}}]}]}。
使用原始发言里的成员和消息编号。每位成员最多出现一次，每条违规发言必须有具体原因。没有违规发言时使用空列表。只列出明确违规的成员，每项 violation 必须为 true。没有违规时返回 {"verdicts":[]}。不要输出代码围栏或额外文字。"""


def validate_decision(text, evidence, verdicts):
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
            if len(violation.excerpts) != len(verdicts) or any(
                not excerpt.strip() or excerpt not in verdict
                for excerpt, verdict in zip(violation.excerpts, verdicts)
            ):
                raise ValueError("各法官判词摘录缺失或无法核对")
            findings[violation.message_id] = {"reason": violation.reason, "excerpts": violation.excerpts}
    return findings


def judge_prompt(transcript, intro, rules):
    return f"【群规】\n{intro}\n{rules}\n\n请用可读判词说明结论，并引用对应成员和消息编号。\n\n【原始发言】\n{transcript}"


def preview(output, transcript=TRANSCRIPT, names=NAMES, *, real_group=False, rules=PROMPT, intro='', summary_rules='', judges=(), clerk_id='', evidence=None, auto_warn=False, request_prefix='', cache_dir=None, on_judges_started=None, progress=lambda message: None):
    if evidence is None:
        evidence = {
            message_id: {"user_id": user_id, "text": text}
            for message_id, user_id, text in re.findall(r"^(M[0-9]+) @(U[0-9]+)：(.*)$", TRANSCRIPT, re.M)
        }
    original = judge_prompt(transcript, intro, rules)
    def call(role, prompt, model=None, *, on_request=None):
        from hashlib import sha256

        saved = None
        if cache_dir is not None and role != "处罚书记官":
            key = sha256(json.dumps([role, model, prompt]).encode()).hexdigest()
            saved = cache_dir / f"{key}.json"
            if saved.exists():
                progress(f"{role} · {model or '默认模型'} 已复用上次结果")
                return json.loads(saved.read_text(encoding="utf-8"))
        request_id = f"{request_prefix}:{uuid4()}" if request_prefix else str(uuid4())
        label = f"{role} · {model or '默认模型'}（请求 {request_id}）"
        progress(f"{label} 正在调用")
        started = perf_counter()
        try:
            result = generate(prompt, request_id, model_id=model, trial=True, on_request=on_request)
        except ChatError as exc:
            detail = " · ".join([str(exc), *getattr(exc, "__notes__", [])])
            progress(f"{label} 失败 · {ceil(perf_counter() - started)} 秒 · {detail}")
            raise
        progress(f"{label} 已返回 · {ceil(result[2])} 秒")
        if saved is not None:
            from ..group_order import write

            write(saved, result)
        return result

    if len(judges) > 3:
        raise ValueError("法官模型最多配置三个")
    models = judges or (None,)
    if judges or on_judges_started:
        launched = Event()
        def judge(model):
            return call("法官", original, model, on_request=launched.set)

        with ThreadPoolExecutor(max_workers=len(models)) as executor:
            futures = [executor.submit(judge, model) for model in models]
            if on_judges_started:
                while not launched.wait(0.05) and not all(future.done() for future in futures):
                    pass
                if launched.is_set():
                    on_judges_started()
            results = [future.result() for future in futures]
    else:
        results = [call("法官", original)]
    if not judges:
        results *= 3
    verdicts = [result[0] for result in results]
    judge_text = "\n\n".join(f"法官 {i}\n{text}" for i, text in enumerate(verdicts, 1))
    summary, clerk, summary_elapsed = call(
        "书记官",
        (summary_rules or "汇总法官裁决，用 Markdown 表格对照，保留成员代号并给出处理建议。")
        + "\n\n" + judge_text,
        clerk_id,
    )
    outcome = None
    penalty = ""
    if auto_warn:
        try:
            extracted, _, _ = call(
                "处罚书记官",
                OUTPUT_RULES + "\n只提取每位法官均明确认定违规的同一条消息。"
                "reason 会公开发送给群成员，请直接引用或描述违规发言本身，原因中不要出现 M007 等消息编号。"
                "每条违规需提供 excerpts 数组，按法官顺序逐字引用支持这一裁决的原文片段。"
                "\n\n" + original + "\n\n" + judge_text,
                clerk_id,
            )
            findings = validate_decision(extracted, evidence, verdicts)
            by_member = {}
            for mid, finding in findings.items():
                row = evidence[mid]
                by_member.setdefault(row["user_id"], []).append({"message_id": mid, **row, **finding})
            outcome = {"complete": True, "warnings": [
                {"user_id": user, "violations": rows} for user, rows in by_member.items()
            ]}
            entries = "".join(
                f"<h3>{escape(names.get('@' + user, user))} · 1 次警告建议</h3><ul>"
                + "".join(f"<li><blockquote>{escape(row['text'])}</blockquote><p>{escape(row['reason'])}</p></li>"
                          for row in rows) + "</ul>"
                for user, rows in by_member.items()
            )
            penalty = entries or "<p>没有符合条件的警告建议。</p>"
        except (ChatError, ValueError) as exc:
            reason = " · ".join([str(exc), *getattr(exc, "__notes__", [])])
            progress(f"处罚提取失败 · {reason}")
            outcome = {"complete": False, "warnings": [], "error": reason}
            penalty = f"<p>处罚提取失败 · {escape(reason)}</p>"
        penalty = "<section><h2>处罚依据</h2>" + penalty + "</section>"
    cards = "".join(
        f"<details><summary>裁决 {i}<small>{escape(judge)} · {ceil(elapsed)} 秒</small></summary>"
        f"<article>{render(verdict, names)}</article></details>"
        for i, (verdict, judge, elapsed) in enumerate(results, 1)
    )
    progress('正在生成 HTML')
    render_started = perf_counter()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(Template(Path(__file__).with_name("template.html").read_text(encoding="utf-8")).substitute(
        original=escape(restore_names(original, names)), cards=cards, clerk=escape(clerk),
        seconds=ceil(summary_elapsed), summary=render(summary, names), penalty=penalty,
    ), encoding="utf-8")
    if outcome is not None:
        output.with_suffix(".json").write_text(json.dumps(outcome, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        output.with_suffix(".json").unlink(missing_ok=True)
    progress(f'HTML 生成完成 · {ceil(perf_counter() - render_started)} 秒')
    return outcome


def read_group(group_id, hours, limit, *, before=None):
    from ...reader import history_source

    source = history_source()
    now = int(time()) if before is None else before.timestamp_ms // 1000
    with closing(source.iter_messages(group_id, limit=limit if before is None else -1, time_range=(now - hours * 3600, now + 1))) as messages:
        from itertools import islice

        rows = list(islice((row for row in messages if before is None or (
            row["timestampMs"] < before.timestamp_ms
            and not before.message_id.endswith(":" + row["messageId"])
        )), limit))
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
                clerk_id=settings.trial_clerk, auto_warn=settings.trial_auto_warn)
    elif args.sample:
        with patch(__name__ + ".generate", side_effect=[
            ("U02 的 M004 是人身攻击。", "示例法官", 2),
            ("| 消息 | 裁决 | 建议 |\n| --- | --- | --- |\n| M004 | 人身攻击 | 提醒 @U02 就事论事 |", "示例书记官", 1),
            ('{"verdicts":[{"user_id":"U02","violation":true,"violations":[{"message_id":"M004","reason":"人身攻击","excerpts":["U02 的 M004 是人身攻击。","U02 的 M004 是人身攻击。","U02 的 M004 是人身攻击。"]}]}]}', "处罚书记官", 1),
        ]):
            preview(output, auto_warn=True)
    else:
        preview(output)
