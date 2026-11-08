"""Structured logging for the local FastAPI process."""

import logging
import logging.config
from pathlib import Path

import structlog


MESSAGES = {
    "http_request": "HTTP 请求处理完成",
    "keyword_delivery": "关键词规则投递处理完成",
    "delivery_recovery": "中断的投递已标为失败，可手动重试",
    "desktop_messages_processed": "已处理新收到的桌面消息",
    "desktop_group_poll_failed": "读取群消息失败",
    "desktop_poll_unavailable": "桌面消息轮询失败",
    "desktop_polling_changed": "桌面消息监听状态已更新",
    "phone_task_claimed": "待发送任务已领取",
    "phone_task_result": "待发送任务结果已回写",
    "reader_profile_load_failed": "读取本机微信配置失败",
    "reader_profile_save_failed": "保存本机微信配置失败",
    "wechat_key_extraction_failed": "获取微信数据库密钥失败",
    "wechat_key_extraction_disabled": "后端未启用微信密钥提取，请求已拒绝",
    "desktop_probe_failed": "微信数据库连接验证失败",
    "desktop_probe_invalid_response": "读取端返回了不兼容的数据",
    "desktop_probe_succeeded": "微信数据库连接验证通过",
    "image_key_import_failed": "读取本机图片密钥配置失败",
    "image_key_import_succeeded": "已导入所选账号的图片密钥",
    "webhook_delivery_succeeded": "HTTP 回调成功",
    "webhook_delivery_failed": "HTTP 回调失败",
}

UVICORN_MESSAGES = {
    "Started server process": "后端进程已启动",
    "Waiting for application startup": "正在等待应用初始化",
    "Application startup complete": "应用初始化完成",
    "Uvicorn running on": "后端正在监听，按 Ctrl+C 停止",
    "Shutting down": "正在关闭后端",
    "Waiting for application shutdown": "正在等待应用完成清理",
    "Application shutdown complete": "应用清理完成",
    "Finished server process": "后端进程已退出",
    "[Errno": "后端启动失败，请查看事件字段中的系统错误",
}


def add_chinese_message(_, __, event):
    name = event.get("event", "")
    if name == "wechat_key_stage":
        message = event.get("message", "微信取钥进度更新")
    elif event.get("logger", "").startswith("uvicorn"):
        message = next(
            (label for prefix, label in UVICORN_MESSAGES.items() if name.startswith(prefix)),
            name,
        )
    else:
        message = MESSAGES.get(name, name)
    event["msg"] = message
    return event


def configure_logging(log_dir: Path | None = None):
    if log_dir is None:
        log_dir = Path(__file__).resolve().parent.parent / "local-data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    common = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            *common,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    logging.config.dictConfig({
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"json": {
            "()": structlog.stdlib.ProcessorFormatter,
            "foreign_pre_chain": common,
            "processors": [
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                add_chinese_message,
                structlog.processors.JSONRenderer(ensure_ascii=False),
            ],
        }},
        "handlers": {
            "console": {"class": "logging.StreamHandler", "formatter": "json"},
            "file": {
                "class": "logging.handlers.RotatingFileHandler", "formatter": "json",
                "filename": str(log_dir / "server.log"), "encoding": "utf-8",
                "maxBytes": 5 * 1024 * 1024, "backupCount": 3,
            },
        },
        "root": {"handlers": ["console", "file"], "level": "INFO"},
        "loggers": {
            "uvicorn": {"handlers": [], "propagate": True},
            "uvicorn.error": {"handlers": [], "propagate": True},
            "uvicorn.access": {"handlers": [], "propagate": False},
        },
    })
