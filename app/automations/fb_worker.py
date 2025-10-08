from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, NamedTuple, Optional

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWebEngineWidgets import QWebEngineView

from automation import create_automation


class SendResult(NamedTuple):
    status: Literal['SUCCESS', 'FAIL_RETRYABLE', 'FAIL_PERM']
    error_code: Optional[str]
    error_msg: Optional[str]
    evidence_path: Optional[str]


@dataclass
class WorkerContext:
    view: QWebEngineView


class FBWorker(QObject):
    progress = pyqtSignal(str, dict)

    def __init__(self, context: WorkerContext, message: str) -> None:
        super().__init__()
        self._context = context
        self._message = message
        self._automation = create_automation(context.view)

    def send_message_to_uid(self, profile_ctx: dict, uid: str, timeout_sec: int) -> SendResult:
        page = self._context.view.page()
        target_url = f"https://www.facebook.com/messages/t/{uid}"
        self.progress.emit("navigate", {"url": target_url})
        load_finished = page.loadFinished
        done = []

        def _loaded(ok: bool) -> None:
            done.append(ok)

        load_finished.connect(_loaded)
        page.load(target_url)
        page.runJavaScript("console.log('Navigating to chat');")

        attempts = 0
        while not done and attempts < timeout_sec * 5:
            self.thread().msleep(200)
            attempts += 1
        try:
            load_finished.disconnect(_loaded)
        except Exception:
            pass
        if not done or not done[-1]:
            return SendResult("FAIL_RETRYABLE", "NAV_TIMEOUT", "Chat page failed to load", None)

        self.progress.emit("page_loaded", {"uid": uid})

        def _callback(success: bool, reason: str | None = None) -> None:
            nonlocal callback_result
            callback_result = (success, reason)

        callback_result: tuple[bool, Optional[str]] | None = None
        self._automation.set_message(self._message)
        self._automation.set_callback(_callback)
        self._automation.automate_messaging(message=self._message, delay=2)
        waited = 0
        while callback_result is None and waited < timeout_sec * 5:
            self.thread().msleep(200)
            waited += 1
        if callback_result is None:
            return SendResult("FAIL_RETRYABLE", "SEND_TIMEOUT", "Message send timed out", None)

        success, reason = callback_result
        if success:
            return SendResult("SUCCESS", None, None, None)
        if reason in self._automation.non_retryable_failure_reasons:  # type: ignore[attr-defined]
            return SendResult("FAIL_PERM", "UI_NOT_FOUND", reason or "Composer not found", None)
        return SendResult("FAIL_RETRYABLE", "UNKNOWN", reason or "Unknown failure", None)


def build_worker(view: QWebEngineView, message: str) -> FBWorker:
    context = WorkerContext(view=view)
    return FBWorker(context, message)


__all__ = ["FBWorker", "SendResult", "build_worker"]
