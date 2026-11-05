"""微信消息发送模块（uiautomator2 3.x）。

只负责「把一段文本发到指定会话」，不含统计与前端逻辑。
3.x 移除了 atx-agent / init / connect_wifi / set_fastinput_ime，
连接统一用 u2.connect(serial)，文本输入走 send_keys（剪贴板或 IME 广播）。
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

import uiautomator2 as u2

ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "config.json"


class SendError(RuntimeError):
    """发送链路中找不到控件或校验失败。"""


def load_config(path: str | Path = DEFAULT_CONFIG) -> dict[str, Any]:
    """读取本机私有配置；换机器首次运行时自动从模板生成一份。"""
    path = Path(path)
    if not path.exists():
        example = ROOT / "config.example.json"
        if not example.exists():
            raise FileNotFoundError(f"缺少配置文件 {path}，也找不到模板 config.example.json")
        shutil.copyfile(example, path)
        print(f"[配置] 未找到 {path.name}，已从模板生成，请按本机环境修改后再用。")
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def setup_adb(cfg: dict[str, Any]) -> None:
    """把本机 adb 目录临时塞进 PATH，让 adbutils 找得到 adb。"""
    adb_dir = cfg.get("adb_dir", "")
    if adb_dir and Path(adb_dir).is_dir():
        os.environ["PATH"] = str(Path(adb_dir)) + os.pathsep + os.environ.get("PATH", "")


def init_db(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.execute(
        """CREATE TABLE IF NOT EXISTS send_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target TEXT NOT NULL,
            text TEXT NOT NULL,
            ok INTEGER NOT NULL,
            message TEXT NOT NULL,
            dry_run INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )"""
    )
    conn.commit()
    return conn


def log_send(conn, target, text, ok, message, dry_run=False) -> None:
    conn.execute(
        "INSERT INTO send_log (target, text, ok, message, dry_run, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (target, text, 1 if ok else 0, message, 1 if dry_run else 0,
         datetime.now().astimezone().isoformat(timespec="seconds")),
    )
    conn.commit()


def normalize_text(text: str) -> str:
    """规范化即将进入微信输入框的文本。

    CRLF / CR 统一为 LF；移除 NUL、BEL、DEL 等控制字符，保留换行与制表符。
    emoji、四字节汉字、零宽字符不受影响。
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(
        ch for ch in text
        if ch in ("\n", "\t") or unicodedata.category(ch) != "Cc"
    )


def text_probe(text: str) -> str:
    """取首个非空行的前 8 个字符，用于发送后回查聊天记录。"""
    for line in text.splitlines():
        if line.strip():
            return line.strip()[:8]
    return ""


def find(d, candidates):
    """按候选顺序找第一个存在的控件，返回 (UiObject, selector)。"""
    for sel in candidates:
        try:
            obj = d(**sel)
            if bool(obj.exists):
                return obj, sel
        except Exception:
            continue
    return None, None


