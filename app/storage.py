from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional

from zoneinfo import ZoneInfo

ISO_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


@dataclass
class UidRow:
    id: int
    raw_input: str
    normalized_uid: str
    profile_id: int
    status: str
    attempts: int
    last_error_code: Optional[str]
    last_error_msg: Optional[str]
    last_evidence_path: Optional[str]
    first_seen_at: str
    last_updated_at: str


@dataclass
class ImportReport:
    added: int
    duplicates: int
    invalid: List[str]


class Storage:
    def __init__(self, db_path: Path, timezone: str, default_daily_limit: int) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.timezone = ZoneInfo(timezone)
        self._default_daily_limit = int(default_daily_limit)
        self._init_db()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.executescript(
                """
                CREATE TABLE IF NOT EXISTS profiles (
                    id INTEGER PRIMARY KEY,
                    nickname TEXT NOT NULL,
                    daily_limit INTEGER NOT NULL,
                    tz TEXT NOT NULL DEFAULT 'Asia/Kathmandu',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

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
                    UNIQUE(profile_id, normalized_uid)
                );

                CREATE TABLE IF NOT EXISTS uid_events (
                    id INTEGER PRIMARY KEY,
                    uid_id INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    event_data TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS daily_counters (
                    id INTEGER PRIMARY KEY,
                    profile_id INTEGER NOT NULL,
                    date_ymd TEXT NOT NULL,
                    sent_success INTEGER NOT NULL DEFAULT 0,
                    sent_fail INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(profile_id, date_ymd)
                );
                """
            )

            self._ensure_default_profile(cur)

    def _ensure_default_profile(self, cur: sqlite3.Cursor) -> None:
        cur.execute("SELECT COUNT(*) FROM profiles")
        count = cur.fetchone()[0]
        if count:
            return
        now = self._now()
        cur.execute(
            """
            INSERT INTO profiles (nickname, daily_limit, tz, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("Profile 1", self._default_daily_limit, self.timezone.key, now, now),
        )

    def _now(self) -> str:
        return datetime.now(tz=self.timezone).strftime(ISO_FORMAT)

    def _date_today(self) -> str:
        return datetime.now(tz=self.timezone).strftime("%Y-%m-%d")

    def add_uids(self, profile_id: int, lines: Iterable[str]) -> ImportReport:
        normalized = []
        duplicates = 0
        invalid: List[str] = []

        for line in lines:
            raw = line.strip()
            if not raw or raw.startswith("#"):
                continue
            norm = self._normalize_uid(raw)
            if not norm:
                invalid.append(raw)
                continue
            normalized.append((raw, norm))

        if not normalized:
            return ImportReport(added=0, duplicates=duplicates, invalid=invalid)

        now = self._now()
        added = 0
        with self._connect() as conn:
            cur = conn.cursor()
            for raw, norm in normalized:
                try:
                    cur.execute(
                        """
                        INSERT INTO uids (
                            raw_input, normalized_uid, profile_id, status,
                            attempts, first_seen_at, last_updated_at
                        ) VALUES (?, ?, ?, 'FRESH', 0, ?, ?)
                        """,
                        (raw, norm, profile_id, now, now),
                    )
                    uid_id = cur.lastrowid
                    cur.execute(
                        """
                        INSERT INTO uid_events (uid_id, event_type, event_data, created_at)
                        VALUES (?, 'QUEUE', ?, ?)
                        """,
                        (uid_id, json.dumps({"raw": raw}), now),
                    )
                    added += 1
                except sqlite3.IntegrityError:
                    duplicates += 1
        return ImportReport(added=added, duplicates=duplicates, invalid=invalid)

    def _normalize_uid(self, raw: str) -> Optional[str]:
        raw = raw.strip()
        if not raw:
            return None
        if raw.isdigit():
            return raw
        if "facebook.com" in raw:
            if "profile.php" in raw and "id=" in raw:
                return raw.split("id=")[-1].split("&")[0]
            parts = raw.rstrip("/").split("/")
            username = parts[-1]
            if username:
                return username
        if " " in raw or "\t" in raw:
            return None
        return raw

    def list_profiles(self) -> list[sqlite3.Row]:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM profiles ORDER BY id ASC")
            return cur.fetchall()

    def get_profile(self, profile_id: int) -> sqlite3.Row:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,))
            row = cur.fetchone()
            if not row:
                raise ValueError(f"Profile {profile_id} not found")
            return row

    def create_profile(self, nickname: str, daily_limit: int) -> sqlite3.Row:
        now = self._now()
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO profiles (nickname, daily_limit, tz, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (nickname, daily_limit, self.timezone.key, now, now),
            )
            profile_id = cur.lastrowid
            cur.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,))
            return cur.fetchone()

    def update_profile(self, profile_id: int, nickname: str, daily_limit: int) -> None:
        now = self._now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE profiles
                SET nickname = ?, daily_limit = ?, updated_at = ?
                WHERE id = ?
                """,
                (nickname, daily_limit, now, profile_id),
            )

    def lease_next_uid(self, profile_id: int) -> Optional[UidRow]:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT * FROM uids
                WHERE profile_id = ?
                  AND status IN ('FRESH', 'FAIL_RETRYABLE')
                ORDER BY CASE status WHEN 'FAIL_RETRYABLE' THEN 0 ELSE 1 END,
                         first_seen_at ASC
                LIMIT 1
                """,
                (profile_id,),
            )
            row = cur.fetchone()
            if not row:
                return None

            uid_id = row["id"]
            now = self._now()
            cur.execute(
                """
                UPDATE uids
                SET status = 'IN_PROGRESS', last_updated_at = ?, attempts = attempts + 1
                WHERE id = ?
                """,
                (now, uid_id),
            )
            cur.execute(
                """
                INSERT INTO uid_events (uid_id, event_type, event_data, created_at)
                VALUES (?, 'START', ?, ?)
                """,
                (uid_id, json.dumps({"attempt": row["attempts"] + 1}), now),
            )
            return UidRow(
                id=row["id"],
                raw_input=row["raw_input"],
                normalized_uid=row["normalized_uid"],
                profile_id=row["profile_id"],
                status="IN_PROGRESS",
                attempts=row["attempts"] + 1,
                last_error_code=row["last_error_code"],
                last_error_msg=row["last_error_msg"],
                last_evidence_path=row["last_evidence_path"],
                first_seen_at=row["first_seen_at"],
                last_updated_at=now,
            )

    def complete_uid(
        self,
        uid_id: int,
        status: str,
        err_code: Optional[str],
        err_msg: Optional[str],
        evidence: Optional[str],
    ) -> None:
        now = self._now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE uids
                SET status = ?,
                    last_error_code = ?,
                    last_error_msg = ?,
                    last_evidence_path = ?,
                    last_updated_at = ?
                WHERE id = ?
                """,
                (status, err_code, err_msg, evidence, now, uid_id),
            )
            conn.execute(
                """
                INSERT INTO uid_events (uid_id, event_type, event_data, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    uid_id,
                    "SUCCESS" if status == "SUCCESS" else "FAIL",
                    json.dumps({"code": err_code, "message": err_msg, "evidence": evidence}),
                    now,
                ),
            )

    def increment_daily(self, profile_id: int, success: bool) -> None:
        date = self._date_today()
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO daily_counters (profile_id, date_ymd, sent_success, sent_fail)
                VALUES (?, ?, 0, 0)
                ON CONFLICT(profile_id, date_ymd) DO NOTHING
                """,
                (profile_id, date),
            )
            if success:
                cur.execute(
                    """
                    UPDATE daily_counters
                    SET sent_success = sent_success + 1
                    WHERE profile_id = ? AND date_ymd = ?
                    """,
                    (profile_id, date),
                )
            else:
                cur.execute(
                    """
                    UPDATE daily_counters
                    SET sent_fail = sent_fail + 1
                    WHERE profile_id = ? AND date_ymd = ?
                    """,
                    (profile_id, date),
                )

    def get_daily_counts(self, profile_id: int) -> dict[str, int]:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT sent_success, sent_fail
                FROM daily_counters
                WHERE profile_id = ? AND date_ymd = ?
                """,
                (profile_id, self._date_today()),
            )
            row = cur.fetchone()
            if not row:
                return {"sent_success": 0, "sent_fail": 0}
            return {"sent_success": row["sent_success"], "sent_fail": row["sent_fail"]}

    def export_csv(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn, path.open("w", encoding="utf-8") as fh:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT raw_input, normalized_uid, status, attempts, last_error_code, last_error_msg, last_updated_at
                FROM uids
                ORDER BY first_seen_at ASC
                """
            )
            fh.write("raw_input,normalized_uid,status,attempts,last_error_code,last_error_msg,last_updated_at\n")
            for row in cur.fetchall():
                values = [
                    row["raw_input"],
                    row["normalized_uid"],
                    row["status"],
                    str(row["attempts"]),
                    row["last_error_code"] or "",
                    (row["last_error_msg"] or "").replace("\n", " "),
                    row["last_updated_at"],
                ]
                quoted = ['"{}"'.format(val.replace('"', '""')) for val in values]
                fh.write(",".join(quoted) + "\n")
        return path

    def list_uids(self, profile_id: int) -> list[UidRow]:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT *
                FROM uids
                WHERE profile_id = ?
                ORDER BY first_seen_at ASC
                """,
                (profile_id,),
            )
            rows = cur.fetchall()
            result: list[UidRow] = []
            for row in rows:
                result.append(
                    UidRow(
                        id=row["id"],
                        raw_input=row["raw_input"],
                        normalized_uid=row["normalized_uid"],
                        profile_id=row["profile_id"],
                        status=row["status"],
                        attempts=row["attempts"],
                        last_error_code=row["last_error_code"],
                        last_error_msg=row["last_error_msg"],
                        last_evidence_path=row["last_evidence_path"],
                        first_seen_at=row["first_seen_at"],
                        last_updated_at=row["last_updated_at"],
                    )
                )
            return result


__all__ = ["Storage", "ImportReport", "UidRow"]
