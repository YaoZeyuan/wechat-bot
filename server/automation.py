"""Persist keyword rules and dispatch matched messages."""

import structlog
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Literal

from pydantic import HttpUrl, model_validator

from .plugins import HANDLERS, WebhookError, call_webhook
from .reply import ContractModel, MessageEvent, NonEmpty, SendTask

log = structlog.get_logger("wechat_bot.automation")


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

    @contextmanager
    def database(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.row_factory = sqlite3.Row
            db.executescript("""
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
                CREATE TABLE IF NOT EXISTS delivery_inputs (
                    id INTEGER PRIMARY KEY, event TEXT NOT NULL, rule TEXT NOT NULL
                );
            """)
            yield db

    def monitoring_enabled(self, default: bool = False) -> bool:
        with self.database() as db:
            row = db.execute("SELECT enabled FROM reader_settings WHERE id=1").fetchone()
            return bool(row["enabled"]) if row is not None else default

    def set_monitoring(self, enabled: bool):
        with self.database() as db:
            db.execute(
                "INSERT INTO reader_settings VALUES (1, ?) ON CONFLICT(id) "
                "DO UPDATE SET enabled=excluded.enabled", (enabled,),
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

    def dispatch(self, event: MessageEvent) -> Literal["unmatched", "duplicate", "pending", "acknowledged", "failed"]:
        with self.database() as db:
            row = db.execute(
                "SELECT config FROM rules WHERE group_id=? AND keyword=?",
                (event.group_id, event.text.strip()),
            ).fetchone()
            if row is None:
                return "unmatched"
            rule = Rule.model_validate_json(row["config"])
            if not rule.enabled:
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
            error = str(exc) if isinstance(exc, WebhookError) else type(exc).__name__
        with self.database() as db:
            db.execute(
                "UPDATE deliveries SET status=?,task=?,error=? WHERE id=?",
                (status, task, error, delivery_id),
            )
            if status != "failed":
                db.execute("DELETE FROM delivery_inputs WHERE id=?", (delivery_id,))
        log.info("keyword_delivery", delivery_id=delivery_id, handler=rule.handler, status=status, error=error)
        return status
