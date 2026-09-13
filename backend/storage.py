"""Transactional local persistence for conversations, memories, and agent runs."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Iterable


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


class Storage:
    def __init__(self, database_path: str, history_json: str | None = None, memory_json: str | None = None):
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()
        self._migrate_json(history_json, memory_json)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None, factory=ClosingConnection)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    workspace TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK(role IN ('user','assistant','system','tool')),
                    content TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    thinking_seconds REAL
                );
                CREATE INDEX IF NOT EXISTS messages_session_idx ON messages(session_id, id);
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    workspace TEXT NOT NULL DEFAULT '',
                    content TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    workspace TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    provider TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    error TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS run_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    UNIQUE(run_id, sequence)
                );
                """
            )

    @staticmethod
    def _read_json(path: str | None) -> Any:
        if not path or not Path(path).exists():
            return None
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _migrate_json(self, history_json: str | None, memory_json: str | None) -> None:
        with self._lock, self._connect() as db:
            migrated = db.execute("SELECT value FROM metadata WHERE key='json_migration_v1'").fetchone()
            if migrated:
                return
            history = self._read_json(history_json) or []
            if isinstance(history, dict):
                history = list(history.values())
            memories = self._read_json(memory_json) or []
            now = int(time.time())
            db.execute("BEGIN IMMEDIATE")
            try:
                for session in history if isinstance(history, list) else []:
                    if not isinstance(session, dict) or not session.get("id"):
                        continue
                    sid = str(session["id"])
                    created = int(session.get("created_at") or now)
                    updated = int(session.get("updated_at") or created)
                    db.execute("INSERT OR IGNORE INTO sessions(id,title,workspace,created_at,updated_at) VALUES(?,?,?,?,?)", (sid, str(session.get("title") or "Conversation"), str(session.get("workspace") or ""), created, updated))
                    exists = db.execute("SELECT 1 FROM messages WHERE session_id=? LIMIT 1", (sid,)).fetchone()
                    if exists:
                        continue
                    for message in session.get("messages", []):
                        if not isinstance(message, dict) or message.get("role") not in {"user", "assistant", "system", "tool"}:
                            continue
                        db.execute("INSERT INTO messages(session_id,role,content,created_at,thinking_seconds) VALUES(?,?,?,?,?)", (sid, message["role"], str(message.get("content") or ""), int(message.get("timestamp") or updated), message.get("thinking_seconds")))
                for memory in memories if isinstance(memories, list) else []:
                    if not isinstance(memory, dict) or not memory.get("id"):
                        continue
                    db.execute("INSERT OR IGNORE INTO memories(id,workspace,content,created_at) VALUES(?,?,?,?)", (str(memory["id"]), str(memory.get("workspace") or ""), str(memory.get("content") or ""), int(memory.get("created_at") or now)))
                db.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('json_migration_v1',?)", (str(now),))
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise

    def ensure_session(self, session_id: str, content: str = "", workspace: str = "") -> None:
        now = int(time.time())
        title = " ".join(content.strip().split())[:60] or "New Chat"
        with self._lock, self._connect() as db:
            db.execute("INSERT OR IGNORE INTO sessions(id,title,workspace,created_at,updated_at) VALUES(?,?,?,?,?)", (session_id, title, workspace, now, now))

    def append_message(self, session_id: str, role: str, content: str, workspace: str = "", thinking_seconds: float | None = None) -> dict:
        if role not in {"user", "assistant", "system", "tool"}:
            raise ValueError(f"Unsupported message role: {role}")
        self.ensure_session(session_id, content if role == "user" else "", workspace)
        now = int(time.time())
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute("SELECT title,workspace FROM sessions WHERE id=?", (session_id,)).fetchone()
                updates: list[str] = ["updated_at=?"]
                values: list[Any] = [now]
                if workspace and not row["workspace"]:
                    updates.append("workspace=?")
                    values.append(workspace)
                if role == "user" and row["title"] in {"New Chat", "New Session", "Conversation"}:
                    updates.append("title=?")
                    values.append(" ".join(content.strip().split())[:60] or "New Chat")
                values.append(session_id)
                db.execute(f"UPDATE sessions SET {','.join(updates)} WHERE id=?", values)
                db.execute("INSERT INTO messages(session_id,role,content,created_at,thinking_seconds) VALUES(?,?,?,?,?)", (session_id, role, content, now, thinking_seconds))
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise
        return self.get_session(session_id)

    def get_session(self, session_id: str) -> dict:
        with self._connect() as db:
            session = db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            if not session:
                return {"id": session_id, "title": "New Chat", "workspace": "", "messages": []}
            messages = [dict(row) for row in db.execute("SELECT role,content,created_at AS timestamp,thinking_seconds FROM messages WHERE session_id=? ORDER BY id", (session_id,))]
            return {**dict(session), "messages": messages}

    def list_sessions(self) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("SELECT s.*, COUNT(m.id) AS message_count FROM sessions s LEFT JOIN messages m ON m.session_id=s.id GROUP BY s.id ORDER BY s.updated_at DESC").fetchall()
            return [dict(row) for row in rows]

    def delete_session(self, session_id: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM sessions WHERE id=?", (session_id,))

    def rename_session(self, session_id: str, title: str) -> bool:
        clean = " ".join(title.strip().split())[:120]
        if not clean:
            raise ValueError("Session title cannot be empty")
        with self._lock, self._connect() as db:
            result = db.execute("UPDATE sessions SET title=?, updated_at=? WHERE id=?", (clean, int(time.time()), session_id))
            return result.rowcount > 0

    def list_memories(self, workspace: str = "") -> list[dict]:
        with self._connect() as db:
            if workspace:
                rows = db.execute("SELECT id,content,workspace,created_at FROM memories WHERE workspace IN ('',?) ORDER BY created_at", (workspace,)).fetchall()
            else:
                rows = db.execute("SELECT id,content,workspace,created_at FROM memories ORDER BY created_at").fetchall()
            return [dict(row) for row in rows]

    def add_memory(self, memory_id: str, content: str, workspace: str = "") -> dict:
        now = int(time.time())
        with self._lock, self._connect() as db:
            db.execute("INSERT INTO memories(id,workspace,content,created_at) VALUES(?,?,?,?)", (memory_id, workspace, content, now))
        return {"id": memory_id, "content": content, "workspace": workspace, "created_at": now}

    def delete_memory(self, memory_id: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM memories WHERE id=?", (memory_id,))

    def create_run(self, run_id: str, session_id: str, workspace: str, provider: str, model: str) -> None:
        self.ensure_session(session_id, workspace=workspace)
        now = int(time.time())
        with self._lock, self._connect() as db:
            db.execute("INSERT INTO runs(id,session_id,workspace,status,provider,model,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", (run_id, session_id, workspace, "preparing", provider, model, now, now))

    def set_run_status(self, run_id: str, status: str, error: str | None = None) -> None:
        with self._lock, self._connect() as db:
            if error is None:
                db.execute("UPDATE runs SET status=?, updated_at=? WHERE id=?", (status, int(time.time()), run_id))
            else:
                db.execute("UPDATE runs SET status=?, error=?, updated_at=? WHERE id=?", (status, error, int(time.time()), run_id))

    def append_run_event(self, run_id: str, sequence: int, event_type: str, payload: dict) -> None:
        with self._lock, self._connect() as db:
            db.execute("INSERT INTO run_events(run_id,sequence,event_type,payload,created_at) VALUES(?,?,?,?,?)", (run_id, sequence, event_type, json.dumps(payload, ensure_ascii=False), int(time.time())))

    def latest_run_checkpoint(self, session_id: str, exclude_run_id: str = "", incomplete_only: bool = True) -> dict | None:
        """Return the newest durable checkpoint from an earlier run."""
        with self._connect() as db:
            status_filter = "AND status<>'completed'" if incomplete_only else ""
            run = db.execute(
                f"SELECT id,status,provider,model,error FROM runs WHERE session_id=? AND id<>? {status_filter} ORDER BY created_at DESC,id DESC LIMIT 1",
                (session_id, exclude_run_id),
            ).fetchone()
            if not run:
                return None
            event = db.execute(
                "SELECT payload FROM run_events WHERE run_id=? AND event_type='run_checkpoint' ORDER BY sequence DESC LIMIT 1",
                (run["id"],),
            ).fetchone()
            if not event:
                return None
            try:
                checkpoint = json.loads(event["payload"])
            except (TypeError, ValueError):
                return None
            return {**checkpoint, **dict(run)}
