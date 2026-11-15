"""Run a group trial and deliver warning notices before recording penalties."""
import json
from threading import Lock
from pathlib import Path
from time import perf_counter, time
from math import ceil
from uuid import NAMESPACE_URL, uuid5

import structlog

from ..group_order import record_trial_warning, write
from .__main__ import preview, read_group

ROOT = Path(__file__).resolve().parents[3] / "local-data" / "chat-trial-runs"
log = structlog.get_logger("wechat_bot.trial")


def dispatch(source, event, settings, *, retry=False):
    from ...group_plugins import robot_wxid
    from ...sender_jobs import Job, get_job, queue_job, run, save, stage_local_report

    group = event.group_id
    if not group.endswith("@chatroom") or not group.removesuffix("@chatroom").isdigit():
        return
    trial_id = uuid5(NAMESPACE_URL, f"trial:{group}:{event.message_id}")
    folder = ROOT / group
    state_path = folder / f"{trial_id}.state.json"
    if state_path.exists() and not retry:
        return
    last_path = folder / "last.json"
    last = json.loads(last_path.read_text(encoding="utf-8")) if last_path.exists() else {}
    details = source.conversation_details([group, event.sender_id])
    nicknames = source.group_nicknames(group)
    sender_name = nicknames.get(event.sender_id) or details.get(event.sender_id, {}).get("original_name") or event.sender_id
    info = details.get(group, {})
    name = info.get("remark") or info.get("original_name") or group

    history_id = uuid5(trial_id, "start")
    resume_path = folder / f"{history_id}.resume.json"

    def send(key, text='', mentions=None, *, asset_id=None, reserve=False):
        job_id = uuid5(trial_id, key)
        job = Job(version=1, request_id=job_id, mode="send", target={"kind": "group", "id": group, "name": name},
                  trigger_text=event.text, messages=[{"type": "file", "asset_id": asset_id}] if asset_id else
                  [{"type": "text", "text": text, "mentions": mentions or []}])
        result, inserted = queue_job(job, strict=False, source="chat_trial")
        if reserve:
            if inserted:
                result["status"] = "running"
                save(str(job_id), result)
            return result["status"]
        if retry and not inserted and result["status"] == "failed" and not result.get("text_sends"):
            result.update(status="queued", items=["pending"])
            save(str(job_id), result)
            inserted = True
        if inserted:
            run(job, result)
        return get_job(job_id)["status"]

    wait = settings.trial_cooldown * 60 - (time() - last.get("time", 0))
    if wait > 0 and not retry:
        send("cooldown", f"距离上轮{last['sender']} 发起的审判间隔不足{settings.trial_cooldown}分钟，请等待{ceil(wait / 60)}分钟后再进行")
        return
    if not retry:
        if send("start", f"开始执行@{sender_name} 发起的审判", reserve=True) != "running":
            return
        write(last_path, {"time": time(), "sender": sender_name})
        write(resume_path, {"event": event.model_dump(mode="json"), "settings": settings.model_dump(mode="json")})
    history = get_job(history_id)
    history['trial_resumable'] = True
    history_lock = Lock()

    def progress(message):
        with history_lock:
            history["steps"].append(message)
            save(str(history_id), history)
        log.info("trial_group_step", group_id=group, trial_id=str(trial_id), msg=message)

    def finish(status, message):
        history["status"] = status
        progress(message)

    notice_sent = any(step.startswith(("审判开场通知 · completed", "开场通知已发送")) for step in history["steps"])

    def announce():
        nonlocal notice_sent
        if notice_sent:
            return
        started = perf_counter()
        status = send("opening-notice", f"开始执行@{sender_name} 发起的审判")
        progress(f"审判开场通知 · {status} · {ceil(perf_counter() - started)} 秒")
        if status != "completed":
            raise RuntimeError(f"开场通知未确认送达 · {status}")
        notice_sent = True

    try:
        started = perf_counter()
        progress('正在读取审判范围内的群聊记录')
        input_path = folder / f"{trial_id}.input.json"
        if input_path.exists():
            transcript, names, evidence = json.loads(input_path.read_text(encoding="utf-8"))
        else:
            transcript, names, evidence = read_group(group, settings.trial_hours, settings.trial_limit, before=event)
            write(input_path, [transcript, names, evidence])
        progress(f'群聊记录已就绪 · {ceil(perf_counter() - started)} 秒')
        report = folder / f"{trial_id}.html"
        prior = report.with_suffix(".json")
        outcome = json.loads(prior.read_text(encoding="utf-8")) if retry and prior.exists() else None
        if not (retry and report.exists() and (not settings.trial_auto_warn or (outcome or {}).get("complete"))):
            outcome = preview(report, transcript, names, evidence=evidence,
                              rules=settings.trial_prompt, intro=settings.trial_intro, summary_rules=settings.trial_summary,
                              judges=settings.trial_judges.splitlines(), clerk_id=settings.trial_clerk,
                              auto_warn=settings.trial_auto_warn, request_prefix=str(history_id),
                              cache_dir=folder / str(trial_id), on_judges_started=announce,
                              progress=progress)
        if not notice_sent:
            announce()
        if retry and any(step.startswith("HTML 报告已发送到原群") for step in history["steps"]):
            progress("已复用此前送达的 HTML 报告")
        else:
            stage_local_report(history_id, report, '群聊审判.html', '.html')
            progress('HTML 报告已生成，正在发送到原群')
            started = perf_counter()
            delivery = send('report', asset_id=history_id)
            if delivery != 'completed':
                raise RuntimeError(f'HTML 报告未确认送达，停止后续警告 · {delivery}')
            progress(f"HTML 报告已发送到原群 · {ceil(perf_counter() - started)} 秒")
    except Exception as exc:
        finish("failed", f"审判失败 · {exc}")
        log.exception("trial_group_failed", group_id=group, trial_id=str(trial_id))
        write(state_path, {"status": "failed"})
        return

    state = json.loads(state_path.read_text(encoding="utf-8")) if retry and state_path.exists() else {}
    state.setdefault("members", {})
    bot = robot_wxid()
    if not bot and (outcome or {}).get("warnings"):
        log.warning("trial_penalties_skipped", group_id=group, trial_id=str(trial_id), msg="请先配置机器人 wxid")
        finish("failed", "报告已生成，未执行警告：请先配置机器人 wxid")
        write(state_path, {**state, "status": "missing_robot_wxid"})
        return
    warnings = (outcome or {}).get("warnings", [])
    if warnings:
        started = perf_counter()
        notice = send("warning-start", "开始根据审判结果，发送警告")
        progress(f"自动警告开始通知 · {notice} · {ceil(perf_counter() - started)} 秒")
        if notice != "completed":
            finish("failed", "自动警告开始通知未确认，暂停警告")
            write(state_path, {**state, "status": "failed"})
            return
    for item in warnings:
        try:
            rows = item["violations"]
            user_id = rows[0]["wechat_user_id"]
            if state["members"].get(user_id, {}).get("recorded"):
                continue
            contact = source.conversation_details([user_id]).get(user_id, {})
            nickname = nicknames.get(user_id)
            username = contact.get("original_name")
            person = {"id": user_id, "username": username or "", "group_nickname": nickname or ""}
            reason = "\n".join(f"违规原文：{row['text']}\n原因：{row['reason']}" for row in rows)
            results = state["members"].setdefault(user_id, {})
            progress(f"正在发送 {nickname or username or user_id} 的违规依据")
            started = perf_counter()
            results["reason"] = send(f"{user_id}:reason", reason)
            progress(f"违规依据发送 · {results['reason']} · {ceil(perf_counter() - started)} 秒")
            if results["reason"] != "completed":
                continue
            if not (nickname or username):
                results["warning"] = "缺少可用于 @ 的昵称"
                continue
            progress(f"正在发送 {nickname or username or user_id} 的 @ 警告")
            started = perf_counter()
            results["warning"] = send(f"{user_id}:warning", "警告", [{
                "group_nickname": nickname or username, "username": username or nickname,
                "remark": contact.get("remark") or None,
            }])
            progress(f"@ 警告发送 · {results['warning']} · {ceil(perf_counter() - started)} 秒")
            if results["warning"] != "completed":
                continue
            now = time()
            record_trial_warning(group, {
                "id": str(uuid5(trial_id, user_id)), "target": person,
                "issuer": {"id": bot, "username": "机器人", "group_nickname": ""},
                "issued_at": now, "expires_at": now + settings.trial_warning_hours * 3600,
                "active": True, "ended_at": None, "ended_by": None, "reason": "未失效",
                "note": "", "warning_reason": reason,
            })
            results["recorded"] = True
            progress(f"已记录 {nickname or username or user_id} 的警告")
        except Exception:
            log.exception("trial_penalty_failed", group_id=group, trial_id=str(trial_id))
        finally:
            progress(f"成员处理状态 · {user_id} · {json.dumps(state['members'].get(user_id, {}), ensure_ascii=False)}")
            write(state_path, state)
    failed = any(not member.get("recorded") for member in state["members"].values())
    if outcome is not None and not outcome.get("complete"):
        failed = True
    state["status"] = "failed" if failed else "completed"
    write(state_path, state)
    finish(state["status"], "审判结束，部分步骤未完成" if failed else "审判完成")



def resume(group, request_id, context):
    from ...group_plugins import Settings
    from ...reader import history_source
    from ...reply import MessageEvent
    from ...sender_jobs import get_job, save

    try:
        dispatch(history_source(), MessageEvent.model_validate(context["event"]),
                 Settings.model_validate(context["settings"]), retry=True)
    except Exception as exc:
        result = get_job(request_id)
        result["status"] = "failed"
        result["steps"].append(f"续跑失败 · {exc}")
        save(str(request_id), result)
        log.exception("trial_resume_failed", group_id=group, request_id=str(request_id))
