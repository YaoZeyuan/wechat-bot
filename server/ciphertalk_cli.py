"""Run the locally built CipherTalk reader without a desktop proxy."""

import json
import os
import re
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path

from .ciphertalk import SourceError


class CipherTalkCLI:
    def __init__(self):
        root = Path(__file__).resolve().parent.parent
        self.entry = root / ".venv" / "ciphertalk-cli" / "bin" / "miyu.js"
        bundled = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"
        self.node = os.environ.get("WECHAT_BOT_NODE") or (str(bundled) if bundled.is_file() else "node")
        self.env = self.local_settings()
        if not (self.entry.parent.parent / "dist/index.js").is_file():
            raise SourceError("请先构建本地密语 CLI")
    @staticmethod
    def local_settings():
        env = dict(os.environ)
        appdata = env.get("APPDATA")
        if appdata:
            path = Path(appdata) / "CipherTalk" / "ciphertalk-config.db"
            if path.is_file():
                CipherTalkCLI._import_settings(path, env)
        return env

    @staticmethod
    def _import_settings(path: Path, env: dict):
        keys = ("accounts", "activeAccountId", "dbPath", "decryptKey", "myWxid")
        try:
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
                values = {k: json.loads(v) for k, v in db.execute(
                    "SELECT key,value FROM config WHERE key IN (?,?,?,?,?)", keys
                )}
            profile = next(
                (a for a in values.get("accounts", []) if a.get("id") == values.get("activeAccountId")),
                values,
            )
            for env_key, value in {
                "MIYU_DB_PATH": profile.get("dbPath"),
                "MIYU_WXID": profile.get("wxid") or profile.get("myWxid"),
                "MIYU_KEY_HEX": profile.get("decryptKey"),
            }.items():
                if isinstance(value, str) and value:
                    if env_key != "MIYU_KEY_HEX" or re.fullmatch("[0-9a-fA-F]{64}", value):
                        env.setdefault(env_key, value)
        except (sqlite3.Error, ValueError, TypeError):
            raise SourceError("无法读取本机密语账号配置") from None

    def _run(self, *args: str, timeout: int = 30) -> dict:
        try:
            result = subprocess.run(
                [self.node, str(self.entry), "--format", "json", *args],
                env=self.env, capture_output=True, encoding="utf-8", timeout=timeout,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode and args == ("key", "get"):
                diagnostic = result.stderr
                if "未运行" in diagnostic:
                    raise SourceError("未检测到微信，请先打开微信，再开始获取密钥")
                if "等待密钥超时" in diagnostic:
                    raise SourceError("等待密钥超时，请在提取期间完成微信登录")
                if "Hook 微信进程失败" in diagnostic:
                    raise SourceError("密钥工具无法连接微信进程，请检查版本兼容性和权限")
                if "密钥提取库未找到" in diagnostic:
                    raise SourceError("本地密钥工具缺少 wx_key.dll")
                raise SourceError(f"本地密钥工具执行失败（退出码 {result.returncode}）")
            body = json.loads(result.stdout)
        except (OSError, subprocess.TimeoutExpired, ValueError):
            raise SourceError("本地密语 CLI 启动失败、超时或输出无效") from None
        if result.returncode or not isinstance(body, dict) or body.get("ok") is False:
            raise SourceError(f"本地密语 CLI 查询失败（退出码 {result.returncode}）")
        return body.get("data", body)

    def check_connection(self):
        connection = self._run("status").get("connection") or {}
        if connection.get("ok"):
            return
        error = connection.get("error") or ""
        if "license expired" in error:
            raise SourceError("本地密语 CLI 的 WCDB 原生库许可证已到期")
        if not connection.get("attempted"):
            raise SourceError("本地密语 CLI 尚未配置数据库路径和密钥")
        raise SourceError("本地密语 CLI 无法打开微信数据库，请检查账号、路径和密钥")

    def extract_key(self) -> str:
        key = self._run("key", "get", timeout=75).get("keyHex")
        if not isinstance(key, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", key):
            raise SourceError("本地密钥工具未返回有效的数据库密钥")
        return key

    def restart_wechat(self):
        if os.name != "nt":
            raise SourceError("自动重启微信目前只支持 Windows")
        native = Path(self.env.get("MIYU_NATIVE_ROOT") or self.entry.parent.parent / "native")
        if not (native / "win32-x64" / "wx_key.dll").is_file():
            raise SourceError("本地密钥工具缺少 wx_key.dll，微信未重启")
        script = """
            $ErrorActionPreference = 'Stop'
            $processes = @(Get-Process -Name Weixin -ErrorAction SilentlyContinue)
            if (!$processes.Count) { exit 2 }
            $paths = @($processes | ForEach-Object { $_.Path } | Sort-Object -Unique)
            if ($paths.Count -ne 1 -or !$paths[0] -or !(Test-Path -LiteralPath $paths[0] -PathType Leaf)) {
                exit 3
            }
            $processes | Stop-Process -Force
            $processes | Wait-Process -Timeout 10 -ErrorAction SilentlyContinue
            Start-Process -FilePath $paths[0] -WorkingDirectory (Split-Path -Parent $paths[0])
            for ($attempt = 0; $attempt -lt 30; $attempt++) {
                if (Get-Process -Name Weixin -ErrorAction SilentlyContinue) { exit 0 }
                Start-Sleep -Milliseconds 500
            }
            exit 4
        """
        try:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True, timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired):
            raise SourceError("无法重启微信或等待启动超时") from None
        if result.returncode == 2:
            raise SourceError("请先打开电脑版微信，以便识别安装位置")
        if result.returncode:
            raise SourceError("无法确认微信安装路径或重启失败，请检查微信进程和权限")

    def sessions(self, *, q="", limit=50) -> dict:
        data = self._run("sessions", "--type", "group", "--limit", "1000")
        groups = [item for item in data["sessions"] if q.casefold() in item["displayName"].casefold()]
        return {
            "items": [{**item, "kind": item["type"]} for item in groups[:limit]],
            "hasMore": len(groups) > limit or data.get("hasMore", False),
        }

    def messages(self, session_id: str, *, limit=1, offset=0, **filters) -> dict:
        return self._run("messages", session_id, "--limit", str(limit), "--offset", str(offset))

