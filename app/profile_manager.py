from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Tuple

from zoneinfo import ZoneInfo

from app.storage import ProfileRow


@dataclass
class DailyStatus:
    remaining: int
    limit: int
    resets_in: timedelta
    sent_success: int
    sent_fail: int


class ProfileManager:
    def __init__(self, storage, timezone: str, initial_profile: ProfileRow | None = None) -> None:
        self._storage = storage
        self._timezone = ZoneInfo(timezone)
        self._profile = initial_profile or self._storage.get_profile()

    @property
    def profile_id(self) -> int:
        return int(self._profile.id)

    @property
    def nickname(self) -> str:
        return str(self._profile.nickname)

    @property
    def daily_limit(self) -> int:
        return int(self._profile.daily_limit)

    @property
    def profile_data_path(self) -> Path:
        return self._profile.data_path

    def list_profiles(self) -> List[ProfileRow]:
        return self._storage.list_profiles()

    def select_profile(self, profile_id: int) -> ProfileRow:
        self._profile = self._storage.get_profile(profile_id)
        return self._profile

    def create_profile(self, nickname: str, daily_limit: int) -> ProfileRow:
        self._profile = self._storage.create_profile(nickname, daily_limit)
        return self._profile

    def update_profile(self, nickname: str, daily_limit: int) -> ProfileRow:
        self._profile = self._storage.update_profile(self.profile_id, nickname, daily_limit)
        return self._profile

    def compute_daily_status(self) -> DailyStatus:
        counts = self._storage.get_daily_counts(self.profile_id)
        sent_success = int(counts["sent_success"])
        sent_fail = int(counts["sent_fail"])
        remaining = max(self.daily_limit - (sent_success + sent_fail), 0)
        resets_in = self._time_until_reset()
        return DailyStatus(
            remaining=remaining,
            limit=self.daily_limit,
            resets_in=resets_in,
            sent_success=sent_success,
            sent_fail=sent_fail,
        )

    def _time_until_reset(self) -> timedelta:
        now = datetime.now(tz=self._timezone)
        tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return tomorrow - now

    def should_pause_for_limit(self) -> Tuple[bool, DailyStatus]:
        status = self.compute_daily_status()
        return status.remaining <= 0, status


__all__ = ["ProfileManager", "DailyStatus"]
