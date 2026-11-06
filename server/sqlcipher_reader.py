"""Read WeChat 4 Windows databases directly using SQLCipher."""

import hashlib
import re
from contextlib import closing, contextmanager
from functools import lru_cache
from pathlib import Path

from .ciphertalk import SourceError
from .ciphertalk_cli import CipherTalkCLI


class SqlCipherReader:
    @classmethod
    def discover(cls):
        env = CipherTalkCLI.local_settings()
        if not all(env.get(key) for key in ("MIYU_DB_PATH", "MIYU_KEY_HEX", "MIYU_WXID")):
            raise SourceError("请提供微信数据库路径、密钥和账号")
        return cls(Path(env["MIYU_DB_PATH"]).expanduser().resolve(), env["MIYU_KEY_HEX"], env["MIYU_WXID"])

    def __init__(self, path: Path, key_hex: str, account: str):
        if not re.fullmatch("[0-9a-fA-F]{64}", key_hex):
            raise SourceError("需要 64 位十六进制微信数据库密钥")
        self.root = path / "db_storage" if (path / "db_storage").is_dir() else path
        self.key = bytes.fromhex(key_hex)
        self.account = account

    @lru_cache(maxsize=64)
    def derive_key(self, salt: bytes) -> str:
        return hashlib.pbkdf2_hmac("sha512", self.key, salt, 256000, 32).hex()

    @contextmanager
    def open(self, path: Path):
        try:
            from sqlcipher3 import dbapi2
        except ImportError:
            raise SourceError("请安装 SQLCipher Python 依赖") from None
        try:
            with path.open("rb") as stream:
                key = self.derive_key(stream.read(16))
            with closing(dbapi2.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
                db.row_factory = dbapi2.Row
                db.execute("PRAGMA cipher_log_level=NONE")
                db.execute(f"""PRAGMA key = "x'{key}'" """)
                db.execute("PRAGMA cipher_compatibility=4")
                db.execute("PRAGMA query_only=ON")
                yield db
        except (OSError, dbapi2.Error):
            raise SourceError(f"无法只读查询 {path.name}，请检查路径、密钥和数据库版本") from None

    def sessions(self, *, q="", limit=50):
        with self.open(self.root / "contact" / "contact.db") as db:
            rows = db.execute("""
                SELECT username, COALESCE(NULLIF(remark,''), NULLIF(nick_name,''), username) AS title
                FROM contact WHERE username LIKE '%@chatroom'
            """).fetchall()
        matches = [row for row in rows if q.casefold() in row["title"].casefold() or q in row["username"]]
        matches.sort(key=lambda row: (row["title"], row["username"]))
        return {
            "items": [
                {"sessionId": row["username"], "displayName": row["title"], "kind": "group"}
                for row in matches[:limit]
            ],
            "hasMore": len(matches) > limit,
        }

    def messages(self, session_id: str, *, offset=0, limit=1, **filters):
        table = "Msg_" + hashlib.md5(session_id.encode(), usedforsecurity=False).hexdigest()
        rows = []
        shards = sorted((self.root / "message").glob("message_*.db"))
        shards = [path for path in shards if re.fullmatch(r"message_\d+\.db", path.name)]
        if not shards:
            raise SourceError("未找到微信消息分库")
        for path in shards:
            with self.open(path) as db:
                if not db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
                    continue
                for row in db.execute(f"""
                    SELECT m.local_id, m.sort_seq, m.create_time, m.local_type,
                           m.message_content, n.user_name
                    FROM "{table}" AS m LEFT JOIN Name2Id AS n ON n.rowid=m.real_sender_id
                    ORDER BY m.sort_seq DESC, m.local_id DESC LIMIT ?
                """, (offset + limit,)):
                    rows.append((row, path.stem))
        rows.sort(key=lambda pair: (pair[0]["sort_seq"], pair[0]["local_id"], pair[1]), reverse=True)
        return {"items": [self.message(row, shard) for row, shard in rows[offset:offset + limit]]}

    def message(self, row, shard):
        sender = row["user_name"]
        content = row["message_content"]
        if row["local_type"] == 1 and isinstance(content, bytes):
            if content.startswith(bytes.fromhex("28b52ffd")):
                import zstandard

                try:
                    content = zstandard.ZstdDecompressor().decompress(content, max_output_size=1024 * 1024)
                except zstandard.ZstdError:
                    raise SourceError("无法解压微信文本消息") from None
            content = content.decode("utf-8", errors="replace")
        text = content if row["local_type"] == 1 and isinstance(content, str) else ""
        if sender and text.startswith(sender + ":\n"):
            text = text[len(sender) + 2:]
        return {
            "messageId": f'{shard}:{row["local_id"]}',
            "timestampMs": row["create_time"] * 1000,
            "direction": "out" if sender == self.account else "in",
            "kind": "text" if row["local_type"] == 1 else "other",
            "sender": {"username": sender},
            "text": text,
        }
