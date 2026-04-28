from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
from typing import Any

from waypoint.schemas import EventRecord, SessionRecord, SessionStatus, SessionTransport


class Storage:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.database_path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                backend TEXT NOT NULL,
                source TEXT NOT NULL,
                transport TEXT NOT NULL DEFAULT 'tmux',
                title TEXT NOT NULL,
                cwd TEXT NOT NULL,
                repo_name TEXT,
                branch TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_event_at TEXT NOT NULL,
                tmux_session TEXT,
                tmux_window TEXT,
                tmux_pane TEXT,
                thread_id TEXT,
                raw_log_path TEXT NOT NULL,
                structured_log_path TEXT NOT NULL,
                pid INTEGER
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                ts TEXT NOT NULL,
                kind TEXT NOT NULL,
                text TEXT NOT NULL,
                metadata TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );
            """
        )
        self._ensure_column("sessions", "transport", "TEXT NOT NULL DEFAULT 'tmux'")
        self._ensure_column("sessions", "thread_id", "TEXT")
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def create_session(self, session: SessionRecord) -> SessionRecord:
        self.connection.execute(
            """
            INSERT INTO sessions (
                id, backend, source, transport, title, cwd, repo_name, branch, status,
                created_at, updated_at, last_event_at, tmux_session, tmux_window,
                tmux_pane, thread_id, raw_log_path, structured_log_path, pid
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session.id,
                session.backend,
                session.source,
                session.transport,
                session.title,
                session.cwd,
                session.repo_name,
                session.branch,
                session.status,
                session.created_at.isoformat(),
                session.updated_at.isoformat(),
                session.last_event_at.isoformat(),
                session.tmux_session,
                session.tmux_window,
                session.tmux_pane,
                session.thread_id,
                session.raw_log_path,
                session.structured_log_path,
                session.pid,
            ),
        )
        self.connection.commit()
        return session

    def list_sessions(self) -> list[SessionRecord]:
        rows = self.connection.execute(
            "SELECT * FROM sessions ORDER BY last_event_at DESC, created_at DESC"
        ).fetchall()
        return [self._session_from_row(row) for row in rows]

    def get_session(self, session_id: str) -> SessionRecord | None:
        row = self.connection.execute(
            "SELECT * FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        return self._session_from_row(row)

    def update_session(self, session_id: str, **fields: Any) -> SessionRecord:
        current = self.get_session(session_id)
        if current is None:
            raise KeyError(session_id)
        fields.setdefault("updated_at", datetime.now(UTC))
        assignments = ", ".join(f"{name} = ?" for name in fields)
        values = [self._serialize_field(value) for value in fields.values()]
        values.append(session_id)
        self.connection.execute(
            f"UPDATE sessions SET {assignments} WHERE id = ?",
            values,
        )
        self.connection.commit()
        updated = self.get_session(session_id)
        assert updated is not None
        return updated

    def append_event(self, event: EventRecord) -> EventRecord:
        cursor = self.connection.execute(
            """
            INSERT INTO events (session_id, ts, kind, text, metadata, sequence)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event.session_id,
                event.ts.isoformat(),
                event.kind,
                event.text,
                json.dumps(event.metadata),
                event.sequence,
            ),
        )
        self.connection.execute(
            """
            UPDATE sessions
            SET last_event_at = ?, updated_at = ?, status = COALESCE(?, status)
            WHERE id = ?
            """,
            (
                event.ts.isoformat(),
                event.ts.isoformat(),
                event.metadata.get("status"),
                event.session_id,
            ),
        )
        self.connection.commit()
        return event.model_copy(update={"id": int(cursor.lastrowid)})

    def list_events(self, session_id: str, cursor: int | None = None) -> list[EventRecord]:
        query = "SELECT * FROM events WHERE session_id = ?"
        params: list[Any] = [session_id]
        if cursor is not None:
            query += " AND id > ?"
            params.append(cursor)
        query += " ORDER BY sequence ASC, id ASC"
        rows = self.connection.execute(query, params).fetchall()
        return [self._event_from_row(row) for row in rows]

    def next_sequence(self, session_id: str) -> int:
        row = self.connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) AS max_sequence FROM events WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return int(row["max_sequence"]) + 1

    def _session_from_row(self, row: sqlite3.Row) -> SessionRecord:
        payload = dict(row)
        for field_name in ("created_at", "updated_at", "last_event_at"):
            payload[field_name] = datetime.fromisoformat(payload[field_name])
        payload["status"] = SessionStatus(payload["status"])
        payload["transport"] = SessionTransport(payload.get("transport", SessionTransport.TMUX))
        return SessionRecord.model_validate(payload)

    def _event_from_row(self, row: sqlite3.Row) -> EventRecord:
        payload = dict(row)
        payload["ts"] = datetime.fromisoformat(payload["ts"])
        payload["metadata"] = json.loads(payload["metadata"])
        return EventRecord.model_validate(payload)

    def _serialize_field(self, value: Any) -> Any:
        if isinstance(value, datetime):
            return value.isoformat()
        return value

    def _ensure_column(self, table: str, column: str, ddl: str) -> None:
        rows = self.connection.execute(f"PRAGMA table_info({table})").fetchall()
        if any(row["name"] == column for row in rows):
            return
        self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
