"""Persist keyword rules and dispatch matched messages."""

import structlog
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from threading import Lock
from typing import Literal

from pydantic import HttpUrl, model_validator

from .plugins import HANDLERS, PLUGINS, PluginActionError, WebhookError, call_webhook
from .reply import ContractModel, MessageEvent, NonEmpty, SendTask

log = structlog.get_logger("wechat_bot.automation")
MAX_MONITORED_GROUPS = 10


class GroupLimitError(ValueError):
    pass


class Rule(ContractModel):
    group_id: NonEmpty
    keyword: NonEmpty
    handler: NonEmpty = "fixed_reply"
    url: HttpUrl | None = None
    enabled: bool = True

    @model_validator(mode="after")
    def validate_handler(self):
        if self.handler == "webhook":
            if self.url is None:
                raise ValueError("Webhook URL required")
        elif self.handler not in HANDLERS or self.url is not None:
            raise ValueError("Invalid local handler")
        return self


class Automation:
    def __init__(self, path: Path):
        self.path = path
        self._schema_ready = False
        self._schema_lock = Lock()

    @contextmanager
    def database(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.row_factory = sqlite3.Row
            if not self._schema_ready:
                self._initialize(db)
            yield db

    def _initialize(self, db):
        with self._schema_lock:
            if self._schema_ready:
                return
            db.executescript("""
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS rules (
                    group_id TEXT NOT NULL, keyword TEXT NOT NULL, config TEXT NOT NULL,
                    PRIMARY KEY(group_id, keyword)
                );
                CREATE TABLE IF NOT EXISTS deliveries (
                    id INTEGER PRIMARY KEY, group_id TEXT NOT NULL, message_id TEXT NOT NULL,
                    handler TEXT NOT NULL, status TEXT NOT NULL, task TEXT, error TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(group_id, message_id)
                );
                CREATE TABLE IF NOT EXISTS reader_cursors (
                    group_id TEXT PRIMARY KEY, message_id TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reader_settings (
                    id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reader_interval (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    seconds INTEGER NOT NULL CHECK(seconds BETWEEN 1 AND 100)
                );
                CREATE TABLE IF NOT EXISTS monitored_groups (
                    group_id TEXT PRIMARY KEY, name TEXT NOT NULL, enabled INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS group_plugins (
                    group_id TEXT NOT NULL, plugin_id TEXT NOT NULL, enabled INTEGER NOT NULL,
                    PRIMARY KEY(group_id, plugin_id)
                );
                CREATE TABLE IF NOT EXISTS automation_migrations (name TEXT PRIMARY KEY);
                INSERT OR IGNORE INTO monitored_groups(group_id, name, enabled)
                    SELECT group_id, group_id, 1 FROM rules
                    WHERE NOT EXISTS (
                        SELECT 1 FROM automation_migrations WHERE name='separate_monitored_groups'
                    )
                    AND json_extract(config, '$.enabled') = 1
                    AND group_id LIKE '%@chatroom'
                    GROUP BY group_id ORDER BY group_id LIMIT 10;
                INSERT OR IGNORE INTO automation_migrations VALUES ('separate_monitored_groups');
                UPDATE monitored_groups SET enabled=0 WHERE group_id IN (
                    SELECT group_id FROM monitored_groups WHERE enabled=1
                    ORDER BY group_id LIMIT -1 OFFSET 10
                );
                CREATE TABLE IF NOT EXISTS delivery_inputs (
                    id INTEGER PRIMARY KEY, event TEXT NOT NULL, rule TEXT NOT NULL
                );
                COMMIT;
            """)
            self._schema_ready = True

    def monitoring_enabled(self, default: bool = False) -> bool:
        with self.database() as db:
            row = db.execute("SELECT enabled FROM reader_settings WHERE id=1").fetchone()
            return bool(row["enabled"]) if row is not None else default

    def monitoring_interval(self) -> int:
        with self.database() as db:
            row = db.execute("SELECT seconds FROM reader_interval WHERE id=1").fetchone()
            return row["seconds"] if row else 5

    def monitored_groups(self) -> list[dict]:
        with self.database() as db:
            return [dict(row) for row in db.execute(
                "SELECT group_id, name, enabled FROM monitored_groups ORDER BY name, group_id"
            )]

    def set_group_monitoring(self, group_id: str, name: str, enabled: bool):
        with self.database() as db:
            db.execute("BEGIN IMMEDIATE")
            if enabled and db.execute(
                "SELECT COUNT(*) FROM monitored_groups WHERE enabled=1 AND group_id<>?", (group_id,),
            ).fetchone()[0] >= MAX_MONITORED_GROUPS:
                raise GroupLimitError("最多同时监听 10 个群，请先暂停一个群")
            db.execute(
                "INSERT INTO monitored_groups VALUES (?, ?, ?) ON CONFLICT(group_id) "
                "DO UPDATE SET name=excluded.name, enabled=excluded.enabled", (group_id, name, enabled),
            )

    def group_plugins(self, group_id: str) -> dict[str, bool]:
        with self.database() as db:
            return {row["plugin_id"]: bool(row["enabled"]) for row in db.execute(
                "SELECT plugin_id, enabled FROM group_plugins WHERE group_id=?", (group_id,),
            )}

    def set_group_plugin(self, group_id: str, plugin_id: str, enabled: bool):
        with self.database() as db:
            db.execute(
                "INSERT INTO group_plugins VALUES (?,?,?) ON CONFLICT(group_id, plugin_id) "
                "DO UPDATE SET enabled=excluded.enabled", (group_id, plugin_id, enabled),
            )
            if enabled and plugin_id == "open_baidu":
                rule = Rule(group_id=group_id, keyword="测试", handler=plugin_id)
                db.execute(
                    "INSERT INTO rules VALUES (?,?,?) ON CONFLICT(group_id, keyword) DO NOTHING",
                    (group_id, rule.keyword, rule.model_dump_json()),
                )

    def set_monitoring(self, enabled: bool, interval_seconds: int | None = None):
        with self.database() as db:
            db.execute(
                "INSERT INTO reader_settings VALUES (1, ?) ON CONFLICT(id) "
                "DO UPDATE SET enabled=excluded.enabled", (enabled,),
            )
            if interval_seconds is not None:
                db.execute(
                    "INSERT INTO reader_interval VALUES (1, ?) ON CONFLICT(id) "
                    "DO UPDATE SET seconds=excluded.seconds", (interval_seconds,),
                )

    def save_rule(self, rule: Rule):
        with self.database() as db:
            db.execute(
                "INSERT INTO rules VALUES (?, ?, ?) ON CONFLICT(group_id, keyword) "
                "DO UPDATE SET config=excluded.config",
                (rule.group_id, rule.keyword, rule.model_dump_json()),
            )

    def rules(self) -> list[Rule]:
        with self.database() as db:
            return [Rule.model_validate_json(row["config"]) for row in db.execute(
                "SELECT config FROM rules ORDER BY group_id, keyword"
            )]

    def deliveries(self) -> list[dict]:
        with self.database() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM deliveries ORDER BY id DESC LIMIT 100"
            )]

    def cursor(self, group_id: str) -> str | None:
        with self.database() as db:
            row = db.execute("SELECT message_id FROM reader_cursors WHERE group_id=?", (group_id,)).fetchone()
            return row["message_id"] if row is not None else None

    def save_cursor(self, group_id: str, message_id: str):
        with self.database() as db:
            db.execute(
                "INSERT INTO reader_cursors VALUES (?, ?) ON CONFLICT(group_id) "
                "DO UPDATE SET message_id=excluded.message_id", (group_id, message_id),
            )

    def dispatch(self, event: MessageEvent, *, skip_fixed=False) -> Literal["unmatched", "duplicate", "pending", "acknowledged", "failed"]:
        with self.database() as db:
            row = db.execute(
                "SELECT config FROM rules WHERE group_id=? AND keyword=?",
                (event.group_id, event.text.strip()),
            ).fetchone()
            if row is None:
                return "unmatched"
            rule = Rule.model_validate_json(row["config"])
            if not rule.enabled or (skip_fixed and rule.handler == "fixed_reply"):
                return "unmatched"
            plugin = db.execute(
                "SELECT enabled FROM group_plugins WHERE group_id=? AND plugin_id=?",
                (event.group_id, rule.handler),
            ).fetchone()
            enabled = bool(plugin["enabled"]) if plugin is not None else PLUGINS[rule.handler]["default_enabled"]
            if rule.handler == "fixed_reply":
                from .group_plugins import config

                enabled = config(event.group_id, rule.handler, {rule.handler: enabled}).enabled
            if not enabled:
                return "unmatched"
            inserted = db.execute(
                "INSERT INTO deliveries(group_id,message_id,handler,status) VALUES (?,?,?,'processing') "
                "ON CONFLICT(group_id,message_id) DO NOTHING",
                (event.group_id, event.message_id, rule.handler),
            )
            if inserted.rowcount == 0:
                return "duplicate"
            delivery_id = inserted.lastrowid
            db.execute(
                "INSERT INTO delivery_inputs VALUES (?, ?, ?)",
                (delivery_id, event.model_dump_json(), rule.model_dump_json()),
            )

        return self.execute(delivery_id, rule, event)

    def retry(self, delivery_id: int) -> str | None:
        with self.database() as db:
            claimed = db.execute(
                "UPDATE deliveries SET status='processing', error=NULL "
                "WHERE id=? AND status='failed' AND EXISTS (SELECT 1 FROM delivery_inputs WHERE id=?)",
                (delivery_id, delivery_id),
            )
            if not claimed.rowcount:
                return None
            row = db.execute("SELECT event, rule FROM delivery_inputs WHERE id=?", (delivery_id,)).fetchone()
            event = MessageEvent.model_validate_json(row["event"])
            rule = Rule.model_validate_json(row["rule"])
        return self.execute(delivery_id, rule, event)

    def recover_interrupted(self):
        with self.database() as db:
            count = db.execute(
                "UPDATE deliveries SET status='failed', error='Interrupted' WHERE status='processing'"
            ).rowcount
        if count:
            log.warning("delivery_recovery", count=count)

    def execute(self, delivery_id: int, rule: Rule, event: MessageEvent) -> Literal["pending", "acknowledged", "failed"]:
        task = error = None
        try:
            reply = (
                call_webhook(rule.url, event, str(delivery_id))
                if rule.url is not None else HANDLERS[rule.handler](event)
            )
            status = "pending" if reply else "acknowledged"
            if reply:
                task = SendTask.from_event(event, reply).model_dump_json()
        except Exception as exc:
            status = "failed"
            error = str(exc) if isinstance(exc, (WebhookError, PluginActionError)) else type(exc).__name__
        with self.database() as db:
            db.execute(
                "UPDATE deliveries SET status=?,task=?,error=? WHERE id=?",
                (status, task, error, delivery_id),
            )
            if status != "failed":
                db.execute("DELETE FROM delivery_inputs WHERE id=?", (delivery_id,))
        log.info("keyword_delivery", delivery_id=delivery_id, handler=rule.handler, status=status, error=error)
        return status
