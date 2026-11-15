"""SQLite storage layer in WAL mode with versioned schema migrations."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from config.settings import Settings, get_settings

logger = logging.getLogger(__name__)


def utc_now_iso(dt: Optional[datetime] = None) -> str:
    """Return an ISO-8601 UTC timestamp string."""
    target = dt.astimezone(timezone.utc) if dt is not None else datetime.now(timezone.utc)
    return target.isoformat()


def utc_timestamp(dt: Optional[datetime] = None) -> float:
    """Return UTC POSIX epoch seconds."""
    target = dt.astimezone(timezone.utc) if dt is not None else datetime.now(timezone.utc)
    return target.timestamp()


MIGRATIONS: list[tuple[int, str, list[str]]] = [
    (
        1,
        "initial_schema",
        [
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                created_at TEXT NOT NULL,
                created_at_ts REAL NOT NULL,
                track_id INTEGER,
                zone_id TEXT,
                zone_name TEXT,
                person_id INTEGER,
                confidence REAL,
                snapshot_path TEXT,
                message TEXT,
                metadata_json TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS people (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                label TEXT,
                notes TEXT,
                is_trusted INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS face_embeddings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                person_id INTEGER NOT NULL,
                embedding BLOB NOT NULL,
                model_name TEXT NOT NULL,
                quality_score REAL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (person_id) REFERENCES people(id) ON DELETE CASCADE
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER NOT NULL,
                notified INTEGER NOT NULL DEFAULT 0,
                synced INTEGER NOT NULL DEFAULT 0,
                notify_attempts INTEGER NOT NULL DEFAULT 0,
                sync_attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor TEXT NOT NULL,
                action TEXT NOT NULL,
                target_type TEXT,
                target_id TEXT,
                details_json TEXT,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
        ],
    ),
    (
        2,
        "add_event_and_outbox_indexes",
        [
            "CREATE INDEX IF NOT EXISTS idx_events_created_at_ts ON events(created_at_ts)",
            "CREATE INDEX IF NOT EXISTS idx_events_type_track ON events(event_type, track_id)",
            "CREATE INDEX IF NOT EXISTS idx_outbox_pending ON outbox(notified, synced)",
            "CREATE INDEX IF NOT EXISTS idx_face_embeddings_person ON face_embeddings(person_id)",
        ],
    ),
    (
        3,
        "add_person_consent_and_photos",
        [
            "ALTER TABLE people ADD COLUMN consent_date TEXT",
            "ALTER TABLE people ADD COLUMN consent_note TEXT",
            """
            CREATE TABLE IF NOT EXISTS person_photos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                person_id INTEGER NOT NULL,
                file_path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (person_id) REFERENCES people(id) ON DELETE CASCADE
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_person_photos_person ON person_photos(person_id)",
        ],
    ),
    (
        4,
        "add_users_table",
        [
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'owner',
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
        ],
    ),
]


