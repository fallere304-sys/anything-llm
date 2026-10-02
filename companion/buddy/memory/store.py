"""記憶・プロジェクト・作業履歴の永続化(SQLite)。ルールは service.py 側で強制する。"""
from __future__ import annotations

import uuid
from typing import Optional

from ..storage.db import Database, NotFound, now_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL UNIQUE,
    description  TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memories (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL CHECK (kind IN ('long_term', 'project')),
    content     TEXT NOT NULL,
    project_id  TEXT REFERENCES projects(id),
    important   INTEGER NOT NULL DEFAULT 0,
    source      TEXT NOT NULL CHECK (source IN ('user', 'conversation', 'ai_proposal')),
    source_ref  TEXT,
    confidence  REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    status      TEXT NOT NULL CHECK (status IN ('active', 'pending')),
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    CHECK ((kind = 'project') = (project_id IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(status, kind);
CREATE TABLE IF NOT EXISTS work_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    ts               TEXT NOT NULL,
    actor            TEXT NOT NULL,
    action           TEXT NOT NULL,
    target           TEXT NOT NULL,
    summary          TEXT NOT NULL,
    ok               INTEGER NOT NULL DEFAULT 1,
    conversation_id  TEXT
);
"""

MEMORY_FIELDS = ("content", "important", "confidence", "status")


def _memory(row: dict) -> dict:
    row["important"] = bool(row["important"])
    return row


class ProjectInUse(Exception):
    """記憶が残っているプロジェクトは削除できない。"""


class MemoryStore:
    def __init__(self, db: Database) -> None:
        self.db = db
        with db.tx() as conn:
            conn.executescript(SCHEMA)

    # --- projects ---
    def create_project(self, name: str, description: str = "") -> dict:
        pid, ts = uuid.uuid4().hex, now_iso()
        with self.db.tx() as conn:
            conn.execute(
                "INSERT INTO projects (id, name, description, created_at, updated_at) VALUES (?,?,?,?,?)",
                (pid, name, description, ts, ts),
            )
        return self.get_project(pid)

    def get_project(self, pid: str) -> dict:
        rows = self.db.query("SELECT * FROM projects WHERE id = ?", (pid,))
        if not rows:
            raise NotFound(pid)
        return rows[0]

    def find_project_by_name(self, name: str) -> Optional[dict]:
        rows = self.db.query("SELECT * FROM projects WHERE name = ?", (name,))
        return rows[0] if rows else None

    def list_projects(self) -> list[dict]:
        return self.db.query(
            "SELECT p.*, (SELECT COUNT(*) FROM memories m WHERE m.project_id = p.id) AS memory_count "
            "FROM projects p ORDER BY p.name"
        )

    def update_project(self, pid: str, name: Optional[str], description: Optional[str]) -> dict:
        cur = self.get_project(pid)
        with self.db.tx() as conn:
            conn.execute(
                "UPDATE projects SET name = ?, description = ?, updated_at = ? WHERE id = ?",
                (name if name is not None else cur["name"],
                 description if description is not None else cur["description"], now_iso(), pid),
            )
        return self.get_project(pid)

    def delete_project(self, pid: str) -> None:
        self.get_project(pid)
        with self.db.tx() as conn:
            n = conn.execute("SELECT COUNT(*) FROM memories WHERE project_id = ?", (pid,)).fetchone()[0]
            if n:
                raise ProjectInUse(n)
            conn.execute("UPDATE conversations SET project_id = NULL WHERE project_id = ?", (pid,))
            conn.execute("DELETE FROM projects WHERE id = ?", (pid,))

    # --- memories ---
    def create_memory(
        self, *, kind: str, content: str, project_id: Optional[str], important: bool,
        source: str, source_ref: Optional[str], confidence: float, status: str,
    ) -> dict:
        mid, ts = uuid.uuid4().hex, now_iso()
        with self.db.tx() as conn:
            conn.execute(
                "INSERT INTO memories (id, kind, content, project_id, important, source, source_ref,"
                " confidence, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (mid, kind, content, project_id, int(important), source, source_ref,
                 confidence, status, ts, ts),
            )
        return self.get_memory(mid)

    def get_memory(self, mid: str) -> dict:
        rows = self.db.query("SELECT * FROM memories WHERE id = ?", (mid,))
        if not rows:
            raise NotFound(mid)
        return _memory(rows[0])

    def list_memories(
        self, *, status: Optional[str] = None, kind: Optional[str] = None,
        project_id: Optional[str] = None, important: Optional[bool] = None,
    ) -> list[dict]:
        where, params = [], []
        for col, val in (("status", status), ("kind", kind), ("project_id", project_id)):
            if val is not None:
                where.append(f"{col} = ?"); params.append(val)
        if important is not None:
            where.append("important = ?"); params.append(int(important))
        sql = "SELECT * FROM memories" + (" WHERE " + " AND ".join(where) if where else "")
        return [_memory(r) for r in self.db.query(sql + " ORDER BY updated_at DESC, id", tuple(params))]

    def update_memory(self, mid: str, **fields) -> dict:
        self.get_memory(mid)
        sets = {k: v for k, v in fields.items() if k in MEMORY_FIELDS and v is not None}
        if "important" in sets:
            sets["important"] = int(sets["important"])
        if sets:
            cols = ", ".join(f"{k} = ?" for k in sets)
            with self.db.tx() as conn:
                conn.execute(f"UPDATE memories SET {cols}, updated_at = ? WHERE id = ?",
                             (*sets.values(), now_iso(), mid))
        return self.get_memory(mid)

    def delete_memory(self, mid: str) -> None:
        with self.db.tx() as conn:
            cur = conn.execute("DELETE FROM memories WHERE id = ?", (mid,))
        if cur.rowcount == 0:
            raise NotFound(mid)

    # --- work log(追記専用) ---
    def add_log(self, actor: str, action: str, target: str, summary: str,
                ok: bool = True, conversation_id: Optional[str] = None) -> None:
        with self.db.tx() as conn:
            conn.execute(
                "INSERT INTO work_log (ts, actor, action, target, summary, ok, conversation_id)"
                " VALUES (?,?,?,?,?,?,?)",
                (now_iso(), actor, action, target, summary, int(ok), conversation_id),
            )

    def list_log(self, limit: int = 50) -> list[dict]:
        rows = self.db.query("SELECT * FROM work_log ORDER BY id DESC LIMIT ?", (limit,))
        for r in rows:
            r["ok"] = bool(r["ok"])
        return rows
