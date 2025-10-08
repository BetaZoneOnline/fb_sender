import json
import os
from datetime import date, datetime
from typing import Dict, List, Optional


class UIDTracker:
    """High level helper that keeps per-UID state and daily counters."""

    TRACKER_VERSION = 2

    def __init__(self, all_uids: List[str], tracker_path: str, daily_limit: int) -> None:
        self._all_uids = all_uids
        self._path = tracker_path
        self._daily_limit = max(0, int(daily_limit))
        self._today = date.today().isoformat()
        self._data = self._load()
        self._ensure_today_bucket()
        self._recover_partial_runs()
        self._save()

    # ------------------------------------------------------------------
    # public helpers
    # ------------------------------------------------------------------
    def set_daily_limit(self, limit: int) -> None:
        self._daily_limit = max(0, int(limit))

    def available_uids(self) -> List[str]:
        catalog = self._data.get("uids", {})
        fresh: List[str] = []
        for uid in self._all_uids:
            state = catalog.get(uid, {})
            if state.get("status", "fresh") != "done":
                fresh.append(uid)
        return fresh

    def next_uid(self) -> Optional[str]:
        available = self.available_uids()
        return available[0] if available else None

    def can_send_more_today(self) -> bool:
        stats = self._data["daily_stats"][self._today]
        if self._daily_limit and stats["successful_sends"] >= self._daily_limit:
            return False
        return bool(self.available_uids())

    def complete_uid(self, uid: str, success: bool, attempts: int, reason: Optional[str]) -> None:
        uid = uid.strip()
        if not uid:
            return

        record = self._data.setdefault("uids", {}).get(uid, {})
        record.update(
            {
                "status": "done",
                "result": "success" if success else "failure",
                "attempts": max(1, int(attempts) if attempts else 1),
                "last_reason": reason or "",
                "last_attempt": datetime.utcnow().isoformat(timespec="seconds"),
            }
        )
        self._data.setdefault("uids", {})[uid] = record

        stats = self._data["daily_stats"][self._today]
        stats["total_attempted"] += 1
        if success:
            stats["successful_sends"] += 1
        else:
            stats["errors"] += 1
        if uid not in stats["used_uids"]:
            stats["used_uids"].append(uid)

        self._save()

    def remaining_quota(self) -> int:
        stats = self._data["daily_stats"][self._today]
        if not self._daily_limit:
            return len(self.available_uids())
        return max(0, self._daily_limit - stats["successful_sends"])

    def today_summary(self) -> Dict[str, int]:
        return dict(self._data["daily_stats"][self._today])

    def status_for(self, uid: str) -> Optional[Dict[str, str]]:
        return self._data.get("uids", {}).get(uid)

    # ------------------------------------------------------------------
    # persistence helpers
    # ------------------------------------------------------------------
    def _load(self) -> Dict:
        if not os.path.exists(self._path):
            return self._default_tracker()

        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (json.JSONDecodeError, OSError):
            return self._default_tracker()

        if raw.get("version") == self.TRACKER_VERSION:
            return raw

        return self._migrate_legacy(raw)

    def _default_tracker(self) -> Dict:
        return {
            "version": self.TRACKER_VERSION,
            "last_reset_date": self._today,
            "uids": {},
            "daily_stats": {self._today: self._blank_daily_stats()},
        }

    def _blank_daily_stats(self) -> Dict:
        return {
            "total_attempted": 0,
            "successful_sends": 0,
            "errors": 0,
            "used_uids": [],
        }

    def _ensure_today_bucket(self) -> None:
        if self._data.get("last_reset_date") == self._today:
            if self._today not in self._data["daily_stats"]:
                self._data["daily_stats"][self._today] = self._blank_daily_stats()
            return

        # New day → add fresh bucket but keep UID completion history
        self._data["last_reset_date"] = self._today
        self._data["daily_stats"][self._today] = self._blank_daily_stats()

    def _recover_partial_runs(self) -> None:
        # Any UID without a terminal status is still "fresh"
        catalog = self._data.setdefault("uids", {})
        for uid, record in list(catalog.items()):
            if record.get("status") not in ("done", "fresh"):
                record["status"] = "fresh"

    def _save(self) -> None:
        tmp_path = f"{self._path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(self._data, fh, indent=4)
        os.replace(tmp_path, self._path)

    def _migrate_legacy(self, payload: Dict) -> Dict:
        migrated = self._default_tracker()
        legacy_used = set(payload.get("used_uids", []))

        for legacy_uid in legacy_used:
            migrated["uids"][legacy_uid] = {
                "status": "done",
                "result": "unknown",
                "attempts": 1,
                "last_reason": "",
                "last_attempt": payload.get("last_reset_date", self._today) + "T00:00:00",
            }

        legacy_daily = payload.get("daily_stats", {})
        for day, stats in legacy_daily.items():
            migrated["daily_stats"][day] = {
                "total_attempted": stats.get("total_attempted", 0),
                "successful_sends": stats.get("successful_sends", 0),
                "errors": stats.get("errors", 0),
                "used_uids": list(dict.fromkeys(stats.get("used_uids", []))),
            }

        if self._today not in migrated["daily_stats"]:
            migrated["daily_stats"][self._today] = self._blank_daily_stats()

        migrated["last_reset_date"] = payload.get("last_reset_date", self._today)
        migrated["version"] = self.TRACKER_VERSION
        return migrated