class Database:
    """Thread-safe SQLite wrapper using WAL mode and transactional outbox."""

    def __init__(self, db_path: Optional[Path | str] = None, settings: Optional[Settings] = None) -> None:
        cfg = settings or get_settings()
        self._db_path = Path(db_path) if db_path is not None else cfg.db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

        self._conn = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False,
            timeout=10.0,
        )
        self._conn.row_factory = sqlite3.Row
        self._configure_pragmas()
        self.apply_migrations()

    @property
    def db_path(self) -> Path:
        return self._db_path

    def _configure_pragmas(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA journal_mode = WAL;")
            self._conn.execute("PRAGMA synchronous = NORMAL;")
            self._conn.execute("PRAGMA foreign_keys = ON;")
            self._conn.execute("PRAGMA busy_timeout = 5000;")

    def journal_mode(self) -> str:
        with self._lock:
            row = self._conn.execute("PRAGMA journal_mode;").fetchone()
            return str(row[0]).lower() if row else ""

    def apply_migrations(self) -> int:
        """Apply pending schema migrations and return the resulting schema version."""
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                )
                """
            )
            row = self._conn.execute(
                "SELECT COALESCE(MAX(version), 0) AS current_version FROM schema_migrations"
            ).fetchone()
            current_version = int(row["current_version"]) if row else 0

            for version, name, statements in MIGRATIONS:
                if version <= current_version:
                    continue
                with self._conn:
                    for stmt in statements:
                        self._conn.execute(stmt)
                    self._conn.execute(
                        """
                        INSERT INTO schema_migrations (version, name, applied_at)
                        VALUES (?, ?, ?)
                        """,
                        (version, name, utc_now_iso()),
                    )
                current_version = version
                logger.info("Applied DB migration v%d (%s)", version, name)

            return current_version

    def schema_version(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(version), 0) AS v FROM schema_migrations"
            ).fetchone()
            return int(row["v"]) if row else 0

    def insert_event(
        self,
        *,
        event_type: str,
        track_id: Optional[int] = None,
        zone_id: Optional[str] = None,
        zone_name: Optional[str] = None,
        person_id: Optional[int] = None,
        confidence: Optional[float] = None,
        snapshot_path: Optional[str] = None,
        message: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        created_at: Optional[datetime] = None,
    ) -> tuple[int, int]:
        """Insert an event and its corresponding outbox row atomically."""
        now_dt = created_at.astimezone(timezone.utc) if created_at else datetime.now(timezone.utc)
        created_iso = now_dt.isoformat()
        created_ts = now_dt.timestamp()
        metadata_str = json.dumps(metadata, sort_keys=True) if metadata is not None else None

        with self._lock:
            with self._conn:
                cur = self._conn.execute(
                    """
                    INSERT INTO events (
                        event_type, created_at, created_at_ts, track_id,
                        zone_id, zone_name, person_id, confidence,
                        snapshot_path, message, metadata_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_type,
                        created_iso,
                        created_ts,
                        track_id,
                        zone_id,
                        zone_name,
                        person_id,
                        confidence,
                        snapshot_path,
                        message,
                        metadata_str,
                    ),
                )
                event_id = int(cur.lastrowid)

                outbox_cur = self._conn.execute(
                    """
                    INSERT INTO outbox (
                        event_id, notified, synced, created_at, updated_at
                    )
                    VALUES (?, 0, 0, ?, ?)
                    """,
                    (event_id, created_iso, created_iso),
                )
                outbox_id = int(outbox_cur.lastrowid)

        return event_id, outbox_id

    def get_event(self, event_id: int) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM events WHERE id = ?",
                (event_id,),
            ).fetchone()
            return dict(row) if row else None

    def list_events(
        self,
        *,
        event_type: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self._lock:
            if event_type:
                rows = self._conn.execute(
                    """
                    SELECT * FROM events
                    WHERE event_type = ?
                    ORDER BY created_at_ts DESC, id DESC
                    LIMIT ?
                    """,
                    (event_type, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT * FROM events
                    ORDER BY created_at_ts DESC, id DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]

    def get_pending_outbox(
        self,
        *,
        limit: int = 100,
        pending_notify: Optional[bool] = None,
        pending_sync: Optional[bool] = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        if pending_notify is True:
            clauses.append("o.notified = 0")
        elif pending_notify is False:
            clauses.append("o.notified = 1")
        if pending_sync is True:
            clauses.append("o.synced = 0")
        elif pending_sync is False:
            clauses.append("o.synced = 1")

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"""
            SELECT o.*, e.event_type, e.created_at AS event_created_at,
                   e.track_id, e.zone_id, e.zone_name, e.person_id,
                   e.confidence, e.snapshot_path, e.message, e.metadata_json,
                   p.name AS person_name, p.is_trusted AS person_is_trusted
            FROM outbox o
            JOIN events e ON e.id = o.event_id
            LEFT JOIN people p ON p.id = e.person_id
            {where_sql}
            ORDER BY o.id ASC
            LIMIT ?
        """
        with self._lock:
            rows = self._conn.execute(sql, (limit,)).fetchall()
            return [dict(r) for r in rows]

    def mark_outbox_notified(self, outbox_id: int) -> None:
        now_iso = utc_now_iso()
        with self._lock:
            with self._conn:
                self._conn.execute(
                    """
                    UPDATE outbox
                    SET notified = 1, notify_attempts = notify_attempts + 1,
                        last_error = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (now_iso, outbox_id),
                )

    def record_outbox_notify_error(self, outbox_id: int, error_message: str) -> None:
        now_iso = utc_now_iso()
        with self._lock:
            with self._conn:
                self._conn.execute(
                    """
                    UPDATE outbox
                    SET notify_attempts = notify_attempts + 1,
                        last_error = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (error_message[:500], now_iso, outbox_id),
                )

    def mark_outbox_synced(self, outbox_id: int) -> None:
        now_iso = utc_now_iso()
        with self._lock:
            with self._conn:
                self._conn.execute(
                    """
                    UPDATE outbox
                    SET synced = 1, sync_attempts = sync_attempts + 1, last_error = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (now_iso, outbox_id),
                )

    def record_outbox_sync_error(self, outbox_id: int, error_message: str) -> None:
        now_iso = utc_now_iso()
        with self._lock:
            with self._conn:
                self._conn.execute(
                    """
                    UPDATE outbox
                    SET sync_attempts = sync_attempts + 1,
                        last_error = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (error_message[:500], now_iso, outbox_id),
                )

    def record_audit(
        self,
        *,
        actor: str,
        action: str,
        target_type: Optional[str] = None,
        target_id: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ) -> int:
        details_str = json.dumps(details, sort_keys=True) if details is not None else None
        with self._lock:
            with self._conn:
                cur = self._conn.execute(
                    """
                    INSERT INTO audit_log (
                        actor, action, target_type, target_id, details_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (actor, action, target_type, target_id, details_str, utc_now_iso()),
                )
                return int(cur.lastrowid)

    def set_setting(self, key: str, value: str) -> None:
        now_iso = utc_now_iso()
        with self._lock:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO settings (key, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        updated_at = excluded.updated_at
                    """,
                    (key, value, now_iso),
                )

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM settings WHERE key = ?",
                (key,),
            ).fetchone()
            return str(row["value"]) if row else default

    def delete_events_before(self, cutoff_ts: float) -> tuple[int, int, list[str]]:
        """Delete events older than cutoff_ts; return (deleted_events, deleted_outbox, snapshot_paths)."""
        with self._lock:
            with self._conn:
                rows = self._conn.execute(
                    "SELECT id, snapshot_path FROM events WHERE created_at_ts < ?",
                    (cutoff_ts,),
                ).fetchall()
                if not rows:
                    return 0, 0, []

                event_ids = [int(r["id"]) for r in rows]
                snapshot_paths = [
                    str(r["snapshot_path"])
                    for r in rows
                    if r["snapshot_path"]
                ]

                placeholders = ",".join("?" for _ in event_ids)
                outbox_count_row = self._conn.execute(
                    f"SELECT COUNT(*) AS cnt FROM outbox WHERE event_id IN ({placeholders})",
                    event_ids,
                ).fetchone()
                deleted_outbox = int(outbox_count_row["cnt"]) if outbox_count_row else 0

                self._conn.execute(
                    f"DELETE FROM events WHERE id IN ({placeholders})",
                    event_ids,
                )
                return len(event_ids), deleted_outbox, snapshot_paths

    def table_names(self) -> set[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
            return {str(r["name"]) for r in rows}

    def create_person(
        self,
        *,
        name: str,
        consent_date: str,
        consent_note: str,
        label: Optional[str] = None,
        notes: Optional[str] = None,
        is_trusted: bool = True,
    ) -> int:
        now_iso = utc_now_iso()
        with self._lock:
            with self._conn:
                cur = self._conn.execute(
                    """
                    INSERT INTO people (
                        name, label, notes, is_trusted,
                        consent_date, consent_note, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        name,
                        label,
                        notes,
                        1 if is_trusted else 0,
                        consent_date,
                        consent_note,
                        now_iso,
                        now_iso,
                    ),
                )
                return int(cur.lastrowid)

    def add_face_embedding(
        self,
        *,
        person_id: int,
        embedding_bytes: bytes,
        model_name: str,
        quality_score: Optional[float] = None,
    ) -> int:
        with self._lock:
            with self._conn:
                cur = self._conn.execute(
                    """
                    INSERT INTO face_embeddings (
                        person_id, embedding, model_name, quality_score, created_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (person_id, embedding_bytes, model_name, quality_score, utc_now_iso()),
                )
                return int(cur.lastrowid)

    def add_person_photo(self, *, person_id: int, file_path: str) -> int:
        with self._lock:
            with self._conn:
                cur = self._conn.execute(
                    """
                    INSERT INTO person_photos (person_id, file_path, created_at)
                    VALUES (?, ?, ?)
                    """,
                    (person_id, file_path, utc_now_iso()),
                )
                return int(cur.lastrowid)

    def list_people(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT p.*,
                       (SELECT COUNT(*) FROM face_embeddings fe WHERE fe.person_id = p.id) AS embedding_count,
                       (SELECT COUNT(*) FROM person_photos pp WHERE pp.person_id = p.id) AS photo_count
                FROM people p
                ORDER BY p.id ASC
                """
            ).fetchall()
            return [dict(r) for r in rows]

    def get_person(self, person_id: int) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM people WHERE id = ?",
                (person_id,),
            ).fetchone()
            return dict(row) if row else None

    def find_people_by_name(self, name: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM people WHERE LOWER(name) = LOWER(?) ORDER BY id ASC",
                (name.strip(),),
            ).fetchall()
            return [dict(r) for r in rows]

    def list_face_embeddings(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT fe.id, fe.person_id, p.name, fe.embedding, fe.model_name, fe.quality_score
                FROM face_embeddings fe
                JOIN people p ON p.id = fe.person_id
                ORDER BY fe.person_id ASC, fe.id ASC
                """
            ).fetchall()
            return [dict(r) for r in rows]

    def list_person_photos(self, person_id: int) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT file_path FROM person_photos WHERE person_id = ? ORDER BY id ASC",
                (person_id,),
            ).fetchall()
            return [str(r["file_path"]) for r in rows]

    def delete_person_records(self, person_id: int) -> tuple[bool, list[str]]:
        """Delete a person, their embeddings, and photo rows; return (existed, photo_paths)."""
        with self._lock:
            with self._conn:
                row = self._conn.execute(
                    "SELECT id FROM people WHERE id = ?",
                    (person_id,),
                ).fetchone()
                if not row:
                    return False, []

                photo_rows = self._conn.execute(
                    "SELECT file_path FROM person_photos WHERE person_id = ?",
                    (person_id,),
                ).fetchall()
                photo_paths = [str(r["file_path"]) for r in photo_rows]

                self._conn.execute(
                    "UPDATE events SET person_id = NULL WHERE person_id = ?",
                    (person_id,),
                )
                self._conn.execute(
                    "DELETE FROM people WHERE id = ?",
                    (person_id,),
                )
                return True, photo_paths

    def create_user(
        self,
        *,
        username: str,
        password_hash: str,
        role: str = "owner",
    ) -> int:
        clean_user = username.strip()
        if not clean_user:
            raise ValueError("Username must not be empty.")
        now_iso = utc_now_iso()
        with self._lock:
            with self._conn:
                cur = self._conn.execute(
                    """
                    INSERT INTO users (username, password_hash, role, is_active, created_at, updated_at)
                    VALUES (?, ?, ?, 1, ?, ?)
                    """,
                    (clean_user, password_hash, role, now_iso, now_iso),
                )
                return int(cur.lastrowid)

    def update_user_password(
        self,
        *,
        username: str,
        password_hash: str,
    ) -> bool:
        clean_user = username.strip()
        if not clean_user:
            raise ValueError("Username must not be empty.")
        now_iso = utc_now_iso()
        with self._lock:
            with self._conn:
                cur = self._conn.execute(
                    "UPDATE users SET password_hash = ?, updated_at = ? WHERE LOWER(username) = LOWER(?)",
                    (password_hash, now_iso, clean_user),
                )
                return cur.rowcount > 0

    def get_user_by_username(self, username: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE LOWER(username) = LOWER(?)",
                (username.strip(),),
            ).fetchone()
            return dict(row) if row else None

    def get_user_by_id(self, user_id: int) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
            return dict(row) if row else None

    def count_users(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS cnt FROM users").fetchone()
            return int(row["cnt"]) if row else 0

    def query_events(
        self,
        *,
        start_ts: Optional[float] = None,
        end_ts: Optional[float] = None,
        event_type: Optional[str] = None,
        person_id: Optional[int] = None,
        person_name: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []

        if start_ts is not None:
            clauses.append("e.created_at_ts >= ?")
            params.append(start_ts)
        if end_ts is not None:
            clauses.append("e.created_at_ts <= ?")
            params.append(end_ts)
        if event_type:
            clauses.append("e.event_type = ?")
            params.append(event_type.strip())
        if person_id is not None:
            clauses.append("e.person_id = ?")
            params.append(person_id)
        if person_name:
            clauses.append("LOWER(COALESCE(p.name, '')) = LOWER(?)")
            params.append(person_name.strip())

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"""
            SELECT e.*, p.name AS person_name
            FROM events e
            LEFT JOIN people p ON p.id = e.person_id
            {where_sql}
            ORDER BY e.created_at_ts DESC, e.id DESC
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def get_latest_event_id(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COALESCE(MAX(id), 0) AS max_id FROM events").fetchone()
            return int(row["max_id"]) if row else 0

    def get_events_since(self, last_id: int, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT e.*, p.name AS person_name
                FROM events e
                LEFT JOIN people p ON p.id = e.person_id
                WHERE e.id > ?
                ORDER BY e.id ASC
                LIMIT ?
                """,
                (last_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_latest_camera_status(self) -> dict[str, Any]:
        with self._lock:
            cam_row = self._conn.execute(
                """
                SELECT event_type, created_at, message, metadata_json
                FROM events
                WHERE event_type IN ('camera_online', 'camera_offline')
                ORDER BY created_at_ts DESC, id DESC
                LIMIT 1
                """
            ).fetchone()
            last_row = self._conn.execute(
                "SELECT created_at, event_type FROM events ORDER BY created_at_ts DESC, id DESC LIMIT 1"
            ).fetchone()
            total_row = self._conn.execute("SELECT COUNT(*) AS cnt FROM events").fetchone()

            if cam_row:
                status = "online" if cam_row["event_type"] == "camera_online" else "offline"
                last_change = cam_row["created_at"]
            else:
                status = "unknown"
                last_change = None

            return {
                "status": status,
                "last_status_change": last_change,
                "last_event_at": last_row["created_at"] if last_row else None,
                "last_event_type": last_row["event_type"] if last_row else None,
                "total_events": int(total_row["cnt"]) if total_row else 0,
            }

    def list_settings(self) -> dict[str, str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT key, value FROM settings ORDER BY key ASC"
            ).fetchall()
            return {str(r["key"]): str(r["value"]) for r in rows}

    def list_outbox_notifications(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT o.*, e.event_type, e.created_at AS event_created_at,
                       e.zone_name, e.message, e.snapshot_path
                FROM outbox o
                JOIN events e ON e.id = o.event_id
                ORDER BY o.id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def list_audit_logs(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()
