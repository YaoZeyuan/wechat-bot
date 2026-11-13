"""Generate and cache a PDF digest from a day of local chat records."""

import hashlib
import os
import secrets
import shutil
import subprocess
from datetime import datetime, timedelta
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from time import time

import re
import structlog
from markdown_it import MarkdownIt

from ... import global_ai
from ...chat_transcript import BEIJING, format_transcript, read_transcript

PLUGIN_ID = "chat_summary"
METADATA = {"name": "聊天梳理", "description": "按北京时间梳理指定日期的群聊，发送 PDF",
            "default_enabled": False, "version": "1.0.0", "capability": "模型 API、PDF、手机发送"}
CACHE = Path(__file__).resolve().parents[3] / "local-data" / "chat-summary"
TTL = 30 * 86400
MAX_SINGLE_PAGE_HEIGHT = 12000
log = structlog.get_logger("wechat_bot.chat_summary")


def command(text, timestamp_ms):
    count = re.fullmatch(r"梳理\s*([0-9]{1,3})", text.strip())
    if count:
        return int(count[1]), False
    matched = re.fullmatch(r"(重新梳理|梳理)(?:\s*([0-9]{8}))?", text.strip())
    if not matched or (matched[1] == "重新梳理" and not matched[2]):
        return None
    day = (datetime.strptime(matched[2], "%Y%m%d").date() if matched[2]
           else datetime.fromtimestamp(timestamp_ms / 1000, BEIJING).date() - timedelta(days=1))
    return day, matched[1] == "重新梳理"


def render_pdf(markdown, output, *, html_output=None):
    edge = Path(os.environ.get("ProgramFiles(x86)", "")) / "Microsoft/Edge/Application/msedge.exe"
    browser = shutil.which("msedge") or shutil.which("chromium") or shutil.which("google-chrome")
    browser = browser or (str(edge) if edge.is_file() else None)
    if not browser:
        raise RuntimeError("未找到 Edge 或 Chromium，无法生成 PDF")
    output.parent.mkdir(parents=True, exist_ok=True)
    parser = MarkdownIt("commonmark", {"html": False}).enable("table")
    parser.disable("image")
    body = parser.render(markdown)
    with TemporaryDirectory(dir=output.parent) as temp:
        folder = Path(temp)
        page = folder / "summary.html"
        pdf = folder / "summary.pdf"
        page.write_text("""<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<style>
@page { size: 393px 852px; margin: 22px 20px 26px; }
@media screen { body { width: 353px; } }
body { margin: 0; font: 16px/1.65 "Microsoft YaHei", "Noto Sans CJK SC", sans-serif; color: #172033;
overflow-wrap: anywhere; print-color-adjust: exact; }
h1,h2,h3 { break-after: avoid; line-height: 1.4; color: #18315b; }
h1 { font-size: 24px; }
h2 { font-size: 20px; }
h3 { font-size: 17px; }
h2 { border-bottom: 1px solid #d7deea; padding-bottom: .35em; }
blockquote { margin: 1em 0; padding: .2em .9em; border-left: 3px solid #527bb7; background: #edf3fb; }
table { border-collapse: collapse; width: 100%; table-layout: fixed; }
th,td { border: 1px solid #d7deea; padding: .3em .5em; }
pre { white-space: pre-wrap; background: #f0f3f7; padding: .6em; }
a { color: #254f90; }
</style><body><main style="display: flow-root">""" + body + "</main></body></html>", encoding="utf-8")
        if html_output is not None:
            shutil.copyfile(page, html_output)
        browser_args = [browser, "--headless", "--no-first-run", "--disable-background-networking",
                        f"--user-data-dir={folder / 'profile'}"]
        html = page.read_text(encoding="utf-8")
        nonce = secrets.token_hex(16)
        measurement = html.replace("default-src 'none';", f"default-src 'none'; script-src 'nonce-{nonce}';", 1)
        measurement += (
            f'<script nonce="{nonce}">document.fonts.ready.then(() => {{'
            "document.body.dataset.printHeight = Math.ceil(document.querySelector('main').getBoundingClientRect().height);"
            "});</script>"
        )
        page.write_text(measurement, encoding="utf-8")
        result = subprocess.run([*browser_args, "--dump-dom", "--virtual-time-budget=1000", page.as_uri()],
                                capture_output=True, timeout=45, check=True)
        match = re.search(rb'<body data-print-height="(\d+)"', result.stdout)
        if not match:
            raise RuntimeError("浏览器未返回正文高度")
        height = int(match[1]) + 52  # Top and bottom margins plus rounding allowance.
        if height <= MAX_SINGLE_PAGE_HEIGHT:
            html = html.replace("393px 852px", f"393px {height}px", 1)
        page.write_text(html, encoding="utf-8")
        subprocess.run([*browser_args, "--no-pdf-header-footer",
                        f"--print-to-pdf={pdf}", page.as_uri()],
                       capture_output=True, timeout=75, check=True)
        if not pdf.is_file() or pdf.stat().st_size == 0:
            raise RuntimeError("浏览器未生成 PDF")
        pdf.replace(output)


