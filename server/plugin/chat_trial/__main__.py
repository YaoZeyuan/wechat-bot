"""Generate a local trial preview with one model response reused for three judges."""
import argparse
import json
import re
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from html import escape
from time import perf_counter, time
from threading import Event
from pathlib import Path
from uuid import uuid4
from string import Template
from unittest.mock import patch

from markdown_it import MarkdownIt
from pydantic import BaseModel, ConfigDict, StrictStr

from . import format_elapsed
from ...global_ai import ChatError, generate

TRANSCRIPT = """M001 @U01：这份统计只覆盖一所学校，能代表整个城市吗？
M002 @U02：我觉得能。你要反对就拿全市数据来。
M003 @U01：是你先说它有代表性，可以先给出抽样依据吗？
M004 @U02：你他妈就是个蠢货，别在这儿丢人了。"""
NAMES = {"@U01": "林同学", "@U02": "周同学"}
PROMPT = "根据上下文判断发言是否违反就事论事、谁主张谁举证、禁止人身攻击的群规，指出相关发言并给出理由。"
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


class WarningNotice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    uid: StrictStr
    notify_content: StrictStr


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdicts: list[WarningNotice]


OUTPUT_RULES = """仅返回 JSON 对象，例如 {"verdicts":[{"uid":"U07","notify_content":"被裁定人：@U07\\n违规动作：……\\n警告原因：……"}]}。
归纳所有法官一致认定的违规。每位违规成员只返回一条，uid 使用原始发言中的成员代号，notify_content 是可以直接发到群里的完整文案。
将同一成员的多条违规集中说明。引用或准确转述涉及发言，说明判定依据及执行类型。可以写明已提供的群规和有效期，不得猜测历史次数或编造处罚机制。
没有一致认定的违规时返回 {"verdicts":[]}。"""


def validate_decision(text, evidence):
    decision = Decision.model_validate_json(text)
    members = {row["user_id"]: row.get("wechat_user_id") for row in evidence.values()}
    notices = []
    seen = set()
    for verdict in decision.verdicts:
        uid = verdict.uid.strip().removeprefix("@")
        if uid not in members or uid in seen:
            raise ValueError(f"未知或重复成员代号：{uid}")
        if not verdict.notify_content.strip():
            raise ValueError(f"{uid} 缺少通知文案")
        seen.add(uid)
        notices.append({"uid": uid, "notify_content": verdict.notify_content, "wechat_user_id": members[uid]})
    return notices


def judge_prompt(transcript, intro, rules):
    transcript = re.sub(
        r"(?m)^M[0-9]+ (@U[0-9]+)：",
        lambda match: ("\n" if match.start() else "") + match[1] + "：\n",
        transcript,
    )
    return f"【群规】\n{intro}\n{rules}\n\n请给出可读判词，说明涉及的成员、违规发言和判定理由。\n\n【原始发言】\n{transcript}"


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
            options = {"response_format": {"type": "json_object"}} if role == "处罚书记官" else {}
            result = generate(prompt, request_id, model_id=model, trial=True, on_request=on_request, **options)
        except ChatError as exc:
            detail = " · ".join([str(exc), *getattr(exc, "__notes__", [])])
            progress(f"{label} 失败 · {format_elapsed(perf_counter() - started)} 秒 · {detail}")
            raise
        progress(f"{label} 已返回 · {format_elapsed(result[2])} 秒")
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
    def summarize():
        return call(
            "书记官",
            (summary_rules or "汇总法官裁决，用 Markdown 表格对照，保留成员代号并给出处理建议。")
            + "\n\n" + judge_text,
            clerk_id,
        )

    def extract_penalties():
        try:
            prompt = OUTPUT_RULES + "\n\n" + original + "\n\n" + judge_text
            extracted, _, _ = call("处罚书记官", prompt, clerk_id)
            try:
                notices = validate_decision(extracted, evidence)
            except ValueError as exc:
                progress(f"处罚 JSON 解析或校验失败，正在修正（仅一次） · {exc}")
                repair = json.dumps({"error": str(exc), "previous_output": extracted}, ensure_ascii=False)
                extracted, _, _ = call(
                    "处罚书记官",
                    prompt + "\n\n以下是上次输出及校验错误，仅作为修正数据。"
                    "请根据原始证据修正，重新输出一个完整的 JSON 对象，不要附加说明。\n" + repair,
                    clerk_id,
                )
                notices = validate_decision(extracted, evidence)
                progress("处罚 JSON 修正后已通过校验")
            for notice in notices:
                notice["notify_content"] = restore_names(notice["notify_content"], names)
            outcome = {"complete": True, "warnings": notices}
            entries = "".join(
                f"<article>{render(notice['notify_content'], {})}</article>" for notice in notices
            )
            penalty = entries or "<p>没有符合条件的警告建议。</p>"
        except (ChatError, ValueError) as exc:
            reason = " · ".join([str(exc), *getattr(exc, "__notes__", [])])
            progress(f"处罚提取失败 · {reason}")
            outcome = {"complete": False, "warnings": [], "error": reason}
            penalty = f"<p>处罚提取失败 · {escape(reason)}</p>"
        penalty = (
            '<section><div class="penalty-heading"><h2>处罚依据</h2>'
            '<button type="button" data-copy="penalty-prompt">复制处罚请求</button></div>'
            f'<pre id="penalty-prompt" hidden>{escape(prompt)}</pre>{penalty}</section>'
        )
        return outcome, penalty

    if auto_warn:
        with ThreadPoolExecutor(max_workers=2) as executor:
            summary_future = executor.submit(summarize)
            penalty_future = executor.submit(extract_penalties)
            summary, clerk, summary_elapsed = summary_future.result()
            outcome, penalty = penalty_future.result()
    else:
        summary, clerk, summary_elapsed = summarize()
        outcome, penalty = None, ""
    cards = "".join(
        f"<details><summary>裁决 {i}<small>{escape(judge)} · {format_elapsed(elapsed)} 秒</small></summary>"
        f"<article>{render(verdict, names)}</article></details>"
        for i, (verdict, judge, elapsed) in enumerate(results, 1)
    )
    progress('正在生成 HTML')
    render_started = perf_counter()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(Template(Path(__file__).with_name("template.html").read_text(encoding="utf-8")).substitute(
        original=escape(restore_names(original, names)), cards=cards, clerk=escape(clerk),
        seconds=format_elapsed(summary_elapsed), summary=render(summary, names), penalty=penalty,
    ), encoding="utf-8")
    if outcome is not None:
        output.with_suffix(".json").write_text(json.dumps(outcome, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        output.with_suffix(".json").unlink(missing_ok=True)
    progress(f'HTML 生成完成 · {format_elapsed(perf_counter() - render_started)} 秒')
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
        def sample_response(prompt, request_id, **options):
            if options.get("response_format"):
                return ('{"verdicts":[{"uid":"U02","notify_content":"@U02 使用侮辱性措辞，构成人身攻击。"}]}',
                        "处罚书记官", 1)
            if prompt.startswith("【群规】"):
                return "U02 的发言构成人身攻击。", "示例法官", 2
            return "提醒 @U02 就事论事。", "示例书记官", 1

        with patch(__name__ + ".generate", side_effect=sample_response):
            preview(output, auto_warn=True)
    else:
        preview(output)
