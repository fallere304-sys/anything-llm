"""SQLite による会話ストア(標準ライブラリのみ)。"""
from __future__ import annotations

import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    project_id  TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id  TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role             TEXT NOT NULL CHECK (role IN ('user','assistant')),
    content          TEXT NOT NULL,
    provider         TEXT,
    model            TEXT,
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, id);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class NotFound(Exception):
    pass


class Database:
    def __init__(self, path: Path | str) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        self._conn.close()

    # --- 他ストア(記憶など)が同じ接続を共有するための汎用口 ---
    @contextmanager
    def tx(self):
        """ロック + トランザクション。例外時はロールバック。"""
        with self._lock, self._conn:
            yield self._conn

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def set_conversation_project(self, cid: str, project_id: Optional[str]) -> None:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE conversations SET project_id = ?, updated_at = ? WHERE id = ?",
                (project_id, now_iso(), cid),
            )
        if cur.rowcount == 0:
            raise NotFound(cid)

    def get_message(self, mid: int) -> dict:
        rows = self.query("SELECT * FROM messages WHERE id = ?", (mid,))
        if not rows:
            raise NotFound(str(mid))
        return rows[0]

    # --- conversations ---
    def create_conversation(self, title: str = "新しい会話", project_id: Optional[str] = None) -> dict:
        cid, ts = uuid.uuid4().hex, now_iso()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO conversations (id, title, project_id, created_at, updated_at) VALUES (?,?,?,?,?)",
                (cid, title, project_id, ts, ts),
            )
        return self.get_conversation(cid)

    def get_conversation(self, cid: str) -> dict:
        with self._lock:
            row = self._conn.execute("SELECT * FROM conversations WHERE id = ?", (cid,)).fetchone()
        if row is None:
            raise NotFound(cid)
        return dict(row)

    def list_conversations(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM conversations ORDER BY updated_at DESC, created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def rename_conversation(self, cid: str, title: str) -> None:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?", (title, now_iso(), cid)
            )
        if cur.rowcount == 0:
            raise NotFound(cid)

    def delete_conversation(self, cid: str) -> None:
        with self._lock, self._conn:
            cur = self._conn.execute("DELETE FROM conversations WHERE id = ?", (cid,))
        if cur.rowcount == 0:
            raise NotFound(cid)

    # --- messages ---
    def add_message(
        self, cid: str, role: str, content: str, provider: Optional[str] = None, model: Optional[str] = None
    ) -> dict:
        ts = now_iso()
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO messages (conversation_id, role, content, provider, model, created_at) VALUES (?,?,?,?,?,?)",
                (cid, role, content, provider, model, ts),
            )
            self._conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (ts, cid))
            row = self._conn.execute("SELECT * FROM messages WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)

    def list_messages(self, cid: str) -> list[dict]:
        self.get_conversation(cid)
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM messages WHERE conversation_id = ? ORDER BY id", (cid,)
            ).fetchall()
        return [dict(r) for r in rows]
