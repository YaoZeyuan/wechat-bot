"""Read WeChat 4 Windows databases directly using SQLCipher."""

import hashlib
import heapq
import re
import xml.etree.ElementTree as ET
from contextlib import ExitStack, closing, contextmanager
from functools import lru_cache
from itertools import islice
from pathlib import Path
from urllib.parse import urlsplit

from .ciphertalk import SourceError
from .ciphertalk_cli import CipherTalkCLI


def protobuf_strings(data: bytes):
    """Read length-delimited fields from WeChat's group member records."""
    index = 0

    def varint():
        nonlocal index
        result = 0
        for shift in range(0, 70, 7):
            if index == len(data):
                break
            byte = data[index]
            index += 1
            result |= (byte & 127) << shift
            if byte < 128:
                return result
        raise ValueError("invalid varint")

    while index < len(data):
        tag = varint()
        wire = tag & 7
        if wire == 0:
            varint()
            continue
        if wire == 2:
            size = varint()
        elif wire in (1, 5):
            size = 8 if wire == 1 else 4
        else:
            raise ValueError("unsupported protobuf wire type")
        end = index + size
        if end > len(data):
            raise ValueError("truncated protobuf field")
        if wire == 2:
            yield tag >> 3, data[index:end]
        index = end

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
                SELECT username, remark, nick_name,
                       COALESCE(NULLIF(remark,''), NULLIF(nick_name,''), username) AS title
                FROM contact WHERE username LIKE '%@chatroom'
            """).fetchall()
        matches = [row for row in rows if any(q.casefold() in (row[field] or "").casefold()
                                             for field in ("remark", "nick_name", "username"))]
        matches.sort(key=lambda row: (row["title"], row["nick_name"] or "", row["username"]))
        return {
            "items": [
                {"sessionId": row["username"], "displayName": row["title"], "kind": "group",
                 "original_name": row["nick_name"] or "", "remark": row["remark"] or ""}
                for row in matches[:limit]
            ],
            "hasMore": len(matches) > limit,
        }

    def conversation_details(self, session_ids: list[str]) -> dict:
        if not session_ids:
            return {}
        placeholders = ",".join("?" for _ in session_ids)
        with self.open(self.root / "contact" / "contact.db") as db:
            rows = db.execute(
                f"SELECT username, remark, nick_name FROM contact WHERE username IN ({placeholders})",
                session_ids,
            ).fetchall()
        return {row["username"]: {
            "original_name": row["nick_name"] or "", "remark": row["remark"] or "",
        } for row in rows}

    def group_members(self, group_id: str) -> set[str]:
        with self.open(self.root / "contact" / "contact.db") as db:
            if not db.execute("SELECT 1 FROM chat_room WHERE username=?", (group_id,)).fetchone():
                raise SourceError("本地数据库中没有该群的当前成员名单")
            rows = db.execute("""
                SELECT c.username
                FROM chat_room r JOIN chatroom_member m ON m.room_id = r.id
                LEFT JOIN contact c ON c.id = m.member_id
                WHERE r.username = ?
            """, (group_id,)).fetchall()
        if not rows or any(not row["username"] for row in rows):
            raise SourceError("本地数据库中的当前群成员名单不完整")
        return {row["username"] for row in rows}

    def group_nicknames(self, group_id: str) -> dict[str, str]:
        with self.open(self.root / "contact" / "contact.db") as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='chat_room'").fetchone():
                return {}
            columns = {row[1] for row in db.execute("PRAGMA table_info(chat_room)")}
            if not {"username", "ext_buffer"} <= columns:
                return {}
            row = db.execute("SELECT ext_buffer FROM chat_room WHERE username=?", (group_id,)).fetchone()
        if not row or not isinstance(row[0], bytes):
            return {}

        names = {}
        try:
            for field, member in protobuf_strings(row[0]):
                if field != 1:
                    continue
                values = dict(protobuf_strings(member))
                username = values.get(1, b"").decode("utf-8")
                nickname = values.get(2, b"").decode("utf-8")
                if username and nickname:
                    names[username] = nickname
        except (ValueError, UnicodeError):
            return {}
        return names

    def conversations(self, *, q: str, kind: str, offset: int, limit: int):
        matches = []
        skipped = 0
        search = q.casefold()
        with self.open(self.root / "session" / "session.db") as db:
            sessions = db.execute("""
                SELECT username, last_timestamp FROM SessionTable
                WHERE username <> '' AND substr(username, 1, 3) <> 'gh_'
                  AND username <> 'brandsessionholder'
                  AND (? = 'all' OR (username LIKE '%@chatroom') = (? = 'group'))
                ORDER BY sort_timestamp DESC, username
            """, (kind, kind))
            while rows := sessions.fetchmany(100):
                contacts = self.conversation_details([row["username"] for row in rows])
                for row in rows:
                    username = row["username"]
                    details = contacts.get(username, {})
                    original = details.get("original_name", "")
                    remark = details.get("remark", "")
                    if not any(search in value.casefold() for value in (username, original, remark)):
                        continue
                    if skipped < offset:
                        skipped += 1
                        continue
                    if len(matches) == limit:
                        return {"items": matches, "has_more": True}
                    matches.append({
                        "id": username, "name": remark or original or username,
                        "kind": "group" if username.endswith("@chatroom") else "private",
                        "timestamp_ms": (row["last_timestamp"] or 0) * 1000,
                    })
        return {"items": matches, "has_more": False}

    def history(self, session_id: str, *, offset: int, limit: int):
        items = self.messages(session_id, offset=offset, limit=limit + 1)["items"]
        has_more = len(items) > limit
        items = items[:limit]
        ids = sorted({item["sender"]["username"] for item in items if item["sender"]["username"]})
        names = {}
        if ids:
            placeholders = ",".join("?" for _ in ids)
            with self.open(self.root / "contact" / "contact.db") as db:
                names = dict(db.execute(f"""
                    SELECT username, COALESCE(NULLIF(remark,''), NULLIF(nick_name,''), username)
                    FROM contact WHERE username IN ({placeholders})
                """, ids).fetchall())
        for item in items:
            sender = item["sender"]["username"]
            item["sender"]["display_name"] = names.get(sender) or sender
        return {"items": items, "has_more": has_more}

    def messages(self, session_id: str, *, offset=0, limit=1, **filters):
        with closing(self.iter_messages(session_id, limit=offset + limit)) as rows:
            return {"items": list(islice(rows, offset, offset + limit))}

    def iter_messages(self, session_id: str, *, limit=-1):
        table = "Msg_" + hashlib.md5(session_id.encode(), usedforsecurity=False).hexdigest()
        shards = sorted((self.root / "message").glob("message_*.db"))
        shards = [path for path in shards if re.fullmatch(r"message_\d+\.db", path.name)]
        if not shards:
            raise SourceError("未找到微信消息分库")
        def tagged_rows(cursor, shard):
            for row in cursor:
                yield row, shard

        with ExitStack() as stack:
            streams = []
            for path in shards:
                db = stack.enter_context(self.open(path))
                if not db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
                    continue
                cursor = db.execute(f"""
                    SELECT m.local_id, m.sort_seq, m.create_time, m.local_type,
                           m.message_content, n.user_name
                    FROM "{table}" AS m LEFT JOIN Name2Id AS n ON n.rowid=m.real_sender_id
                    ORDER BY m.sort_seq DESC, m.local_id DESC LIMIT ?
                """, (limit,))
                streams.append(tagged_rows(cursor, path.stem))
            merged = heapq.merge(
                *streams, key=lambda pair: (pair[0]["sort_seq"], pair[0]["local_id"], pair[1]), reverse=True,
            )
            for row, shard in merged:
                yield self.message(row, shard)

    def message(self, row, shard):
        sender = row["user_name"]
        content = row["message_content"]
        type_code = row["local_type"] & 0xFFFFFFFF
        if type_code in (1, 49) and isinstance(content, bytes):
            if content.startswith(bytes.fromhex("28b52ffd")):
                import zstandard

                try:
                    content = zstandard.ZstdDecompressor().decompress(content, max_output_size=1024 * 1024)
                except zstandard.ZstdError:
                    if type_code == 1:
                        raise SourceError("无法解压微信文本消息") from None
                    content = b""
            content = content.decode("utf-8", errors="replace")
        if isinstance(content, str) and sender and content.startswith(sender + ":\n"):
            content = content[len(sender) + 2:]
        card = self.app_card(content) if type_code == 49 and isinstance(content, str) else None
        link = card if card and card.get("url") else None
        return {
            "messageId": f'{shard}:{row["local_id"]}',
            "timestampMs": row["create_time"] * 1000,
            "direction": "out" if sender == self.account else "in",
            "kind": "text" if type_code == 1 else "link" if link else "other",
            "type_code": row["local_type"],
            "sender": {"username": sender},
            "text": content if type_code == 1 and isinstance(content, str) else "",
            "link": link,
            "file_name": card["title"] if card and not link else None,
        }

    @staticmethod
    def app_card(content: str):
        if len(content) > 1024 * 1024:
            return None
        try:
            root = ET.fromstring(content.strip())
            app = root if root.tag == "appmsg" else root.find("appmsg")
            if app is None:
                return None
            kind = (app.findtext("type") or "").strip()
            title = (app.findtext("title") or "").strip()[:300]
            if kind == "6":
                return {"title": title}
            if kind not in {"4", "5"}:
                return None
            url = (app.findtext("url") or "").strip()
            if not SqlCipherReader.is_web_url(url):
                return None
            cover = (app.findtext(".//coverpicimageurl") or "").strip()
            return {
                "url": url, "title": title,
                "description": (app.findtext("des") or "").strip()[:600],
                "source": (app.findtext("sourcedisplayname") or "").strip()[:120],
                "cover": cover if SqlCipherReader.is_web_url(cover) else "",
            }
        except (ET.ParseError, ValueError):
            return None

    @staticmethod
    def is_web_url(url: str) -> bool:
        parts = urlsplit(url)
        return parts.scheme in {"http", "https"} and bool(parts.hostname) and not any(
            char.isspace() or ord(char) < 32 for char in url
        )
