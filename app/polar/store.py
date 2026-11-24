"""Persistencia mínima Polar en SQLite (usa el mismo archivo que users.db)."""
from __future__ import annotations
import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from typing import Optional
from dataclasses import dataclass
from datetime import datetime, timezone

_SCHEMA = """
CREATE TABLE IF NOT EXISTS polar_accounts (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    polar_user_id INTEGER NOT NULL,
    access_token TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_sync_at TEXT
);
CREATE TABLE IF NOT EXISTS polar_imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    polar_exercise_id TEXT NOT NULL,
    video_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(user_id, polar_exercise_id)
);
"""

@dataclass
class PolarAccount:
    user_id: int
    polar_user_id: int
    access_token: str
    created_at: str
    last_sync_at: Optional[str] = None

class PolarStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init()

    def _init(self):
        with closing(sqlite3.connect(self.db_path, check_same_thread=False)) as conn:
            conn.executescript(_SCHEMA)
            conn.commit()

    def _connect(self):
        # cada hilo su propia conexión; el lock protege escrituras
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def save_account(self, user_id: int, polar_user_id: int, access_token: str):
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, closing(self._connect()) as conn:
            conn.execute("INSERT OR REPLACE INTO polar_accounts(user_id, polar_user_id, access_token, created_at, last_sync_at) VALUES(?,?,?,?,?)",
                         (user_id, polar_user_id, access_token, now, None))
            conn.commit()

    def get_account(self, user_id: int) -> Optional[PolarAccount]:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM polar_accounts WHERE user_id=?", (user_id,)).fetchone()
            if not row:
                return None
            return PolarAccount(user_id=row["user_id"], polar_user_id=row["polar_user_id"],
                                access_token=row["access_token"], created_at=row["created_at"], last_sync_at=row["last_sync_at"])

    def delete_account(self, user_id: int):
        with self._lock, closing(self._connect()) as conn:
            conn.execute("DELETE FROM polar_accounts WHERE user_id=?", (user_id,))
            conn.execute("DELETE FROM polar_imports WHERE user_id=?", (user_id,))
            conn.commit()

    def touch_sync(self, user_id: int):
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, closing(self._connect()) as conn:
            conn.execute("UPDATE polar_accounts SET last_sync_at=? WHERE user_id=?", (now, user_id))
            conn.commit()

    def is_imported(self, user_id: int, polar_exercise_id: str) -> bool:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT 1 FROM polar_imports WHERE user_id=? AND polar_exercise_id=?",
                               (user_id, polar_exercise_id)).fetchone()
            return row is not None

    def mark_imported(self, user_id: int, polar_exercise_id: str, video_id: str):
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, closing(self._connect()) as conn:
            conn.execute("INSERT OR IGNORE INTO polar_imports(user_id, polar_exercise_id, video_id, created_at) VALUES(?,?,?,?)",
                         (user_id, polar_exercise_id, video_id, now))
            conn.commit()

    def imported_map(self, user_id: int) -> set[str]:
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT polar_exercise_id FROM polar_imports WHERE user_id=?", (user_id,)).fetchall()
            return {r["polar_exercise_id"] for r in rows}
