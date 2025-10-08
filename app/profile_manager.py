from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple

from zoneinfo import ZoneInfo

from . import storage


@dataclass
class RuntimeSettings:
    delay_between_uids_sec: int
    page_load_countdown_sec: int
    retry_max_attempts: int
    retry_backoff_sec: int
    message_retry_delay_sec: int
    capture_screenshots_on_fail: bool
    result_decrement_on: str
    evidence_dir: Path


class ProfileManager:
    """Manages profile configuration, runtime settings, and time computations."""

    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.config_path = base_dir / "app" / "config" / "defaults.json"
        with self.config_path.open("r", encoding="utf-8") as fh:
            self.defaults = json.load(fh)

        storage.configure(str(base_dir / self.defaults["db_path"]))
        storage.init_db()

        self.profile_row = storage.ensure_profile(
            nickname="Profile 1",
            daily_limit=int(self.defaults.get("daily_limit", 10)),
            tz=self.defaults.get("timezone", "Asia/Kathmandu"),
        )
        self.profile_id = int(self.profile_row["id"])
        self.timezone = ZoneInfo(self.profile_row["tz"])

        self._settings = self._load_settings()
        self.evidence_dir = self._settings.evidence_dir
        self.evidence_dir.mkdir(parents=True, exist_ok=True)

    def _load_settings(self) -> RuntimeSettings:
        raw_settings = self.defaults.copy()
        raw_settings.update(storage.get_settings())

        return RuntimeSettings(
            delay_between_uids_sec=int(raw_settings.get("delay_between_uids_sec", 60)),
            page_load_countdown_sec=int(raw_settings.get("page_load_countdown_sec", 10)),
            retry_max_attempts=int(raw_settings.get("retry_max_attempts", 3)),
            retry_backoff_sec=int(raw_settings.get("retry_backoff_sec", 10)),
            message_retry_delay_sec=int(raw_settings.get("message_retry_delay_sec", 5)),
            capture_screenshots_on_fail=str(raw_settings.get("capture_screenshots_on_fail", True)).lower()
            in {"true", "1", "yes"},
            result_decrement_on=str(raw_settings.get("result_decrement_on", "terminal")),
            evidence_dir=self.base_dir / raw_settings.get("evidence_dir", "data/evidence"),
        )

    # Public accessors -------------------------------------------------
    def refresh_profile(self) -> None:
        row = storage.get_profile(self.profile_id)
        if row is not None:
            self.profile_row = row
            self.timezone = ZoneInfo(self.profile_row["tz"])

    @property
    def nickname(self) -> str:
        return self.profile_row["nickname"]

    def set_nickname(self, nickname: str) -> None:
        storage.update_profile(self.profile_id, nickname=nickname)
        self.refresh_profile()

    @property
    def daily_limit(self) -> int:
        return int(self.profile_row["daily_limit"])

    def set_daily_limit(self, limit: int) -> None:
        storage.update_profile(self.profile_id, daily_limit=limit)
        self.refresh_profile()

    @property
    def settings(self) -> RuntimeSettings:
        return self._settings

    def update_setting(self, key: str, value) -> None:
        storage.set_setting(key, str(value))
        self._settings = self._load_settings()

    # Time helpers -----------------------------------------------------
    def now_local(self) -> datetime:
        return datetime.now(self.timezone)

    def today_local(self) -> str:
        return self.now_local().date().isoformat()

    def next_reset_seconds(self) -> int:
        now = self.now_local()
        next_midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return max(0, int((next_midnight - now).total_seconds()))

    # Messaging assets -------------------------------------------------
    def load_messages(self) -> List[str]:
        message_path = self.base_dir / self.defaults.get("messages_file", "messages.txt")
        if not message_path.exists():
            return ["Hello!"]
        with message_path.open("r", encoding="utf-8") as fh:
            messages = [line.strip() for line in fh if line.strip()]
        return messages or ["Hello!"]

    # Daily counters ---------------------------------------------------
    def ensure_today_counter(self) -> Dict[str, int]:
        today = self.today_local()
        storage.ensure_daily_counter(self.profile_id, today)
        return storage.get_daily_counter(self.profile_id, today)

    def remaining_today(self) -> Tuple[int, Dict[str, int]]:
        counters = self.ensure_today_counter()
        used = counters.get("sent_success", 0) + counters.get("sent_fail", 0)
        remaining = max(0, self.daily_limit - used)
        return remaining, counters

    # Evidence ---------------------------------------------------------
    def evidence_path_for(self, uid: str) -> Path:
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        return self.evidence_dir / f"{uid}_{timestamp}.png"
