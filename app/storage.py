from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

DB_PATH: Path | None = None

UTC = timezone.utc

STATUS_FRESH = "FRESH"
STATUS_IN_PROGRESS = "IN_PROGRESS"
STATUS_SUCCESS = "SUCCESS"
STATUS_FAIL_RETRYABLE = "FAIL_RETRYABLE"
STATUS_FAIL_PERM = "FAIL_PERM"

TERMINAL_STATUSES = {STATUS_SUCCESS, STATUS_FAIL_PERM}


@dataclass
class ImportReport:
    added: int
    duplicates: int
    invalid: List[Tuple[str, str]]


def configure(db_path: str) -> None:
    """Configure the storage backend with a database path."""
    global DB_PATH
    DB_PATH = Path(db_path)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def _get_connection() -> sqlite3.Connection:
    if DB_PATH is None:
        raise RuntimeError("Storage is not configured. Call configure() first.")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """Initialise the database schema if it does not exist."""
    conn = _get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS profiles (
            id INTEGER PRIMARY KEY,
            nickname TEXT NOT NULL,
            daily_limit INTEGER NOT NULL,
            tz TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS uids (
            id INTEGER PRIMARY KEY,
            raw_input TEXT NOT NULL,
            normalized_uid TEXT NOT NULL,
            profile_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error_code TEXT,
            last_error_msg TEXT,
            last_evidence_path TEXT,
            first_seen_at TEXT NOT NULL,
            last_updated_at TEXT NOT NULL,
            last_started_at TEXT,
            next_attempt_after TEXT,
            heartbeat_at TEXT,
            UNIQUE(profile_id, normalized_uid),
            FOREIGN KEY(profile_id) REFERENCES profiles(id) ON DELETE CASCADE
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS uid_events (
            id INTEGER PRIMARY KEY,
            uid_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            event_data TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(uid_id) REFERENCES uids(id) ON DELETE CASCADE
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_counters (
            id INTEGER PRIMARY KEY,
            profile_id INTEGER NOT NULL,
            date_ymd TEXT NOT NULL,
            sent_success INTEGER NOT NULL DEFAULT 0,
            sent_fail INTEGER NOT NULL DEFAULT 0,
            UNIQUE(profile_id, date_ymd),
            FOREIGN KEY(profile_id) REFERENCES profiles(id) ON DELETE CASCADE
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )

    conn.commit()
    conn.close()


def ensure_profile(nickname: str, daily_limit: int, tz: str) -> sqlite3.Row:
    """Ensure the default profile exists."""
    conn = _get_connection()
    cur = conn.cursor()
    now = datetime.now(UTC).isoformat()

    cur.execute("SELECT * FROM profiles ORDER BY id LIMIT 1")
    row = cur.fetchone()
    if row is None:
        cur.execute(
            "INSERT INTO profiles (nickname, daily_limit, tz, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (nickname, daily_limit, tz, now, now),
        )
        conn.commit()
        cur.execute("SELECT * FROM profiles ORDER BY id LIMIT 1")
        row = cur.fetchone()

    conn.close()
    return row


def update_profile(profile_id: int, *, nickname: Optional[str] = None, daily_limit: Optional[int] = None) -> None:
    conn = _get_connection()
    cur = conn.cursor()
    parts: List[str] = []
    values: List[object] = []
    if nickname is not None:
        parts.append("nickname = ?")
        values.append(nickname)
    if daily_limit is not None:
        parts.append("daily_limit = ?")
        values.append(daily_limit)
    if not parts:
        conn.close()
        return
    values.extend([datetime.now(UTC).isoformat(), profile_id])
    cur.execute(
        f"UPDATE profiles SET {', '.join(parts)}, updated_at = ? WHERE id = ?",
        values,
    )
    conn.commit()
    conn.close()


def get_profile(profile_id: int) -> Optional[sqlite3.Row]:
    conn = _get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,))
    row = cur.fetchone()
    conn.close()
    return row


def _record_event(conn: sqlite3.Connection, uid_id: int, event_type: str, event_data: Optional[str]) -> None:
    conn.execute(
        "INSERT INTO uid_events (uid_id, event_type, event_data, created_at) VALUES (?, ?, ?, ?)",
        (uid_id, event_type, event_data, datetime.now(UTC).isoformat()),
    )


def normalize_uid(raw: str) -> Tuple[Optional[str], Optional[str]]:
    """Normalise a UID string. Returns (normalized, reason)."""
    if not raw:
        return None, "Empty line"

    value = raw.strip()
    if not value:
        return None, "Empty line"

    lower = value.lower()
    if "facebook.com" in lower:
        # Remove URL parts
        for prefix in ("https://", "http://"):
            if value.startswith(prefix):
                value = value[len(prefix) :]
                break
        value = value.rstrip("/")
        if "?" in value:
            path, query = value.split("?", 1)
        else:
            path, query = value, ""
        if "profile.php" in path and "id=" in query:
            for part in query.split("&"):
                if part.startswith("id="):
                    candidate = part.split("=", 1)[1]
                    if candidate.isdigit():
                        return candidate, None
            return None, "Could not parse profile id"
        else:
            path_parts = [p for p in path.split("/") if p and p != "www.facebook.com"]
            if not path_parts:
                return None, "Invalid Facebook URL"
            candidate = path_parts[-1]
            return candidate, None

    if value.isdigit():
        return value, None

    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789._")
    cleaned = value.strip()
    if set(cleaned.lower()) <= allowed:
        return cleaned, None

    return None, "Unsupported UID format"


def add_uids(profile_id: int, lines: Iterable[str]) -> ImportReport:
    conn = _get_connection()
    cur = conn.cursor()
    now = datetime.now(UTC).isoformat()

    added = 0
    duplicates = 0
    invalid: List[Tuple[str, str]] = []

    for raw in lines:
        normalized, reason = normalize_uid(raw)
        if not normalized:
            invalid.append((raw.strip(), reason or "Invalid"))
            continue

        try:
            cur.execute(
                "INSERT INTO uids (raw_input, normalized_uid, profile_id, status, attempts, first_seen_at, last_updated_at)"
                " VALUES (?, ?, ?, ?, 0, ?, ?)",
                (raw.strip(), normalized, profile_id, STATUS_FRESH, now, now),
            )
            added += 1
            _record_event(conn, cur.lastrowid, "QUEUE", None)
        except sqlite3.IntegrityError:
            duplicates += 1

    conn.commit()
    conn.close()
    return ImportReport(added=added, duplicates=duplicates, invalid=invalid)


def list_uids(profile_id: int, status_filter: Optional[List[str]] = None) -> List[Dict[str, object]]:
    conn = _get_connection()
    cur = conn.cursor()
    query = "SELECT * FROM uids WHERE profile_id = ?"
    params: List[object] = [profile_id]
    if status_filter:
        placeholders = ",".join(["?"] * len(status_filter))
        query += f" AND status IN ({placeholders})"
        params.extend(status_filter)
    query += " ORDER BY first_seen_at"
    cur.execute(query, params)
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return rows


def get_uid_counts(profile_id: int) -> Dict[str, int]:
    conn = _get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT status, COUNT(*) as cnt FROM uids WHERE profile_id = ? GROUP BY status",
        (profile_id,),
    )
    counts = {row["status"]: row["cnt"] for row in cur.fetchall()}
    conn.close()
    # Ensure all statuses present
    for status in [STATUS_FRESH, STATUS_IN_PROGRESS, STATUS_SUCCESS, STATUS_FAIL_RETRYABLE, STATUS_FAIL_PERM]:
        counts.setdefault(status, 0)
    return counts


