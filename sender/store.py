"""SQLite 发送日志。"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path


def init_db(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.execute(
        """CREATE TABLE IF NOT EXISTS send_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target TEXT NOT NULL,
            text TEXT NOT NULL,
            ok INTEGER NOT NULL,
            message TEXT NOT NULL,
            dry_run INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )"""
    )
    conn.commit()
    return conn


def log_send(conn, target, text, ok, message, dry_run=False) -> None:
    conn.execute(
        "INSERT INTO send_log (target, text, ok, message, dry_run, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (target, text, 1 if ok else 0, message, 1 if dry_run else 0,
         datetime.now().astimezone().isoformat(timespec="seconds")),
    )
    conn.commit()
