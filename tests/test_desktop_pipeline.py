import hashlib
import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from sqlcipher3 import dbapi2
from fastapi import HTTPException

from server.automation import Automation, Rule
from server import reader
from server import export_history
from server.polling import poll_group
from server.reply import SendTask
from server.reader import TaskResult, claim_task, finish_task, pending_tasks
from server.sqlcipher_reader import SqlCipherReader


class DesktopPipelineTest(unittest.TestCase):
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
