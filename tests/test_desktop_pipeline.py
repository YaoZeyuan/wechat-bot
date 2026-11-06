import hashlib
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from sqlcipher3 import dbapi2

from server.automation import Automation, Rule
from server.polling import poll_group
from server.reply import SendTask
from server.reader import pending_tasks
from server.sqlcipher_reader import SqlCipherReader


class DesktopPipelineTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