def report_pdf(source, group_id, request_id, day, refresh=False, *, progress=lambda message: None):
    identity = hashlib.sha256(f"{source.account}\0{group_id}".encode()).hexdigest()
    target = CACHE / identity / f"{day.isoformat()}-mobile-long.pdf"
    cutoff = time() - TTL
    for cached in CACHE.glob("*/*.pdf"):
        if cached.stat().st_mtime <= cutoff:
            cached.unlink(missing_ok=True)
    if refresh:
        target.unlink(missing_ok=True)
    if target.is_file():
        progress("命中缓存，使用已有 PDF")
        log.info("chat_summary_cache_hit", msg="使用聊天梳理缓存", request_id=str(request_id), day=str(day))
    else:
        progress("读取当天的完整聊天记录")
        transcript = read_transcript(source, group_id, day, day)
        progress("正在请求模型梳理")
        markdown = global_ai.summarize(transcript, str(request_id))
        progress("模型已返回，正在生成 PDF")
        render_pdf(markdown, target)
        log.info("chat_summary_pdf_ready", msg="聊天梳理 PDF 已生成", request_id=str(request_id), day=str(day))
    return target


def report_filename(source, group_id, day):
    details = source.conversation_details([group_id]).get(group_id, {})
    name = (details.get("original_name") or "").strip() or group_id
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)[:50]
    return f"群聊梳理-{name}-{day:%Y%m%d}.pdf"


def make_report(source, event, request_id, day, refresh, *, progress=lambda message: None):
    from ...sender_jobs import stage_local_pdf

    if isinstance(day, int):
        progress(f"读取指令之前最近 {day} 条聊天记录")
        rows = []
        found = False
        with closing(source.iter_messages(event.group_id)) as messages:
            for row in messages:
                if found:
                    rows.append(row)
                    if len(rows) == day:
                        break
                elif event.message_id == row["messageId"] or event.message_id.endswith(":" + row["messageId"]):
                    found = True
        if not found or not rows:
            raise RuntimeError("未找到指令之前的聊天记录")
        rows.reverse()
        source.populate_sender_names(rows, include_remarks=False, group_id=event.group_id)
        transcript = format_transcript(source, event.group_id, rows, "指令前的聊天记录（北京时间）")
        progress("正在请求模型梳理")
        markdown = global_ai.summarize(transcript, str(request_id))
        progress("模型已返回，正在生成 PDF")
        CACHE.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=CACHE) as temp:
            target = Path(temp) / "summary.pdf"
            render_pdf(markdown, target)
            stage_local_pdf(request_id, target, f"群聊梳理-最近{len(rows)}条.pdf")
    else:
        target = report_pdf(source, event.group_id, request_id, day, refresh, progress=progress)
        stage_local_pdf(request_id, target, report_filename(source, event.group_id, day))
    progress("PDF 已就绪，等待手机发送")
    return {"type": "file", "asset_id": request_id}
