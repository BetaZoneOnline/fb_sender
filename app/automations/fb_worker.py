from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from PyQt6.QtCore import QObject, QEventLoop, QTimer, pyqtSignal

from automation import create_automation

from ..browser_window import BrowserWindow
from ..profile_manager import ProfileManager, RuntimeSettings
from .. import storage


@dataclass
class SendResult:
    status: Literal[storage.STATUS_SUCCESS, storage.STATUS_FAIL_RETRYABLE, storage.STATUS_FAIL_PERM]
    error_code: Optional[str]
    error_msg: Optional[str]
    evidence_path: Optional[str]


class FBWorker(QObject):
    """Adapter that bridges the BrowserAutomation helper to the new task engine."""

    progress = pyqtSignal(str, dict)

    def __init__(self, browser_window: BrowserWindow, profile_manager: ProfileManager):
        super().__init__()

        self.browser_window = browser_window
        self.profile_manager = profile_manager

    def send_message_to_uid(self, uid: str, message: str, settings: RuntimeSettings) -> SendResult:
        view = self.browser_window.current_browser()
        # Navigation ----------------------------------------------------
        self.progress.emit("navigate", {"uid": uid})  # type: ignore[func-returns-value]

        load_ok = self._wait_for_page_load(view, uid, settings)
        if not load_ok:
            return SendResult(storage.STATUS_FAIL_RETRYABLE, "NAV_TIMEOUT", "Failed to load Messenger thread", None)

        # Countdown before automation to allow async content to settle
        if settings.page_load_countdown_sec > 0:
            self._countdown(settings.page_load_countdown_sec, "page_load_wait", uid)

        automation = create_automation(view)
        automation.set_message(message)
        automation.max_attempts = settings.retry_max_attempts

        result_holder: dict[str, object] = {}
        loop = QEventLoop()

        def on_complete(success: bool, reason: Optional[str] = None):
            result_holder["success"] = success
            if reason:
                result_holder["reason"] = reason
            loop.quit()

        timeout_timer = QTimer()
        timeout_timer.setSingleShot(True)

        def on_timeout():
            result_holder.setdefault("success", False)
            result_holder.setdefault("reason", "Automation timed out")
            loop.quit()

        timeout_timer.timeout.connect(on_timeout)

        automation.automate_messaging(
            message=message,
            delay=settings.message_retry_delay_sec,
            callback=on_complete,
        )
        timeout_timer.start(max(45, settings.retry_max_attempts * settings.message_retry_delay_sec + 10) * 1000)
        loop.exec()
        timeout_timer.stop()

        success = bool(result_holder.get("success"))
        reason = result_holder.get("reason")

        if success:
            self.progress.emit("sent", {"uid": uid})  # type: ignore[func-returns-value]
            return SendResult(storage.STATUS_SUCCESS, None, None, None)

        error_msg = str(reason or "Unknown automation error")
        status, error_code = self._classify_failure(error_msg)
        evidence_path = None
        if settings.capture_screenshots_on_fail and status != storage.STATUS_SUCCESS:
            evidence_path = self._capture_evidence(uid)

        self.progress.emit(
            "failed",
            {"uid": uid, "status": status, "error_code": error_code, "error_msg": error_msg},
        )  # type: ignore[func-returns-value]

        return SendResult(status, error_code, error_msg, evidence_path)

    # ------------------------------------------------------------------
    def _wait_for_page_load(self, view, uid: str, settings: RuntimeSettings) -> bool:
        loop = QEventLoop()
        success_flag = {"value": False}

        def on_finished(success: bool):
            success_flag["value"] = bool(success)
            try:
                view.loadFinished.disconnect(on_finished)
            finally:
                loop.quit()

        view.loadFinished.connect(on_finished)
        self.browser_window.open_chat(uid)

        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)
        timeout_ms = max(15000, (settings.page_load_countdown_sec + 15) * 1000)
        timer.start(timeout_ms)
        loop.exec()
        timer.stop()
        return success_flag["value"]

    def _countdown(self, seconds: int, stage: str, uid: str) -> None:
        if seconds <= 0:
            return
        loop = QEventLoop()
        remaining = {"value": seconds}
        timer = QTimer()
        timer.setInterval(1000)

        def tick():
            remaining["value"] -= 1
            self.progress.emit(stage, {"uid": uid, "remaining": remaining["value"]})  # type: ignore
            if remaining["value"] <= 0:
                timer.stop()
                loop.quit()

        timer.timeout.connect(tick)
        timer.start()
        # Emit initial state
        self.progress.emit(stage, {"uid": uid, "remaining": seconds})  # type: ignore
        loop.exec()

    def _capture_evidence(self, uid: str) -> Optional[str]:
        pixmap = self.browser_window.grab_current_frame()
        if pixmap.isNull():
            return None
        path = self.profile_manager.evidence_path_for(uid)
        if pixmap.save(str(path)):
            return str(path)
        return None

    @staticmethod
    def _classify_failure(reason: str) -> tuple[str, str]:
        normalized = reason.lower()
        if any(token in normalized for token in ("composer", "message input box", "message box")):
            return storage.STATUS_FAIL_PERM, "UI_NOT_FOUND"
        if "timeout" in normalized or "timed out" in normalized or "load" in normalized:
            return storage.STATUS_FAIL_RETRYABLE, "NAV_TIMEOUT"
        if "login" in normalized or "auth" in normalized:
            return storage.STATUS_FAIL_RETRYABLE, "AUTH_REQUIRED"
        return storage.STATUS_FAIL_RETRYABLE, "UNKNOWN"
