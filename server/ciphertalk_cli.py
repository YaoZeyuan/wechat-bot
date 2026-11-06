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

    def _run(self, *args: str) -> dict:
        try:
            result = subprocess.run(
                [self.node, str(self.entry), "--format", "json", *args],
                env=self.env, capture_output=True, encoding="utf-8", timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
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

    def sessions(self, *, q="", limit=50) -> dict:
        data = self._run("sessions", "--type", "group", "--limit", "1000")
        groups = [item for item in data["sessions"] if q.casefold() in item["displayName"].casefold()]
        return {
            "items": [{**item, "kind": item["type"]} for item in groups[:limit]],
            "hasMore": len(groups) > limit or data.get("hasMore", False),
        }

    def messages(self, session_id: str, *, limit=1, offset=0, **filters) -> dict:
        return self._run("messages", session_id, "--limit", str(limit), "--offset", str(offset))

