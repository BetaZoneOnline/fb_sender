from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Tuple

from zoneinfo import ZoneInfo


@dataclass
class DailyStatus:
    remaining: int
    limit: int
    resets_in: timedelta
    sent_success: int
    sent_fail: int


class ProfileManager:
    def __init__(self, profile_row, timezone: str, storage) -> None:
        self._profile_row = profile_row
        self._timezone = ZoneInfo(timezone)
        self._storage = storage

    @property
    def profile_id(self) -> int:
        return int(self._profile_row["id"])

    @property
    def nickname(self) -> str:
        return str(self._profile_row["nickname"])

    @property
    def daily_limit(self) -> int:
        return int(self._profile_row["daily_limit"])

    def update_profile(self, nickname: str, daily_limit: int) -> None:
        self._storage.update_profile(nickname, daily_limit)
        self._profile_row = self._storage.get_profile()

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
