"""微信消息发送模块（可插拔）。

目录职责：
  base.py    Sender 协议与 SendError
  config.py  配置加载与 adb 环境
  text.py    文本规范化与校验探针
  store.py   SQLite 发送日志
  u2.py      uiautomator2 实现（当前唯一通道）

新增发送通道：实现 Sender 协议，在 create_sender 里注册一行即可。
"""
from __future__ import annotations

from typing import Any

from .base import SendError, Sender
from .config import CONFIG_PATH, DEFAULT_CONFIG, ROOT, load_config, setup_adb
from .store import init_db, log_send
from .text import normalize_text, text_probe
from .u2 import U2Sender, find, find_waiting

def create_sender(
    channel: str | None = None,
    *,
    cfg: dict[str, Any] | None = None,
    serial: str | None = None,
    dry_run: bool | None = None,
) -> Sender:
    """按通道名创建发送器；新增实现只需在这里加一个分支。"""
    cfg = cfg or load_config()
    channel = channel or cfg.get("sender_channel", "u2")
    if channel == "u2":
        return U2Sender(cfg=cfg, serial=serial, dry_run=dry_run)
    raise SendError(f"未知发送通道：{channel}（当前只注册了 u2）")


__all__ = [
    "SendError",
    "Sender",
    "ROOT",
    "CONFIG_PATH",
    "DEFAULT_CONFIG",
    "load_config",
    "setup_adb",
    "init_db",
    "log_send",
    "normalize_text",
    "text_probe",
    "U2Sender",
    "find",
    "find_waiting",
    "create_sender",
]
