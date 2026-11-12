"""Generate and cache a PDF digest from a day of local chat records."""

import hashlib
import os
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from time import time

import re
import structlog
from markdown_it import MarkdownIt

from ... import global_ai
from ...chat_transcript import BEIJING, read_transcript

PLUGIN_ID = "chat_summary"
METADATA = {"name": "聊天梳理", "description": "按北京时间梳理指定日期的群聊，发送 PDF",
            "default_enabled": False, "version": "1.0.0", "capability": "模型 API、PDF、手机发送"}
CACHE = Path(__file__).resolve().parents[3] / "local-data" / "chat-summary"
TTL = 30 * 86400
log = structlog.get_logger("wechat_bot.chat_summary")


def command(text, timestamp_ms):
    matched = re.fullmatch(r"(重新梳理|梳理)(?:\s*([0-9]{8}))?", text.strip())
    if not matched or (matched[1] == "重新梳理" and not matched[2]):
        return None
    day = (datetime.strptime(matched[2], "%Y%m%d").date() if matched[2]
           else datetime.fromtimestamp(timestamp_ms / 1000, BEIJING).date() - timedelta(days=1))
    return day, matched[1] == "重新梳理"


def render_pdf(markdown, output):
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
@page { size: A4; margin: 17mm 17mm 19mm; }
body { font: 14px/1.75 "Microsoft YaHei", "Noto Sans CJK SC", sans-serif; color: #172033;
overflow-wrap: anywhere; print-color-adjust: exact; }
h1,h2,h3 { break-after: avoid; line-height: 1.4; color: #18315b; }
h2 { border-bottom: 1px solid #d7deea; padding-bottom: .35em; }
blockquote { margin: 1em 0; padding: .2em .9em; border-left: 3px solid #527bb7; background: #edf3fb; }
table { border-collapse: collapse; width: 100%; }
th,td { border: 1px solid #d7deea; padding: .3em .5em; }
pre { white-space: pre-wrap; background: #f0f3f7; padding: .6em; }
a { color: #254f90; }
</style><body>""" + body + "</body></html>", encoding="utf-8")
        subprocess.run([browser, "--headless", "--no-first-run", "--disable-background-networking",
                        f"--user-data-dir={folder / 'profile'}", "--no-pdf-header-footer",
                        f"--print-to-pdf={pdf}", page.as_uri()],
                       capture_output=True, timeout=75, check=True)
        if not pdf.is_file() or pdf.stat().st_size == 0:
            raise RuntimeError("浏览器未生成 PDF")
        pdf.replace(output)


def make_report(source, event, request_id, day, refresh):
    identity = hashlib.sha256(f"{source.account}\0{event.group_id}".encode()).hexdigest()
    target = CACHE / identity / f"{day.isoformat()}.pdf"
    cutoff = time() - TTL
    for cached in CACHE.glob("*/*.pdf"):
        if cached.stat().st_mtime <= cutoff:
            cached.unlink(missing_ok=True)
    if refresh:
        target.unlink(missing_ok=True)
    if target.is_file():
        log.info("chat_summary_cache_hit", msg="使用聊天梳理缓存", request_id=str(request_id), day=str(day))
    else:
        transcript = read_transcript(source, event.group_id, day, day)
        markdown = global_ai.summarize(transcript, str(request_id))
        render_pdf(markdown, target)
        log.info("chat_summary_pdf_ready", msg="聊天梳理 PDF 已生成", request_id=str(request_id), day=str(day))
    from ...sender_jobs import stage_local_pdf

    stage_local_pdf(request_id, target, f"群聊梳理-{day:%Y%m%d}.pdf")
    return {"type": "file", "asset_id": request_id}
