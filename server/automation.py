"""Persist keyword rules and dispatch matched messages."""

import logging
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Literal

from pydantic import HttpUrl, model_validator

from .plugins import HANDLERS, call_webhook
from .reply import ContractModel, MessageEvent, NonEmpty, SendTask

log = logging.getLogger("uvicorn.error")


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
            """)
            yield db

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
            error = type(exc).__name__
        with self.database() as db:
            db.execute(
                "UPDATE deliveries SET status=?,task=?,error=? WHERE id=?",
                (status, task, error, delivery_id),
            )
        log.info("Keyword delivery id=%s handler=%s status=%s error=%s", delivery_id, rule.handler, status, error)
        return status
