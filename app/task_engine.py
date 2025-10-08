from __future__ import annotations

import json
import random
from datetime import datetime, timedelta
from typing import Dict, Optional

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from . import storage
from .automations.fb_worker import FBWorker, SendResult
from .profile_manager import ProfileManager


class TaskEngine(QObject):
    """Deterministic queue runner that processes UIDs one-by-one."""

    uid_started = pyqtSignal(str)
    uid_progress = pyqtSignal(str, str, dict)
    uid_result = pyqtSignal(str, str, object, object, object)
    engine_state = pyqtSignal(str)
    limit_update = pyqtSignal(int, int)
    stats_updated = pyqtSignal(dict)
    queue_empty = pyqtSignal()

    def __init__(self, profile_manager: ProfileManager, worker: FBWorker):
        super().__init__()
        self.profile_manager = profile_manager
        self.worker = worker

        self.state = "IDLE"
        self.state_reason: Optional[str] = None
        self.current_uid_row: Optional[Dict[str, object]] = None
        self.messages = self.profile_manager.load_messages()

        self.delay_timer = QTimer()
        self.delay_timer.setSingleShot(True)
        self.delay_timer.timeout.connect(self._process_next_uid)

        self.cooldown_timer = QTimer()
        self.cooldown_timer.setInterval(1000)
        self.cooldown_timer.timeout.connect(self._tick_cooldown)
        self.cooldown_remaining = 0

        self.worker.progress.connect(self._relay_worker_progress)

    # ------------------------------------------------------------------
    def start(self) -> None:
        if self.state == "RUNNING":
            return
        self.profile_manager.refresh_profile()
        self.messages = self.profile_manager.load_messages()
        storage.reset_stale_in_progress(self.profile_manager.profile_id)
        self._transition("RUNNING")
        self._update_stats()
        self._schedule_next(0)

    def pause(self) -> None:
        if self.state != "RUNNING":
            return
        self.state_reason = "user"
        self._transition("PAUSED")
        self.delay_timer.stop()
        self.cooldown_timer.stop()

    def resume(self) -> None:
        if self.state not in {"PAUSED", "IDLE"}:
            return
        self.state_reason = None
        self._transition("RUNNING")
        self._schedule_next(0)

    def stop(self) -> None:
        self._transition("STOPPED")
        self.delay_timer.stop()
        self.cooldown_timer.stop()
        self.current_uid_row = None

    # ------------------------------------------------------------------
    def _transition(self, state: str) -> None:
        self.state = state
        self.engine_state.emit(state)

    def _schedule_next(self, delay_seconds: int) -> None:
        self.delay_timer.stop()
        self.cooldown_timer.stop()
        self.cooldown_remaining = delay_seconds
        if delay_seconds <= 0:
            self.delay_timer.start(0)
        else:
            self.delay_timer.start(delay_seconds * 1000)
            self.cooldown_timer.start()
            self.uid_progress.emit("__engine__", "cooldown", {"remaining": delay_seconds})

    def _tick_cooldown(self) -> None:
        if self.cooldown_remaining <= 0:
            self.cooldown_timer.stop()
            return
        self.cooldown_remaining -= 1
        self.uid_progress.emit("__engine__", "cooldown", {"remaining": self.cooldown_remaining})
        if self.cooldown_remaining <= 0:
            self.cooldown_timer.stop()

    def _process_next_uid(self) -> None:
        if self.state != "RUNNING":
            return

        remaining, counters = self.profile_manager.remaining_today()
        resets_in = self.profile_manager.next_reset_seconds()
        self.limit_update.emit(remaining, resets_in)

        if remaining <= 0:
            self.state_reason = "limit"
            self._transition("PAUSED")
            return

        uid_row = storage.lease_next_uid(self.profile_manager.profile_id)
        if uid_row is None:
            self._transition("IDLE")
            self._update_stats()
            self.queue_empty.emit()
            self.current_uid_row = None
            return

        self.current_uid_row = uid_row
        uid_value = str(uid_row["normalized_uid"])
        self.uid_started.emit(uid_value)

        message = random.choice(self.messages)
        settings = self.profile_manager.settings

        result = self.worker.send_message_to_uid(uid_value, message, settings)
        self._handle_result(uid_row, result)

    def _handle_result(self, uid_row: Dict[str, object], result: SendResult) -> None:
        uid_id = int(uid_row["id"])
        uid_value = str(uid_row["normalized_uid"])
        attempts = int(uid_row.get("attempts", 1))
        today = self.profile_manager.today_local()
        payload = json.dumps(
            {
                "status": result.status,
                "error_code": result.error_code,
                "error_msg": result.error_msg,
                "attempt": attempts,
            }
        )

        now = datetime.now(storage.UTC)
        settings = self.profile_manager.settings

        if result.status == storage.STATUS_SUCCESS:
            storage.complete_uid(
                uid_id,
                storage.STATUS_SUCCESS,
                error_code=None,
                error_msg=None,
                evidence_path=result.evidence_path,
                event_payload=payload,
            )
            storage.ensure_daily_counter(self.profile_manager.profile_id, today)
            storage.increment_daily(self.profile_manager.profile_id, today, True)
            self.uid_result.emit(uid_value, storage.STATUS_SUCCESS, None, None, result.evidence_path)
            self._update_stats()
            self._schedule_next(settings.delay_between_uids_sec)
            self.current_uid_row = None
            return

        if result.status == storage.STATUS_FAIL_PERM:
            storage.complete_uid(
                uid_id,
                storage.STATUS_FAIL_PERM,
                error_code=result.error_code,
                error_msg=result.error_msg,
                evidence_path=result.evidence_path,
                event_payload=payload,
            )
            storage.ensure_daily_counter(self.profile_manager.profile_id, today)
            storage.increment_daily(self.profile_manager.profile_id, today, False)
            self.uid_result.emit(
                uid_value,
                storage.STATUS_FAIL_PERM,
                result.error_code,
                result.error_msg,
                result.evidence_path,
            )
            self._update_stats()
            self._schedule_next(settings.delay_between_uids_sec)
            self.current_uid_row = None
            return

        # Retryable failure --------------------------------------------
        if attempts >= settings.retry_max_attempts:
            storage.complete_uid(
                uid_id,
                storage.STATUS_FAIL_PERM,
                error_code=result.error_code or "MAX_ATTEMPTS",
                error_msg=result.error_msg,
                evidence_path=result.evidence_path,
                event_payload=payload,
            )
            storage.ensure_daily_counter(self.profile_manager.profile_id, today)
            storage.increment_daily(self.profile_manager.profile_id, today, False)
            self.uid_result.emit(
                uid_value,
                storage.STATUS_FAIL_PERM,
                result.error_code or "MAX_ATTEMPTS",
                result.error_msg,
                result.evidence_path,
            )
            self._update_stats()
            self._schedule_next(settings.delay_between_uids_sec)
            self.current_uid_row = None
            return

        backoff_seconds = min(120, settings.retry_backoff_sec * (2 ** (attempts - 1)))
        storage.complete_uid(
            uid_id,
            storage.STATUS_FAIL_RETRYABLE,
            error_code=result.error_code,
            error_msg=result.error_msg,
            evidence_path=result.evidence_path,
            next_attempt_after=now + timedelta(seconds=backoff_seconds),
            event_payload=payload,
        )
        self.uid_result.emit(
            uid_value,
            storage.STATUS_FAIL_RETRYABLE,
            result.error_code,
            result.error_msg,
            result.evidence_path,
        )
        self._update_stats()
        self._schedule_next(settings.delay_between_uids_sec)
        self.current_uid_row = None

    def _update_stats(self) -> None:
        counts = storage.get_uid_counts(self.profile_manager.profile_id)
        self.stats_updated.emit(counts)

    def _relay_worker_progress(self, stage: str, info: dict) -> None:
        uid = info.get("uid") or (self.current_uid_row or {}).get("normalized_uid", "")
        self.uid_progress.emit(str(uid), stage, info)
