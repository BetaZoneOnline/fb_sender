from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Optional

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal

from app.storage import Storage, UidRow


@dataclass
class EngineConfig:
    delay_between_uids_sec: int
    page_load_countdown_sec: int
    retry_max_attempts: int
    retry_backoff_sec: int
    result_decrement_on: str


class WorkerRunner(QObject):
    finished = pyqtSignal(UidRow, str, object, object, object)
    progress = pyqtSignal(str, dict)

    def __init__(self, worker_factory: Callable[[], object], uid: UidRow, profile_ctx: dict, timeout_sec: int) -> None:
        super().__init__()
        self._worker_factory = worker_factory
        self._uid = uid
        self._profile_ctx = profile_ctx
        self._timeout_sec = timeout_sec

    def run(self) -> None:
        worker = self._worker_factory()
        if hasattr(worker, "progress"):
            try:
                worker.progress.connect(self.progress)  # type: ignore[attr-defined]
            except TypeError:
                pass
        send_message = getattr(worker, "send_message_to_uid")
        result = send_message(self._profile_ctx, self._uid.normalized_uid, self._timeout_sec)
        status = result.status
        self.finished.emit(
            self._uid,
            status,
            result.error_code,
            result.error_msg,
            result.evidence_path,
        )


class TaskEngine(QObject):
    uid_started = pyqtSignal(str)
    uid_progress = pyqtSignal(str, str, dict)
    uid_result = pyqtSignal(str, str, object, object, object)
    engine_state = pyqtSignal(str)
    limit_update = pyqtSignal(int, int)
    current_uid_changed = pyqtSignal(object)
    countdown_tick = pyqtSignal(int)

    def __init__(
        self,
        storage: Storage,
        profile_manager,
        message_supplier: Callable[[], str],
        worker_factory: Callable[[str], object],
        config: EngineConfig,
    ) -> None:
        super().__init__()
        self._storage = storage
        self._profile_manager = profile_manager
        self._message_supplier = message_supplier
        self._worker_factory = worker_factory
        self._config = config
        self._state = "IDLE"
        self._thread: Optional[QThread] = None
        self._current_uid: Optional[UidRow] = None
        self._countdown_timer = QTimer()
        self._countdown_timer.setSingleShot(True)
        self._countdown_timer.timeout.connect(self._process_next)
        self._pending_delay = 0
        self._tick_timer = QTimer()
        self._tick_timer.timeout.connect(self._on_tick)

    def start(self) -> None:
        if self._state in {"RUNNING", "STARTING"}:
            return
        self._set_state("RUNNING")
        self._process_next()

    def pause(self) -> None:
        if self._state != "RUNNING":
            return
        self._set_state("PAUSED")

    def resume(self) -> None:
        if self._state != "PAUSED":
            return
        self._set_state("RUNNING")
        self._process_next()

    def stop(self) -> None:
        self._set_state("STOPPED")
        self._current_uid = None
        if self._thread and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait()
        self._countdown_timer.stop()
        self._tick_timer.stop()

    def login_only(self) -> None:
        self.stop()
        self._set_state("LOGIN_ONLY")

    def _process_next(self) -> None:
        if self._state != "RUNNING":
            return

        limit_reached = self._emit_limit_status()
        if limit_reached:
            self._set_state("PAUSED_LIMIT")
            return

        profile_id = self._profile_manager.profile_id
        uid = self._storage.lease_next_uid(profile_id)
        if not uid:
            self._set_state("IDLE")
            self._current_uid = None
            return

        self._current_uid = uid
        self.current_uid_changed.emit(uid)
        self.uid_started.emit(uid.normalized_uid)
        self._start_worker(uid)

    def _start_worker(self, uid: UidRow) -> None:
        if self._thread and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait()
        self._thread = QThread()
        profile_ctx = {
            "profile_id": self._profile_manager.profile_id,
            "nickname": self._profile_manager.nickname,
            "message": self._message_supplier(),
        }
        message = profile_ctx["message"]
        runner = WorkerRunner(lambda: self._worker_factory(message), uid, profile_ctx, self._config.page_load_countdown_sec)
        runner.moveToThread(self._thread)
        runner.finished.connect(self._handle_result)
        runner.progress.connect(lambda stage, info: self.uid_progress.emit(uid.normalized_uid, stage, info))
        self._thread.started.connect(runner.run)
        runner.finished.connect(lambda *_: self._thread.quit())
        self._thread.finished.connect(runner.deleteLater)
        self._thread.start()

    def _handle_result(self, uid: UidRow, status: str, err_code, err_msg, evidence) -> None:
        self.uid_result.emit(uid.normalized_uid, status, err_code, err_msg, evidence)
        max_attempts = self._config.retry_max_attempts
        attempts = uid.attempts
        final_status = status
        if status == "FAIL_RETRYABLE" and attempts >= max_attempts:
            final_status = "FAIL_PERM"
        terminal = final_status in {"SUCCESS", "FAIL_PERM"}
        success = final_status == "SUCCESS"
        self._storage.complete_uid(
            uid.id,
            final_status,
            err_code,
            err_msg,
            evidence,
        )
        if terminal:
            self._storage.increment_daily(self._profile_manager.profile_id, success)

        if final_status == "FAIL_RETRYABLE":
            delay = self._config.retry_backoff_sec * (2 ** max(attempts - 1, 0))
        else:
            delay = self._config.delay_between_uids_sec
        self._schedule_next(delay)
        self._emit_limit_status()

    def _schedule_next(self, delay: int) -> None:
        if self._state != "RUNNING":
            return
        self._pending_delay = delay
        self.countdown_tick.emit(max(delay, 0))
        self._countdown_timer.start(max(delay, 0) * 1000)
        if delay > 0:
            self._tick_timer.start(1000)
        else:
            self._tick_timer.stop()

    def _set_state(self, state: str) -> None:
        self._state = state
        self.engine_state.emit(state)
        if state != "RUNNING":
            self._tick_timer.stop()

    def _emit_limit_status(self) -> bool:
        status = self._profile_manager.compute_daily_status()
        resets_in = max(int(math.ceil(status.resets_in.total_seconds())), 0)
        self.limit_update.emit(status.remaining, resets_in)
        return status.remaining <= 0

    def _on_tick(self) -> None:
        if self._state != "RUNNING":
            self._tick_timer.stop()
            return
        if self._pending_delay <= 0:
            self._tick_timer.stop()
            self.countdown_tick.emit(0)
            return
        self._pending_delay -= 1
        self.countdown_tick.emit(max(self._pending_delay, 0))

    @property
    def state(self) -> str:
        return self._state


__all__ = ["TaskEngine", "EngineConfig"]
