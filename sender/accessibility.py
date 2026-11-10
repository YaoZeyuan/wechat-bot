"""USB client for the phone's explicitly enabled accessibility service."""
from __future__ import annotations

import json
import socket

from .base import SendError
from .config import setup_adb
from .store import init_db, log_send
from .text import normalize_text


class ShareRejected(SendError):
    """The phone confirmed that the share was rejected before clicking Send."""


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
            error_type = ShareRejected if action == "share" else SendError
            raise error_type(result["error"])
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

    def send_asset(self, target, kind, path, filename, *, group=False):
        import mimetypes
        import re
        import shlex
        import subprocess
        from uuid import uuid4

        from adbutils._utils import adb_path

        user = self.share_user()
        mime = mimetypes.guess_type(filename)[0]
        if mime not in {"text/plain", "application/pdf", "image/png", "image/jpeg"}:
            raise SendError("附件媒体类型不受支持")
        directory = f"Download/WeChatSender-{uuid4().hex}/"
        collection = "content://media/external/downloads"

        def content(action, uri, *args):
            return ["content", action, "--user", user, "--uri", uri, *args]

        def shell(*args):
            return self.device.shell(list(args), timeout=10)

        self.progress(f"正在为 Android 系统分享准备附件：{filename}")
        shell(*content("insert", collection, "--bind", f"_display_name:s:{filename}",
                       "--bind", f"mime_type:s:{mime}", "--bind", f"relative_path:s:{directory}"))
        rows = shell(*content("query", collection, "--projection", "_id:_data",
                              "--where", f"relative_path='{directory}'"))
        entries = re.findall(r"_id=(\d+), _data=(/storage/emulated/\d+/Download/[^\r\n]+)", rows)
        if len(entries) != 1:
            raise SendError("Android 未返回唯一的附件共享地址")
        row_id, remote = entries[0]
        if not remote.endswith(f"/{directory}{filename}"):
            raise SendError("Android 返回的共享路径与本次附件不一致")
        uri = f"{collection}/{row_id}"
        adb = [adb_path(), "-s", self.device.serial]
        try:
            data = path.read_bytes()
            shell("mkdir", "-p", remote.rsplit("/", 1)[0])
            self.device.sync.push(str(path), remote)
            copied = subprocess.check_output([*adb, "exec-out", shlex.join(content("read", uri))], timeout=30)
            if copied != data:
                raise SendError(f"手机上的共享附件未通过内容校验（预期 {len(data)} 字节，实际 {len(copied)} 字节）")

            self.progress("已校验附件内容，正在打开微信系统分享")
            return self.share(user, target, mime, kind,
                              ["--grant-read-uri-permission", "--eu", "android.intent.extra.STREAM", uri],
                              filename=filename, group=group)
        finally:
            try:
                shell(*content("delete", uri))
                shell("rmdir", remote.rsplit("/", 1)[0])
            except Exception:
                self.progress("手机端临时分享文件清理失败")

    def foreground(self):
        import re

        windows = self.device.shell(["dumpsys", "window"], timeout=5)
        return re.search(r"mCurrentFocus=Window\{[^\r\n]*\bu(\d+) ([\w.]+)/([^}\s]+)", windows)

    def share_user(self):
        active = self.foreground()
        if active is None or active.group(2) != "com.tencent.mm":
            raise SendError("请先将目标微信打开到前台")
        return active.group(1)

    def share_text(self, target, text, *, group=False):
        text = normalize_text(text)
        if not text.strip() or len(text) > self.max_chars:
            raise SendError(f"文字不能为空，且最多 {self.max_chars} 字")
        return self.share(self.share_user(), target, "text/plain", "text",
                          ["--es", "android.intent.extra.TEXT", text], text=text, group=group)

    def share(self, user, target, mime, kind, extras, **payload):
        import time

        output = self.device.shell(
            ["am", "start", "-W", "--user", user, "-n", "com.tencent.mm/.ui.tools.ShareImgUI",
             "-a", "android.intent.action.SEND", "-t", mime, *extras], timeout=10)
        if "Error:" in output or "Exception" in output:
            raise SendError("Android 无法打开微信分享页面")

        prompted = False
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            active = self.foreground()
            if active and active.group(1) == user and active.group(2) == "com.tencent.mm":
                if self.call("share_status")["ready"]:
                    break
            if active and "XSpaceResolveActivity" in active.group(3) and not prompted:
                self.progress("检测到双开微信，请在手机上选择原先处于前台的微信实例")
                prompted = True
            time.sleep(1)
        else:
            raise SendError("等待微信分享页面超时，未发送消息")

        self.progress("正在核对微信分享确认页上的收件人和内容")
        try:
            result = self.call("share", target=target, kind=kind, dry_run=self.dry_run, **payload)
            self.progress(result.get("error", "系统分享操作已完成"))
            return result["ok"]
        finally:
            try:
                self.leave_share(user)
            except Exception:
                self.progress("微信分享页面未能自动退出，请在手机上返回")

    def leave_share(self, user):
        import time

        share_pages = {"MvvmContactListUI", "SelectConversationUI", "SendAppMessageWrapperUI",
                       "ShareImgUI", "ConvBoxTransmitUI", "HalfScreenTransparentActivity"}
        for attempt in range(5):
            active = self.foreground()
            if not active or active.group(1) != user or active.group(2) != "com.tencent.mm":
                return
            if active.group(3).rsplit(".", 1)[-1] not in share_pages:
                return
            if attempt == 4:
                self.device.shell(["am", "start", "-W", "--user", user,
                                   "-n", "com.tencent.mm/.ui.LauncherUI"], timeout=10)
                return
            self.device.shell(["input", "keyevent", "KEYCODE_BACK"], timeout=5)
            time.sleep(0.4)
