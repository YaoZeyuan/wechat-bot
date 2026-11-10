"""USB client for the phone's explicitly enabled accessibility service."""
from __future__ import annotations

import json
import socket

from .base import SendError
from .config import setup_adb
from .store import init_db, log_send
from .text import normalize_text


class AccessibilitySender:
    def __init__(self, cfg, serial=None, dry_run=None):
        setup_adb(cfg)
        self.serial = serial or cfg.get("serial") or None
        self.dry_run = cfg.get("dry_run", True) if dry_run is None else dry_run
        self.max_chars = int(cfg.get("max_chars", 1800))
        self.database = cfg.get("database", "bot.db")
        self.on_progress = None

    def progress(self, message):
        if self.on_progress:
            self.on_progress(message)

    def connect(self):
        from adbutils import adb

        self.device = adb.device(self.serial)
        result = self.call("status")
        self.progress(f"已连接手机无障碍服务，前台微信可见节点 {result['nodes']} 个")

    def call(self, action, **payload):
        endpoint = "localabstract:wechat_sender"
        try:
            port = self.device.forward_port(endpoint)
            with socket.create_connection(("127.0.0.1", port), timeout=3) as peer:
                peer.settimeout(25)
                request = json.dumps({"action": action, **payload}, ensure_ascii=False)
                peer.sendall(request.encode("utf-8") + b"\n")
                with peer.makefile("rb") as response:
                    result = json.loads(response.readline(16384))
        except (OSError, ValueError) as exc:
            raise SendError("手机无障碍通道未响应，请检查手机端服务是否已启用") from exc
        if not result["ok"] and not result.get("uncertain"):
            raise SendError(result["error"])
        return result

    def prepare_chat(self, target):
        self.progress("通过手机无障碍服务检查首页并定位会话")
        self.call("prepare", target=target)
        self.progress("手机无障碍服务已核对会话标题")

    def send(self, target, text, *, current_chat=False, strict_target=False):
        text = normalize_text(text)
        if not text.strip() or len(text) > self.max_chars:
            raise SendError(f"文字不能为空，且最多 {self.max_chars} 字")
        if not current_chat:
            self.prepare_chat(target)
        self.progress("手机无障碍服务正在校验输入框和完整文本")
        result = self.call("text", target=target, text=text, dry_run=self.dry_run)
        ok = result["ok"]
        conn = init_db(self.database)
        try:
            log_send(conn, target, text, ok and not self.dry_run,
                     result.get("error", "dry_run" if self.dry_run else "sent"), self.dry_run)
        finally:
            conn.close()
        self.progress(result.get("error", "演练已清理输入框" if self.dry_run else "手机已核对发送后的完整文本"))
        return ok
