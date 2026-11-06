"""Read-only client for the CipherTalk desktop application's MCP HTTP proxy."""

import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class SourceError(RuntimeError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class CipherTalk:
    @classmethod
    def discover(cls):
        """Read only proxy settings from the current user's desktop configuration."""
        appdata = os.environ.get("APPDATA")
        if not appdata:
            raise SourceError("无法定位密语桌面配置，请手动输入代理设置")
        path = Path(appdata) / "CipherTalk" / "ciphertalk-config.db"
        try:
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
                settings = {
                    key: json.loads(value)
                    for key, value in db.execute(
                        "SELECT key, value FROM config WHERE key IN (?, ?)",
                        ("mcpProxyPort", "mcpProxyToken"),
                    )
                }
            return cls(int(settings.get("mcpProxyPort", 5032)), settings.get("mcpProxyToken") or "")
        except (sqlite3.Error, ValueError, TypeError):
            raise SourceError("无法读取密语代理设置，请手动输入端口和令牌") from None

    def __init__(self, port: int, token: str):
        if not 1 <= port <= 65535:
            raise ValueError("Invalid CipherTalk port")
        self.base = f"http://127.0.0.1:{port}"
        self.token = token
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def _tool(self, name: str, args: dict) -> dict:
        request = Request(
            f"{self.base}/tool/{name}",
            data=json.dumps({"args": args}).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.token}",
            },
        )
        try:
            with self.opener.open(request, timeout=15) as response:
                body = json.loads(response.read(8 * 1024 * 1024))
        except HTTPError as exc:
            if exc.code == 401:
                raise SourceError("密语代理拒绝鉴权，请检查访问令牌") from None
            raise SourceError(f"密语代理调用 {name} 失败（HTTP {exc.code}）") from None
        except (URLError, OSError):
            raise SourceError("无法连接密语代理，请确认代理已启动并检查端口") from None
        except (ValueError, UnicodeError):
            raise SourceError("密语代理返回了无效的 JSON") from None
        if not isinstance(body, dict) or body.get("success") is not True or not isinstance(body.get("data"), dict):
            raise SourceError(f"密语代理调用 {name} 未返回有效数据")
        return body["data"]

    def sessions(self, **filters) -> dict:
        return self._tool("list_sessions", filters)

    def messages(self, session_id: str, **filters) -> dict:
        return self._tool("get_messages", {"sessionId": session_id, **filters})
