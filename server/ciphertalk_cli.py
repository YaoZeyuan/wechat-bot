"""Run the locally built CipherTalk reader without a desktop proxy."""

import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path
from queue import Empty, Queue
from threading import Thread

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

    def restart_wechat(self, progress):
        if os.name != "nt":
            raise SourceError("自动重启微信目前只支持 Windows")
        dll = self.key_dll()
        if not dll.is_file():
            raise SourceError("本地密钥工具缺少 wx_key.dll，微信未重启")
        discover = """
            $ErrorActionPreference = 'Stop'
            $processes = @(Get-Process -Name Weixin -ErrorAction SilentlyContinue)
            if (!$processes.Count) { exit 2 }
            $paths = @($processes | ForEach-Object { $_.Path } | Sort-Object -Unique)
            if ($paths.Count -ne 1 -or !$paths[0] -or !(Test-Path -LiteralPath $paths[0] -PathType Leaf)) {
                exit 3
            }
            [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
            [Console]::WriteLine($paths[0])
        """
        close = """
            $ErrorActionPreference = 'Stop'
            $session = (Get-Process -Id $PID).SessionId
            $processes = @(Get-Process -Name Weixin -ErrorAction SilentlyContinue |
                Where-Object { $_.SessionId -eq $session -and $_.Path -eq $env:WECHAT_BOT_RESTART_EXE })
            foreach ($process in $processes) {
                try { Stop-Process -Id $process.Id -Force -ErrorAction Stop }
                catch {
                    if (Get-Process -Id $process.Id -ErrorAction SilentlyContinue) { exit 5 }
                }
            }
            for ($attempt = 0; $attempt -lt 40; $attempt++) {
                $remaining = @(Get-Process -Name Weixin -ErrorAction SilentlyContinue |
                    Where-Object { $_.SessionId -eq $session })
                if (!$remaining.Count) { exit 0 }
                Start-Sleep -Milliseconds 250
            }
            [Console]::WriteLine(($remaining.Id -join ','))
            exit 4
        """
        launch = """
            $ErrorActionPreference = 'Stop'
            $exe = $env:WECHAT_BOT_RESTART_EXE
            $started = Start-Process -FilePath $exe -WorkingDirectory (Split-Path -Parent $exe) -PassThru
            for ($attempt = 0; $attempt -lt 30; $attempt++) {
                $process = Get-Process -Id $started.Id -ErrorAction SilentlyContinue
                if ($process -and $process.ProcessName -eq 'Weixin') {
                    [Console]::WriteLine($process.Id)
                    exit 0
                }
                Start-Sleep -Milliseconds 500
            }
            exit 4
        """
        def run(script, **extra_env):
            try:
                return subprocess.run(
                    ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                    capture_output=True, timeout=30, env={**self.env, **extra_env},
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except (OSError, subprocess.TimeoutExpired):
                raise SourceError("微信进程操作超时。请确认微信窗口状态，再重新尝试。") from None

        progress("detecting", "正在查找当前微信进程和安装位置")
        result = run(discover)
        if result.returncode == 2:
            raise SourceError("没有找到 Weixin.exe。请先打开电脑版微信，再重试。")
        if result.returncode:
            raise SourceError("无法确认唯一的微信安装位置。请退出其他微信实例，并以同一 Windows 用户运行后端。")
        executable = result.stdout.decode("utf-8").strip()
        if not executable or not Path(executable).is_file():
            raise SourceError("微信安装路径不可读。请以启动微信的同一 Windows 用户运行后端。")
        progress("closing", "已识别微信，正在关闭当前进程")
        closed = run(close, WECHAT_BOT_RESTART_EXE=executable)
        if closed.returncode == 4:
            pids = closed.stdout.decode("ascii", errors="ignore").strip()
            raise SourceError(f"等待 10 秒后仍检测到微信进程（PID：{pids}）。请关闭这些进程后再试。")
        if closed.returncode == 5:
            raise SourceError("无法结束仍在运行的微信进程。请确认后端与微信使用相同的权限级别。")
        if closed.returncode:
            raise SourceError(f"微信退出检查脚本失败（退出码 {closed.returncode}）。")
        progress("starting", "微信已退出，正在重新启动")
        started = run(launch, WECHAT_BOT_RESTART_EXE=executable)
        if started.returncode or not started.stdout.strip().isdigit():
            raise SourceError("微信已退出，但重新启动失败。请手动打开微信，再重试。")
        return int(started.stdout.strip())

    def key_dll(self):
        native = Path(self.env.get("MIYU_NATIVE_ROOT") or self.entry.parent.parent / "native")
        return native / "win32-x64" / "wx_key.dll"

    def extract_with_progress(self, pid: int, progress) -> str:
        errors = {
            "dll_failed": "无法加载密钥 DLL，请检查库文件和 Python 架构是否匹配。",
            "hook_failed": "DLL 已加载，但 Hook 初始化失败。请检查微信版本和进程权限。",
            "timeout": "Hook 已初始化，但等待密钥超时。请确认在等待期间登录了目标账号。",
        }
        try:
            process = subprocess.Popen(
                [sys.executable, "-m", "server.wechat_key_worker", str(self.key_dll()), str(pid)],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, encoding="utf-8",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError:
            raise SourceError("无法启动本机密钥工作进程") from None

        events = Queue()

        def read_events():
            with process.stdout as stream:
                for line in stream:
                    events.put(line)
            events.put(None)

        Thread(target=read_events, daemon=True).start()
        deadline = time.monotonic() + 75
        key = None
        try:
            while (remaining := deadline - time.monotonic()) > 0:
                line = events.get(timeout=remaining)
                if line is None:
                    break
                event = json.loads(line)
                stage = event["stage"]
                if stage in errors:
                    raise SourceError(errors[stage])
                if stage == "dll_loaded":
                    progress(stage, "DLL 已加载，正在初始化微信 Hook")
                elif stage == "hook_initialized":
                    progress(stage, "Hook 初始化成功，正在等待密钥。请登录目标微信账号。")
                elif stage == "key":
                    key = event.get("key")
            if key and re.fullmatch(r"[0-9a-fA-F]{64}", key) and process.wait(timeout=2) == 0:
                return key
            raise SourceError("密钥工作进程未正常完成，请查看后端日志")
        except (Empty, ValueError, subprocess.TimeoutExpired):
            raise SourceError("密钥工作进程超时或返回无效结果，请重新启动微信后重试") from None
        finally:
            if process.poll() is None:
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()

    def sessions(self, *, q="", limit=50) -> dict:
        data = self._run("sessions", "--type", "group", "--limit", "1000")
        groups = [item for item in data["sessions"] if q.casefold() in item["displayName"].casefold()]
        return {
            "items": [{**item, "kind": item["type"]} for item in groups[:limit]],
            "hasMore": len(groups) > limit or data.get("hasMore", False),
        }

    def messages(self, session_id: str, *, limit=1, offset=0, **filters) -> dict:
        return self._run("messages", session_id, "--limit", str(limit), "--offset", str(offset))

