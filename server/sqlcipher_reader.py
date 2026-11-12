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
        self.populate_sender_names(items)
        return {"items": items, "has_more": has_more}

    def populate_sender_names(self, items, *, include_remarks=True, group_id=None):
        ids = sorted({item["sender"]["username"] for item in items if item["sender"]["username"]})
        group_names = self.group_nicknames(group_id) if ids and group_id else {}
        name_column = "COALESCE(NULLIF(remark,''), nick_name)" if include_remarks else "nick_name"
        names = {}
        if ids:
            with self.open(self.root / "contact" / "contact.db") as db:
                for offset in range(0, len(ids), 500):
                    batch = ids[offset:offset + 500]
                    placeholders = ",".join("?" for _ in batch)
                    names.update(db.execute(f"""
                        SELECT username, {name_column}
                        FROM contact WHERE username IN ({placeholders})
                    """, batch).fetchall())
        for item in items:
            sender = item["sender"]["username"]
            item["sender"]["display_name"] = group_names.get(sender) or names.get(sender) or (sender if include_remarks else None)

    def messages_in_range(self, session_id: str, *, start_seconds: int, end_seconds: int):
        """Read every stored row in the half-open time range, oldest first."""
        items = list(self.iter_messages(session_id, time_range=(start_seconds, end_seconds)))
        items.reverse()
        items.sort(key=lambda item: item["timestampMs"])
        self.populate_sender_names(items, include_remarks=False,
                                   group_id=session_id if session_id.endswith("@chatroom") else None)
        return items

    def messages(self, session_id: str, *, offset=0, limit=1, **filters):
        with closing(self.iter_messages(session_id, limit=offset + limit)) as rows:
            return {"items": list(islice(rows, offset, offset + limit))}

    def iter_messages(self, session_id: str, *, limit=-1, time_range: tuple[int, int] | None = None):
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
            lookup_dbs = []
            time_filter = "WHERE m.create_time >= ? AND m.create_time < ?" if time_range is not None else ""
            parameters = (*time_range, limit) if time_range is not None else (limit,)
            for path in shards:
                db = stack.enter_context(self.open(path))
                if not db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
                    continue
                if any(column[1] == "server_id" for column in db.execute(f'PRAGMA table_info("{table}")')):
                    lookup_dbs.append(db)
                cursor = db.execute(f"""
                    SELECT m.local_id, m.sort_seq, m.create_time, m.local_type,
                           m.message_content, n.user_name
                    FROM "{table}" AS m LEFT JOIN Name2Id AS n ON n.rowid=m.real_sender_id
                    {time_filter}
                    ORDER BY m.sort_seq DESC, m.local_id DESC LIMIT ?
                """, parameters)
                streams.append(tagged_rows(cursor, path.stem))
            merged = heapq.merge(
                *streams, key=lambda pair: (pair[0]["sort_seq"], pair[0]["local_id"], pair[1]), reverse=True,
            )
            def lookup_quote(server_id):
                if not server_id.isascii() or not server_id.isdecimal() or len(server_id) > 19:
                    return None
                for db in lookup_dbs:
                    original = db.execute(f"""
                        SELECT m.message_content, n.user_name
                        FROM "{table}" AS m LEFT JOIN Name2Id AS n ON n.rowid=m.real_sender_id
                        WHERE m.server_id=? LIMIT 1
                    """, (server_id,)).fetchone()
                    if original is not None:
                        return self.decode_content(original["message_content"], original["user_name"], 49)
                return None

            for row, shard in merged:
                yield self.message(row, shard, lookup_quote)

    @staticmethod
    def decode_content(content, sender, type_code):
        if type_code in (1, 49, 10000, 10002) and isinstance(content, bytes):
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
        return content

    def message(self, row, shard, lookup_quote=None):
        sender = row["user_name"]
        type_code = row["local_type"] & 0xFFFFFFFF
        content = self.decode_content(row["message_content"], sender, type_code)
        card = self.app_card(content, lookup_quote) if type_code == 49 and isinstance(content, str) else None
        if type_code in (10000, 10002):
            card = {"label": "系统消息", "title": self.system_message(content)}
        link = card if card and card.get("url") else None
        quote = card if card and "quotes" in card else None
        summary = card if card and "label" in card else None
        return {
            "messageId": f'{shard}:{row["local_id"]}',
            "timestampMs": row["create_time"] * 1000,
            "direction": "out" if sender == self.account else "in",
            "kind": "text" if type_code == 1 else "quote" if quote else "link" if link else "other",
            "type_code": row["local_type"],
            "sender": {"username": sender},
            "text": quote["title"] if quote else summary["title"] if summary else content if type_code == 1 and isinstance(content, str) else "",
            "link": link,
            "file_name": card["title"] if card and not link and not quote and not summary else None,
            "message_label": summary["label"] if summary else None,
            "forwarded": summary.get("forwarded", []) if summary else [],
            "quotes": quote["quotes"] if quote else [],
        }

    @staticmethod
    def system_message(content):
        if not isinstance(content, str):
            return ""
        text = re.sub(r"^[^\s:]+@chatroom:\r?\n", "", content).strip()
        if not text.startswith("<"):
            return text
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            return ""
        if root.get("type") == "revokemsg":
            notice = (root.findtext("./revokemsg/replacemsg") or root.findtext("./revokemsg/content") or "").strip()
            return notice if "撤回" in notice and not notice.startswith("<") else "撤回了一条消息"
        template = root.find(".//content_template")
        if template is not None:
            plain = (template.findtext("plain") or "").strip()
            if plain:
                return plain
            values = {}
            for link in template.findall("link_list/link"):
                names = [name.text.strip() for name in link.findall(".//nickname") if name.text and name.text.strip()]
                values[link.get("name")] = "、".join(names) or (link.findtext("plain") or link.findtext("title") or "").strip()
            return re.sub(r"\$([\w-]+)\$", lambda match: values.get(match[1]) or "…",
                          (template.findtext("template") or "").strip())
        for path in (".//replacemsg", ".//plain", ".//text"):
            value = (root.findtext(path) or "").strip()
            if value:
                return value
        return ""

    @staticmethod
    def app_element(content: str):
        if len(content) > 1024 * 1024:
            return None
        content = re.sub(r"^(<msg>\s*)<\?xml\s+[^?]*\?>", r"\1", content.strip(), count=1)
        root = ET.fromstring(content)
        return root if root.tag == "appmsg" else root.find("appmsg")

    @staticmethod
    def quote_lines(app, lookup_quote=None, *, first_tag="refermsg"):
        labels = {"3": "图片", "34": "语音", "43": "视频", "47": "动画表情", "42": "名片", "48": "位置"}
        lines = []
        seen = set()
        for depth in range(3):
            refer = app.find(first_tag if depth == 0 else "refermsg")
            if refer is None:
                break
            server_id = (refer.findtext("svrid") or "").strip()
            if server_id and server_id != "0":
                if server_id in seen:
                    break
                seen.add(server_id)
            sender = (refer.findtext("displayname") or "").strip()
            if sender.startswith("wxid_"):
                sender = ""
            kind = (refer.findtext("type") or "").strip()
            body = refer.find("content")
            raw = (body.text or "").strip() if body is not None else ""
            nested = None
            if kind in {"49", "57", "2001"}:
                if body is not None:
                    nested = body.find("appmsg")
                    if nested is None:
                        nested = body.find("msg/appmsg")
                if nested is None and raw.startswith("<"):
                    try:
                        nested = SqlCipherReader.app_element(raw)
                    except ET.ParseError:
                        pass
                nested_kind = (nested.findtext("type") or "").strip() if nested is not None else ""
                nested_refer = nested.find("refermsg") if nested_kind == "57" else None
                if (depth < 2 and lookup_quote and server_id and server_id != "0"
                        and (kind == "57" or nested_kind == "57")
                        and (nested_refer is None or not len(nested_refer))):
                    original = lookup_quote(server_id)
                    if original:
                        try:
                            restored = SqlCipherReader.app_element(original)
                            if restored is not None and (restored.findtext("type") or "").strip() == "57":
                                nested = restored
                        except ET.ParseError:
                            pass
                if kind == "2001" or nested_kind == "2001":
                    packet = nested if nested is not None else body
                    greeting = (packet.findtext(".//receivertitle") or packet.findtext(".//sendertitle") or "").strip() if packet is not None else ""
                    raw = f"[红包] {greeting}" if greeting else "[红包]"
                else:
                    raw = (nested.findtext("title") or "").strip() if nested is not None else ""
                    if kind == "49" and not raw and nested_kind != "57":
                        raw = "[链接]"
            label = labels.get(kind)
            lines.append({"sender": sender, "text": f"[{label}]" if label else raw or refer.findtext("referdesc") or "[引用消息]"})
            if depth == 2 or nested is None or (nested.findtext("type") or "").strip() != "57":
                break
            next_refer = nested.find("refermsg")
            if next_refer is None or not len(next_refer):
                break
            app = nested
        return lines

    @staticmethod
    def forwarded_items(container, depth=1):
        if container is None:
            return []
        root = container.find("recordinfo")
        if root is None:
            raw = (container.text or "").strip()
            try:
                root = ET.fromstring(raw) if raw else container
            except ET.ParseError:
                return []
        labels = {"2": "图片", "3": "语音", "4": "视频", "5": "链接", "6": "位置", "7": "音乐",
                  "8": "文件", "10": "名片", "15": "表情", "34": "语音", "43": "视频", "47": "表情"}
        items = []
        for item in root.findall("datalist/dataitem"):
            kind = item.get("datatype", "")
            title = (item.findtext("datatitle") or "").strip()
            description = (item.findtext("datadesc") or "").strip()
            nested = item.find("recordxml")
            if nested is None:
                nested = item.find("recorditem")
            is_forward = kind == "17" or nested is not None
            label = "聊天记录" if is_forward and depth == 2 else "合并转发的聊天记录" if is_forward else labels.get(kind, "其他消息")
            body = (description or title or "（空白文本）") if kind == "1" and not is_forward else f"[{label}]"
            if not is_forward and kind != "1" and (title or description):
                body += " " + "\n".join(filter(None, (title, description)))
            url = (item.findtext("link") or "").strip()
            if kind == "5" and SqlCipherReader.is_web_url(url):
                body += f"\n<{url}>"
            items.append({
                "depth": depth, "sender": (item.findtext("sourcename") or "").strip(),
                "time": (item.findtext("sourcetime") or "").strip(), "text": body,
                "quotes": SqlCipherReader.quote_lines(item, first_tag="refermsgitem"),
            })
            if is_forward and depth < 2:
                items.extend(SqlCipherReader.forwarded_items(nested, depth + 1))
        return items

    @staticmethod
    def app_card(content: str, lookup_quote=None):
        try:
            app = SqlCipherReader.app_element(content)
            if app is None:
                return None
            kind = (app.findtext("type") or "").strip()
            if kind == "57":
                return {"title": (app.findtext("title") or "").strip(), "quotes": SqlCipherReader.quote_lines(app, lookup_quote)}
            if kind == "51":
                title = (app.findtext("title") or "").strip() or (app.findtext("finderFeed/desc") or "").strip()
                return {"label": "视频号卡片", "title": title}
            if kind == "50":
                nickname = (app.findtext("findernamecard/nickname") or "").strip()
                account = (app.findtext("findernamecard/username") or "").strip()
                title = " · ".join(filter(None, (nickname, account))) or (app.findtext("title") or "").strip()
                return {"label": "视频号名片", "title": title}
            if kind in {"1", "8", "19", "53", "62"}:
                labels = {"1": "", "8": "自定义表情", "19": "合并转发的聊天记录", "53": "视频", "62": "拍一拍"}
                title = (app.findtext("title") or "").strip()
                return {"label": labels[kind], "title": title,
                        "forwarded": SqlCipherReader.forwarded_items(app.find("recorditem")) if kind == "19" else []}
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