def ensure_daily_counter(profile_id: int, date_ymd: str) -> None:
    conn = _get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO daily_counters (profile_id, date_ymd, sent_success, sent_fail) VALUES (?, ?, 0, 0)",
        (profile_id, date_ymd),
    )
    conn.commit()
    conn.close()


def get_daily_counter(profile_id: int, date_ymd: str) -> Dict[str, int]:
    conn = _get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT sent_success, sent_fail FROM daily_counters WHERE profile_id = ? AND date_ymd = ?",
        (profile_id, date_ymd),
    )
    row = cur.fetchone()
    conn.close()
    if row is None:
        return {"sent_success": 0, "sent_fail": 0}
    return dict(row)


def increment_daily(profile_id: int, date_ymd: str, success: bool) -> None:
    conn = _get_connection()
    cur = conn.cursor()
    column = "sent_success" if success else "sent_fail"
    cur.execute(
        f"UPDATE daily_counters SET {column} = {column} + 1 WHERE profile_id = ? AND date_ymd = ?",
        (profile_id, date_ymd),
    )
    conn.commit()
    conn.close()


def get_settings() -> Dict[str, str]:
    conn = _get_connection()
    cur = conn.cursor()
    cur.execute("SELECT key, value FROM app_settings")
    rows = cur.fetchall()
    conn.close()
    return {row["key"]: row["value"] for row in rows}


def set_setting(key: str, value: str) -> None:
    conn = _get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


def reset_stale_in_progress(profile_id: int, stale_after_seconds: int = 300) -> None:
    conn = _get_connection()
    cur = conn.cursor()
    cutoff = datetime.now(UTC) - timedelta(seconds=stale_after_seconds)
    cutoff_iso = cutoff.isoformat()
    cur.execute(
        """
        SELECT id FROM uids
        WHERE profile_id = ? AND status = ? AND (heartbeat_at IS NULL OR heartbeat_at < ?)
        """,
        (profile_id, STATUS_IN_PROGRESS, cutoff_iso),
    )
    rows = cur.fetchall()
    if not rows:
        conn.close()
        return

    ids = [row["id"] for row in rows]
    now_iso = datetime.now(UTC).isoformat()
    cur.executemany(
        """
        UPDATE uids
        SET status = ?, last_error_code = 'ENGINE_CRASH', last_error_msg = 'Recovered from stale in-progress state',
            last_updated_at = ?, next_attempt_after = NULL, heartbeat_at = NULL
        WHERE id = ?
        """,
        [(STATUS_FAIL_RETRYABLE, now_iso, uid_id) for uid_id in ids],
    )
    for uid_id in ids:
        _record_event(conn, uid_id, "RECOVERED", '{"reason": "stale in-progress reset"}')
    conn.commit()
    conn.close()


