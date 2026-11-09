import hashlib
import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from sqlcipher3 import dbapi2
from fastapi import HTTPException
from pydantic import ValidationError

from server.automation import Automation, GroupLimitError, Rule
from server import reader
from server import export_history
from server.polling import poll_group
from server.reply import SendTask
from server.reader import TaskResult, claim_task, finish_task, pending_tasks
from server.sqlcipher_reader import SqlCipherReader
from server.wechat_scan_worker import image_template


class DesktopPipelineTest(unittest.TestCase):
    def test_baidu_plugin_opens_once_only_after_real_dispatch(self):
        from server.reply import MessageEvent

        with tempfile.TemporaryDirectory() as tmp:
            bot = Automation(Path(tmp) / "automation.db")
            group = "fixture@chatroom"
            bot.set_group_monitoring(group, "fixture", True)
            bot.set_group_plugin(group, "open_baidu", True)
            rule, = bot.rules()
            event = MessageEvent(group_id=group, message_id="1", sender_id="member",
                                 timestamp_ms=1, text="测试")
            with patch("server.plugins.webbrowser.open", return_value=True) as open_page:
                self.assertTrue(reader.preview_rule(reader.RulePreview(rule=rule, text="测试"))["matched"])
                open_page.assert_not_called()
                self.assertEqual(bot.dispatch(event), "acknowledged")
                self.assertEqual(bot.dispatch(event), "duplicate")
                open_page.assert_called_once_with("https://www.baidu.com/", new=2)

    def test_manual_test_plugin_uses_current_phone_chat(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = Automation(Path(tmp) / "automation.db")
            group = "fixture@chatroom"
            bot.set_group_monitoring(group, "fixture", True)
            with patch.object(reader, "automation", bot), patch.object(reader, "history_source") as source:
                source.return_value.conversation_details.return_value = {
                    group: {"original_name": "测试群", "remark": "备注"},
                }
                with patch.object(reader, "create_sender") as create:
                    create.return_value.send.return_value = True
                    self.assertEqual(reader.test_send_to_group(group), {"ok": True})
                    create.assert_called_once_with(dry_run=False)
                    create.return_value.connect.assert_called_once()
                    create.return_value.send.assert_called_once_with("测试群", "测试成功", current_chat=True)

    def test_group_limit_and_plugin_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = Automation(Path(tmp) / "automation.db")
            for number in range(10):
                bot.set_group_monitoring(f"{number}@chatroom", "fixture", True)
            with self.assertRaises(GroupLimitError):
                bot.set_group_monitoring("10@chatroom", "fixture", True)
            bot.set_group_monitoring("0@chatroom", "fixture", False)
            bot.set_group_monitoring("10@chatroom", "fixture", True)
            for group in ("1@chatroom", "2@chatroom"):
                bot.save_rule(Rule(group_id=group, keyword="test"))
            bot.set_group_plugin("1@chatroom", "fixed_reply", False)
            from server.reply import MessageEvent
            event = MessageEvent(group_id="1@chatroom", message_id="1", sender_id="sender",
                                 timestamp_ms=1, text="test")
            self.assertEqual(bot.dispatch(event), "unmatched")
            self.assertEqual(bot.dispatch(event.model_copy(update={"group_id": "2@chatroom"})), "pending")
            bot.save_rule(Rule(group_id="2@chatroom", keyword="hook", handler="webhook",
                               url="http://127.0.0.1:1/hook"))
            hook_event = event.model_copy(update={"group_id": "2@chatroom", "message_id": "2", "text": "hook"})
            with patch("server.automation.call_webhook", return_value=None) as hook:
                self.assertEqual(bot.dispatch(hook_event), "unmatched")
                hook.assert_not_called()
                bot.set_group_plugin("2@chatroom", "webhook", True)
                self.assertEqual(bot.dispatch(hook_event), "acknowledged")
                hook.assert_called_once()

    def test_group_monitoring_is_independent_from_keyword_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "automation.db"
            bot = Automation(path)
            group = "fixture@chatroom"
            bot.save_rule(Rule(group_id=group, keyword="test"))
            self.assertEqual(bot.monitored_groups(), [])
            bot.set_group_monitoring(group, "fixture", True)
            bot.save_rule(Rule(group_id=group, keyword="test", enabled=False))
            self.assertTrue(Automation(path).monitored_groups()[0]["enabled"])
            bot.set_group_monitoring(group, "fixture", False)
            self.assertFalse(Automation(path).monitored_groups()[0]["enabled"])
            self.assertEqual(len(bot.rules()), 1)

    def test_poll_interval_persists_and_rejects_out_of_range_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "automation.db"
            bot = Automation(path)
            self.assertEqual(bot.monitoring_interval(), 5)
            for seconds in (1, 100):
                settings = reader.MonitoringSettings(enabled=False, interval_seconds=seconds)
                bot.set_monitoring(settings.enabled, settings.interval_seconds)
                self.assertEqual(Automation(path).monitoring_interval(), seconds)
            for seconds in (0, 101):
                with self.assertRaises(ValidationError):
                    reader.MonitoringSettings(enabled=False, interval_seconds=seconds)

    def test_database_scan_reports_zero_read_without_suggesting_login(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contact = root / "db_storage/contact/contact.db"
            contact.parent.mkdir(parents=True)
            contact.touch()
            dll = root / "wechat_key_tool.dll"
            dll.touch()
            reply = {"dll_loaded": True, "challenge_created": True, "error": "no_key",
                     "diagnostics": {"auth": True, "pids": 1, "opened": 0, "bytes": 0}}
            with patch.dict("os.environ", {"WECHAT_BOT_KEY_EXTRACTION": "1", "WECHAT_BOT_SCANNER_DLL": str(dll)}):
                with patch.object(reader.sys, "platform", "win32"), patch.object(reader, "key_progress", {}):
                    with patch.object(reader.subprocess, "run") as run:
                        run.return_value.returncode = 0
                        run.return_value.stdout = json.dumps(reply)
                        selection = reader.ReaderProfile(database_path=tmp, account="fixture")
                        with self.assertRaises(HTTPException) as failure:
                            reader.scan_key("database", selection)
                        self.assertIn("未读取到进程数据", failure.exception.detail)
                        self.assertIn("dll_loaded", [step["stage"] for step in reader.get_key_progress()["steps"]])

    def test_image_template_extracts_xor_and_ciphertext(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            block = bytes(range(16))
            (folder / "fixture_t.dat").write_bytes(
                b"\x07\x08V2\x08\x07" + b"\x00" * 9 + block
                + bytes((0x23 ^ 0xFF, 0x23 ^ 0xD9))
            )
            self.assertEqual(image_template(folder), (0x23, block))

    def test_local_reader_profile_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "local-data" / "reader-profile.json"
            with patch.object(reader, "profile_path", path):
                self.assertIsNone(reader.get_profile())
                selection = reader.ReaderProfile(database_path=tmp, account="wxid_fixture")
                reader.save_account_selection(selection)
                self.assertEqual(reader.get_account_selection(), selection)
                self.assertIsNone(reader.load_profile())
                profile = reader.StoredReaderProfile(
                    database_path=tmp, account="wxid_fixture", database_key="a" * 64, image_xor_key="0x23",
                )
                reader.save_profile(profile)
                self.assertEqual(json.loads(path.read_text())["database_key"], "a" * 64)
                self.assertEqual(json.loads(path.read_text())["image_xor_key"], "0x23")
                self.assertEqual(reader.load_profile(), profile)

    def test_key_progress_retains_hook_success_after_completion(self):
        with patch.object(reader, "key_progress", {}), patch.dict("os.environ", {"WECHAT_BOT_KEY_EXTRACTION": "1"}):
            with patch.object(reader, "CipherTalkCLI") as factory:
                tool = factory.return_value
                tool.restart_wechat.return_value = 123

                def extract(pid, report):
                    self.assertEqual(pid, 123)
                    report("dll_loaded", "loaded")
                    report("hook_initialized", "initialized")
                    return "a" * 64

                tool.extract_with_progress.side_effect = extract
                self.assertEqual(reader.extract_key(), {"key": "a" * 64})
                progress = reader.get_key_progress()
                self.assertEqual(progress["stage"], "complete")
                self.assertIn("hook_initialized", [step["stage"] for step in progress["steps"]])
                self.assertNotIn("a" * 64, json.dumps(progress))

    def test_encrypted_new_group_message_creates_one_task(self):
        key = bytes(range(32))
        group = "test@chatroom"
        table = "Msg_" + hashlib.md5(group.encode(), usedforsecurity=False).hexdigest()
        salt = bytes(range(16))
        derived = hashlib.pbkdf2_hmac("sha512", key, salt, 256000, 32)
        raw_key = (derived + salt).hex()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            message_dir = root / "message"
            message_dir.mkdir()
            path = message_dir / "message_0.db"
            source = SqlCipherReader(root, key.hex(), "self")
            automation_path = root / "automation.db"
            bot = Automation(automation_path)
            bot.save_rule(Rule(group_id=group, keyword="测试"))
            with closing(dbapi2.connect(path)) as db:
                db.execute(f"""PRAGMA key = "x'{raw_key}'" """)
                db.execute("PRAGMA cipher_compatibility=4")
                db.execute("CREATE TABLE Name2Id(user_name TEXT)")
                db.execute("INSERT INTO Name2Id VALUES ('member'), ('self')")
                db.execute(f"""
                    CREATE TABLE "{table}"(
                        local_id INTEGER, sort_seq INTEGER, create_time INTEGER, local_type INTEGER,
                        message_content TEXT, real_sender_id INTEGER
                    )
                """)
                insert = f'INSERT INTO "{table}" VALUES (?, ?, ?, 1, ?, ?)'
                db.execute(insert, (1, 1, 1, "member:\n测试", 1))
                db.commit()
                self.assertEqual(poll_group(source, bot, group), 0)
                db.execute(insert, (2, 2, 2, "member:\n测试", 1))
                db.execute(insert, (3, 3, 3, "测试", 2))
                db.commit()
                self.assertEqual(poll_group(source, bot, group), 2)
                restarted = Automation(automation_path)
                self.assertEqual(poll_group(source, restarted, group), 0)
                deliveries = restarted.deliveries()
                self.assertEqual(len(deliveries), 1)
                task = SendTask.model_validate_json(deliveries[0]["task"])
                self.assertEqual(task.trigger_message_id, "message_0:2")
                self.assertEqual(task.reply.content.text, "测试成功")
                with patch("server.reader.automation", restarted):
                    tasks = pending_tasks()
                self.assertEqual(len(tasks), 1)
                self.assertEqual(tasks[0].delivery_id, deliveries[0]["id"])
                self.assertEqual(tasks[0].task, task)
                with patch("server.reader.automation", restarted):
                    delivery_id = tasks[0].delivery_id
                    self.assertEqual(claim_task(delivery_id), tasks[0])
                    self.assertEqual(pending_tasks(), [])
                    with self.assertRaises(HTTPException):
                        claim_task(delivery_id)
                    result = TaskResult(status="sent")
                    self.assertEqual(finish_task(delivery_id, result), result)
                    self.assertEqual(finish_task(delivery_id, result), result)
            contact_dir = root / "contact"
            contact_dir.mkdir()
            with closing(dbapi2.connect(contact_dir / "contact.db")) as db:
                db.execute(f"""PRAGMA key = "x'{raw_key}'" """)
                db.execute("CREATE TABLE contact(username TEXT, remark TEXT, nick_name TEXT)")
                db.execute("INSERT INTO contact VALUES (?, '', 'fixture group')", (group,))
                db.commit()
            output = root / "history.jsonl"
            argv = ["export_history", "--snapshot", tmp, "--account", "self",
                    "--group", "fixture group", "--output", str(output)]
            with patch("sys.argv", argv), patch.object(export_history, "load_profile", return_value=None):
                with patch("getpass.getpass", return_value=key.hex()):
                    export_history.main()
            rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 3)
            self.assertTrue(all(row["text"] == "测试" for row in rows))


if __name__ == "__main__":
    unittest.main()