def find_waiting(d, candidates, timeout=8.0, interval=0.4):
    """轮询等待控件出现；微信页面加载慢时比单次 exists 可靠。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        obj, sel = find(d, candidates)
        if obj is not None:
            return obj, sel
        time.sleep(interval)
    return None, None


class WeChatSender:
    def __init__(self, cfg=None, serial=None, dry_run=None):
        self.cfg = cfg or load_config()
        setup_adb(self.cfg)
        self.sel = self.cfg["selectors"]
        self.pkg = self.cfg.get("wechat_package", "com.tencent.mm")
        self.delay = float(self.cfg.get("step_delay", 0.6))
        self.page_delay = float(self.cfg.get("page_load_delay", 1.5))
        self.wait_timeout = float(self.cfg.get("wait_timeout", 10.0))
        self.verify_timeout = float(self.cfg.get("verify_timeout", 6.0))
        self.result_class = self.cfg.get("result_class", "android.widget.TextView")
        self.dry_run = bool(self.cfg.get("dry_run", False)) if dry_run is None else bool(dry_run)
        self.serial = serial or self.cfg.get("serial") or None
        self.debug_dir = ROOT / self.cfg.get("debug_dir", "debug")
        self.conn = init_db(ROOT / self.cfg.get("database", "bot.db"))
        self.d = None

    def connect(self):
        try:
            self.d = u2.connect(self.serial) if self.serial else u2.connect()
        except Exception as exc:
            raise SendError(
                "连接设备失败。确认手机已用 USB 连接、开启 USB 调试并在弹窗点了「允许」，"
                "且 adb devices 里状态是 device（不是 unauthorized / offline）。"
            ) from exc
        self.d.implicitly_wait(self.wait_timeout)
        info = self.d.info
        label = info.get("productName") or self.serial or "自动选择"
        print(f"[设备] {label} / Android {info.get('version', '?')}")
        return self.d

    def _snapshot(self, tag):
        self.debug_dir.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = self.debug_dir / f"{tag}_{stamp}"
        try:
            self.d.screenshot(str(base.with_suffix(".jpg")))
        except Exception:
            pass
        try:
            xml = self.d.dump_hierarchy(compressed=False, pretty=True)
            base.with_suffix(".xml").write_text(xml, encoding="utf-8")
        except Exception:
            pass
        print(f"[调试] 截图与界面层级已保存：{base.name}.jpg / {base.name}.xml（在 debug/）")

    def dump_ui(self) -> Path:
        self.debug_dir.mkdir(exist_ok=True)
        path = self.debug_dir / "hierarchy.xml"
        xml = self.d.dump_hierarchy(compressed=False, pretty=True)
        path.write_text(xml, encoding="utf-8")
        return path

    def _goto_home(self):
        for _ in range(5):
            mark, _ = find(self.d, self.sel["home_mark"])
            if mark is not None:
                return
            self.d.press("back")
            time.sleep(self.delay)
        try:
            cur = self.d.app_current()
        except Exception:
            cur = {}
        if cur.get("package") != self.pkg:
            self.d.app_start(self.pkg)
            time.sleep(1.0)
        mark, _ = find_waiting(self.d, self.sel["home_mark"], 4.0)
        if mark is None:
            raise SendError(
                "回不到微信主界面。运行 .venv/Scripts/python.exe send.py --dump 看当前界面，"
                "再改 config.json 的 selectors.home_mark。"
            )

    def _text_written(self, obj, text) -> bool:
        """确认文本确实进了输入框（空框时 get_text 会返回 hint）。"""
        try:
            got = obj.get_text() or ""
        except Exception:
            return False
        probe = text_probe(text)
        if not probe:
            return bool(got.strip())
        return probe[:6] in got

    def _wait_written(self, obj, text, timeout=2.0, interval=0.35) -> bool:
        """微信页面有异步刷新，写入后轮询读取而不是只等一次。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._text_written(obj, text):
                return True
            time.sleep(interval)
        return self._text_written(obj, text)

    def _set_text_checked(self, obj, text, label="输入框"):
        """按实测有效顺序写入文本，并校验结果。

        在 Android 16 / 微信 8.x 上实测：selector.set_text 有效；
        d.send_keys 会静默失败（剪贴板有值但输入框没变），剪贴板长按粘贴也不可用。
        """
        try:
            obj.set_text(text)
            if self._wait_written(obj, text):
                return
        except Exception:
            pass
        try:
            self.d.send_keys(text, clear=True)
            if self._wait_written(obj, text):
                return
        except Exception:
            pass
        try:
            self.d.clipboard = text
            obj.long_click()
            paste, _ = find_waiting(self.d, self.sel["paste_menu"], 3.0)
            if paste is not None:
                paste.click()
                if self._wait_written(obj, text):
                    return
        except Exception:
            pass
        raise SendError(
            f"{label}写入失败：set_text / send_keys / 剪贴板粘贴都没生效，"
            "看 debug/ 截图与界面层级调整 selectors。"
        )

    def _click_result(self, target) -> bool:
        """在搜索结果里点击目标会话，跳过输入框节点。"""
        for sel in (
            {"text": target, "className": self.result_class},
            {"textContains": target, "className": self.result_class},
        ):
            try:
                obj = self.d(**sel)
                if bool(obj.exists):
                    obj.click()
                    return True
            except Exception:
                continue
        for index in range(4):
            try:
                obj = self.d(textContains=target, instance=index)
                if not bool(obj.exists):
                    break
                if "EditText" in str((obj.info or {}).get("className", "")):
                    continue
                obj.click()
                return True
            except Exception:
                continue
        return False

    def _open_chat(self, target):
        self.d.app_start(self.pkg)
        time.sleep(self.page_delay)
        self._goto_home()

        # 路径 A：目标已在主界面会话列表里，直接点，绕开搜索页输入
        direct = self.d(text=target, className=self.result_class)
        if bool(direct.exists):
            print(f"[会话] 主界面直点：{target}")
            direct.click()
            time.sleep(self.page_delay)
            found, _ = find_waiting(self.d, self.sel["chat_input"], 3.0)
            if found is not None:
                return found
            self.d.press("back")
            time.sleep(self.delay)
            self._goto_home()

        # 路径 B：搜索
        entry, used = find_waiting(self.d, self.sel["search_entry"], self.wait_timeout)
        if entry is None:
            raise SendError(
                "找不到微信首页的搜索入口。运行 .venv/Scripts/python.exe send.py --dump 看界面，"
                "再改 selectors.search_entry。"
            )
        print(f"[搜索] 入口命中 {used}")
        entry.click()
        time.sleep(self.page_delay)

        box, _ = find_waiting(self.d, self.sel["search_input"], self.wait_timeout)
        if box is None:
            raise SendError("搜索页找不到输入框，改 selectors.search_input。")
        box.click()
        time.sleep(self.delay)
        self._set_text_checked(box, target, "搜索框")
        time.sleep(self.page_delay)

        if not self._click_result(target):
            self._snapshot("search_failed")
            raise SendError(
                f"搜索「{target}」没有可点的结果。确认名字与微信里显示一致，或看 debug/ 截图。"
            )
        time.sleep(self.page_delay)

        chat_input, _ = find_waiting(self.d, self.sel["chat_input"], self.wait_timeout)
        if chat_input is None:
            raise SendError("点进会话后找不到输入框，改 selectors.chat_input。")
        return chat_input

    def _type_text(self, chat_input, text):
        chat_input.click()
        time.sleep(self.delay)
        self._set_text_checked(chat_input, text, "聊天输入框")
        time.sleep(self.delay)

    def _match_count(self, probe: str) -> int:
        """统计当前界面里包含 probe 的文本节点数量。"""
        if not probe:
            return 0
        try:
            return len(self.d(textContains=probe).info_list)
        except Exception:
            try:
                return 1 if bool(self.d(textContains=probe).exists) else 0
            except Exception:
                return 0

    def _input_cleared(self) -> bool:
        """兜底判据：微信接受发送后通常会清空输入框。"""
        try:
            obj, _ = find(self.d, self.sel["chat_input"])
            if obj is None:
                return False
            return not (obj.get_text() or "").strip()
        except Exception:
            return False

    def _verify_sent(self, probe: str, before: int) -> bool:
        """要求匹配节点数比发送前增加，避免历史相同文本造成误报。"""
        if not probe:
            return True
        deadline = time.monotonic() + self.verify_timeout
        while time.monotonic() < deadline:
            if self._match_count(probe) > before:
                return True
            time.sleep(0.5)
        return self._input_cleared()

    def send(self, target: str, text: str) -> bool:
        text = normalize_text(text)
        if not text or not text.strip():
            raise SendError("发送内容为空（或规范化后只剩空白）")
        max_chars = int(self.cfg.get("max_chars", 1800))
        if len(text) > max_chars:
            raise SendError(
                f"文本 {len(text)} 字超过上限 {max_chars}，"
                "请分段发送或调 config.json 的 max_chars"
            )
        probe = text_probe(text)
        print(f"[发送] 目标={target} 字数={len(text)} 演练={self.dry_run}")
        try:
            chat_input = self._open_chat(target)
            try:
                chat_input.clear_text()
            except Exception:
                pass
            before = self._match_count(probe) if not self.dry_run else 0
            self._type_text(chat_input, text)

            if self.dry_run:
                self._snapshot("dry_run")
                print("[演练] 文本已填入输入框，未点击发送。内容：\n" + text)
                try:
                    chat_input.clear_text()
                except Exception:
                    pass
                log_send(self.conn, target, text, False, "dry_run", True)
                return True

            button, used = find_waiting(self.d, self.sel["send_button"], 5.0)
            if button is None:
                raise SendError(
                    "找不到「发送」按钮。用 python send.py --dump 看界面，改 selectors.send_button。"
                )
            print(f"[发送] 按钮命中 {used}")
            button.click()
            time.sleep(self.delay)

            ok = self._verify_sent(probe, before)
            log_send(self.conn, target, text, ok, "sent" if ok else "verify_failed", False)
            if ok:
                print("[完成] 文本已出现在会话中。")
            else:
                self._snapshot("verify_failed")
                print("[警告] 点了发送但没校验到文本，请看 debug/ 里的截图确认。")
            try:
                self.d.press("back")
            except Exception:
                pass
            return ok
        except SendError as exc:
            self._snapshot("error")
            log_send(self.conn, target, text, False, str(exc), self.dry_run)
            raise
        except Exception as exc:
            self._snapshot("error")
            log_send(self.conn, target, text, False, f"{type(exc).__name__}: {exc}", self.dry_run)
            raise SendError(f"发送链路异常：{type(exc).__name__}: {exc}") from exc