def lease_next_uid(profile_id: int, now: Optional[datetime] = None) -> Optional[Dict[str, object]]:
    now = now or datetime.now(UTC)
    conn = _get_connection()
    conn.isolation_level = None  # autocommit off for manual transaction
    cur = conn.cursor()
    cur.execute("BEGIN IMMEDIATE")

    cur.execute(
        """
        SELECT * FROM uids
        WHERE profile_id = ?
          AND status IN (?, ?)
          AND (
                status = ?
                OR next_attempt_after IS NULL
                OR next_attempt_after <= ?
          )
        ORDER BY CASE WHEN status = ? THEN 0 ELSE 1 END, first_seen_at
        LIMIT 1
        """,
        (
            profile_id,
            STATUS_FAIL_RETRYABLE,
            STATUS_FRESH,
            STATUS_FRESH,
            now.isoformat(),
            STATUS_FAIL_RETRYABLE,
        ),
    )
    row = cur.fetchone()
    if row is None:
        cur.execute("COMMIT")
        conn.close()
        return None

    uid_id = row["id"]
    attempts = row["attempts"] + 1
    now_iso = now.isoformat()
    cur.execute(
        """
        UPDATE uids
        SET status = ?, attempts = ?, last_started_at = ?, last_updated_at = ?, heartbeat_at = ?
        WHERE id = ?
        """,
        (STATUS_IN_PROGRESS, attempts, now_iso, now_iso, now_iso, uid_id),
    )
    _record_event(conn, uid_id, "START", None)
    cur.execute("COMMIT")
    conn.close()

    data = dict(row)
    data["status"] = STATUS_IN_PROGRESS
    data["attempts"] = attempts
    data["last_started_at"] = now_iso
    return data


def beat_heartbeat(uid_id: int) -> None:
    conn = _get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE uids SET heartbeat_at = ?, last_updated_at = ? WHERE id = ?",
        (datetime.now(UTC).isoformat(), datetime.now(UTC).isoformat(), uid_id),
    )
    conn.commit()
    conn.close()


def complete_uid(
    uid_id: int,
    status: str,
    *,
    error_code: Optional[str] = None,
    error_msg: Optional[str] = None,
    evidence_path: Optional[str] = None,
    next_attempt_after: Optional[datetime] = None,
    event_payload: Optional[str] = None,
) -> None:
    conn = _get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE uids
        SET status = ?, last_error_code = ?, last_error_msg = ?, last_evidence_path = ?,
            last_updated_at = ?, next_attempt_after = ?, heartbeat_at = NULL
        WHERE id = ?
        """,
        (
            status,
            error_code,
            error_msg,
            evidence_path,
            datetime.now(UTC).isoformat(),
            next_attempt_after.isoformat() if next_attempt_after else None,
            uid_id,
        ),
    )
    _record_event(conn, uid_id, "RESULT", event_payload)
    conn.commit()
    conn.close()


def get_uid_events(uid_id: int, limit: int = 50) -> List[Dict[str, object]]:
    conn = _get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT event_type, event_data, created_at FROM uid_events WHERE uid_id = ? ORDER BY id DESC LIMIT ?",
        (uid_id, limit),
    )
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return rows


def count_total_uids(profile_id: int) -> int:
    conn = _get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM uids WHERE profile_id = ?", (profile_id,))
    value = cur.fetchone()[0]
    conn.close()
    return int(value)


def update_attempt_schedule(uid_id: int, next_attempt_after: datetime) -> None:
    conn = _get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE uids SET next_attempt_after = ?, last_updated_at = ? WHERE id = ?",
        (next_attempt_after.isoformat(), datetime.now(UTC).isoformat(), uid_id),
    )
    conn.commit()
    conn.close()


def force_retry(uid_id: int) -> None:
    """Manually reset a UID to retryable state immediately."""
    conn = _get_connection()
    cur = conn.cursor()
    now_iso = datetime.now(UTC).isoformat()
    cur.execute(
        """
        UPDATE uids
        SET status = ?, next_attempt_after = ?, last_updated_at = ?, attempts = CASE WHEN attempts > 0 THEN attempts - 1 ELSE 0 END
        WHERE id = ?
        """,
        (STATUS_FAIL_RETRYABLE, now_iso, now_iso, uid_id),
    )
    _record_event(conn, uid_id, "RETRY_SCHEDULED", '{"manual": true}')
    conn.commit()
    conn.close()
