"""uiautomator2 发送通道（当前唯一实现）。

只负责「把一段文本发到指定会话」，不含统计与前端逻辑。
uiautomator2 3.x 移除了 atx-agent / init / connect_wifi / set_fastinput_ime，
连接统一用 u2.connect(serial)。
"""
from __future__ import annotations

import time
import structlog
from datetime import datetime
from pathlib import Path

import uiautomator2 as u2

from .base import SendError
from .config import ROOT, load_config, setup_adb
from .store import init_db, log_send
from .text import normalize_text, text_probe


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


class U2Sender:
    """实现 sender.base.Sender 协议：通过 adb 驱动手机上的微信 UI。"""

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
        self.on_progress = None

    def progress(self, message):
        structlog.get_logger("wechat_bot.sender").info("sender_step", msg=message)
        if self.on_progress:
            self.on_progress(message)

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

    def _click_result(self, target, *, strict_target=False, search_bottom=0) -> bool:
        """在搜索结果里点击目标会话，跳过输入框节点。"""
        if strict_target:
            matches = []
            for obj in self.d(text=target, packageName=self.pkg):
                info = obj.info
                bounds = info.get("visibleBounds") or info.get("bounds") or {}
                if info.get("className") != "android.widget.EditText" and bounds.get("top", -1) >= search_bottom:
                    matches.append(obj)
            if len(matches) != 1:
                self.progress(f"搜索区找到 {len(matches)} 个同名结果，无法唯一定位")
                return False
            matches[0].click()
            return True
        selectors = (
            {"text": target, "className": self.result_class},
            {"textContains": target, "className": self.result_class},
        )
        for sel in selectors:
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

    def _open_chat(self, target, *, strict_target=False):
        self.progress("检查手机微信首页")
        if strict_target:
            if self.d.app_current().get("package") != self.pkg:
                raise SendError("请先在手机前台打开微信并停在聊天首页")
            mark, _ = find_waiting(self.d, self.sel["home_mark"], 2.0)
            if mark is None:
                raise SendError("未识别到微信聊天首页，请手动切到聊天列表后重试")
        else:
            self.d.app_start(self.pkg)
            time.sleep(self.page_delay)
            self._goto_home()

        # 路径 A：目标已在主界面会话列表里，直接点，绕开搜索页输入
        direct = self.d(text=target, className=self.result_class)
        if bool(direct.exists) and (not strict_target or direct.count == 1):
            print(f"[会话] 主界面直点：{target}")
            direct.click()
            time.sleep(self.page_delay)
            found, _ = find_waiting(self.d, self.sel["chat_input"], 3.0)
            if found is not None:
                return found
            if strict_target:
                raise SendError("点开目标后未找到聊天输入框")
            self.d.press("back")
            time.sleep(self.delay)
            self._goto_home()

        # 路径 B：搜索
        search_entries = [
            *self.sel["search_entry"],
            {"resourceId": f"{self.pkg}:id/meb"},
            {"resourceId": f"{self.pkg}:id/icon_search"},
        ]
        entry, used = find_waiting(self.d, search_entries, self.wait_timeout)
        if entry is None:
            raise SendError(
                "找不到微信首页的搜索入口。运行 .venv/Scripts/python.exe send.py --dump 看界面，"
                "再改 selectors.search_entry。"
            )
        print(f"[搜索] 入口命中 {used}")
        entry.click()
        self.progress("已打开搜索，正在查找目标会话")
        time.sleep(self.page_delay)

        box, _ = find_waiting(self.d, self.sel["search_input"], self.wait_timeout)
        if box is None:
            raise SendError("搜索页找不到输入框，改 selectors.search_input。")
        box.click()
        search_bottom = (box.info.get("bounds") or {}).get("bottom", 0) if strict_target else 0
        time.sleep(self.delay)
        self._set_text_checked(box, target, "搜索框")
        time.sleep(self.page_delay)

        if not self._click_result(target, strict_target=strict_target, search_bottom=search_bottom):
            self._snapshot("search_failed")
            raise SendError(
                f"搜索「{target}」未能唯一定位会话。确认名称与微信显示一致，或查看 debug/ 截图。"
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

    def current_chat_input(self, target: str):
        if self.d.app_current().get("package") != self.pkg:
            raise SendError("请在手机微信中打开目标群聊")
        title = self.d(text=target, packageName=self.pkg, className="android.widget.TextView")
        height = self.d.window_size()[1]
        if not any(item.info["bounds"]["top"] < height * 0.15 for item in title):
            raise SendError(f"手机当前聊天页标题未匹配「{target}」，请打开对应群聊")
        chat_input, _ = find(self.d, self.sel["chat_input"])
        if chat_input is None:
            raise SendError("当前页面没有聊天输入框")
        if chat_input.get_text():
            raise SendError("聊天输入框已有内容，请先处理草稿")
        return chat_input

    def send(self, target: str, text: str, *, current_chat: bool = False, strict_target: bool = False) -> bool:
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
            chat_input = self.current_chat_input(target) if current_chat else self._open_chat(target, strict_target=strict_target)
            if strict_target and not current_chat:
                chat_input = self.current_chat_input(target)
            self.progress("已进入聊天页，正在填入文本")
            try:
                chat_input.clear_text()
            except Exception:
                pass
            before = self._match_count(probe) if not self.dry_run else 0
            self._type_text(chat_input, text)

            if self.dry_run:
                self.progress("演练完成，未点击发送")
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
                    "找不到「发送」按钮。用 .venv/Scripts/python.exe send.py --dump "
                    "看界面，改 selectors.send_button。"
                )
            print(f"[发送] 按钮命中 {used}")
            button.click()
            self.progress("已点击发送，正在核对结果")
            time.sleep(self.delay)

            ok = self._verify_sent(probe, before)
            log_send(self.conn, target, text, ok, "sent" if ok else "verify_failed", False)
            if ok:
                print("[完成] 文本已出现在会话中。")
            else:
                self._snapshot("verify_failed")
                print("[警告] 点了发送但没校验到文本，请看 debug/ 里的截图确认。")
            try:
                if not current_chat and not strict_target:
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
