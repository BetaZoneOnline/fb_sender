from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, NamedTuple, Optional, Sequence, Tuple

from PyQt6.QtCore import QEventLoop, QObject, QTimer, QUrl, pyqtSignal
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

    _NON_RETRYABLE_REASONS: Sequence[str] = (
        "Composer not found",
        "Composer not found in any same-origin frame",
        "Thread composer unavailable",
    )

    def __init__(self, context: WorkerContext) -> None:
        super().__init__()
        self._context = context
        self._automation = create_automation(context.view)

    def send_message_to_uid(
        self,
        profile_ctx: dict,
        uid: str,
        timeout_sec: int,
        message: str,
    ) -> SendResult:
        page = self._context.view.page()
        target_url = f"https://www.facebook.com/messages/t/{uid}"
        self.progress.emit("navigate", {"url": target_url})
        if not self._load_page(page, target_url, timeout_sec):
            return SendResult(
                "FAIL_RETRYABLE",
                "NAV_TIMEOUT",
                "Chat page failed to load",
                None,
            )

        self.progress.emit("page_loaded", {"uid": uid})
        result = self._run_automation(message, timeout_sec)
        if result is None:
            return SendResult(
                "FAIL_RETRYABLE",
                "SEND_TIMEOUT",
                "Message send timed out",
                None,
            )

        success, reason = result
        if success:
            return SendResult("SUCCESS", None, None, None)

        non_retryable: Tuple[str, ...]
        automation_reasons = getattr(self._automation, "non_retryable_failure_reasons", ())
        if isinstance(automation_reasons, (list, tuple, set)):
            non_retryable = tuple(automation_reasons) + tuple(self._NON_RETRYABLE_REASONS)
        else:
            non_retryable = tuple(self._NON_RETRYABLE_REASONS)

        reason_text = reason or "Unknown failure"
        if reason_text in non_retryable:
            return SendResult("FAIL_PERM", "UI_NOT_FOUND", reason_text, None)

        return SendResult("FAIL_RETRYABLE", "UNKNOWN", reason_text, None)

    def _load_page(self, page, url: str, timeout_sec: int) -> bool:
        loop = QEventLoop()
        timer = QTimer()
        timer.setSingleShot(True)
        result: dict[str, Optional[bool]] = {"ok": None}

        def on_finished(ok: bool) -> None:
            result["ok"] = ok
            if timer.isActive():
                timer.stop()
            loop.quit()

        page.loadFinished.connect(on_finished)
        page.load(QUrl(url))

        timer.timeout.connect(loop.quit)
        timeout_ms = max(timeout_sec, 1) * 1000
        timer.start(timeout_ms)
        loop.exec()

        try:
            page.loadFinished.disconnect(on_finished)
        except Exception:
            pass

        if timer.isActive():
            timer.stop()

        return bool(result["ok"])

    def _run_automation(self, message: str, timeout_sec: int) -> Optional[Tuple[bool, Optional[str]]]:
        loop = QEventLoop()
        timer = QTimer()
        timer.setSingleShot(True)
        callback_result: dict[str, Tuple[bool, Optional[str]]] = {}

        def _callback(success: bool, reason: Optional[str] = None) -> None:
            callback_result["value"] = (success, reason)
            if timer.isActive():
                timer.stop()
            loop.quit()

        self.progress.emit("automation", {"stage": "start"})
        self._automation.set_message(message)
        self._automation.set_callback(_callback)
        self._automation.automate_messaging(message=message, delay=2)

        timer.timeout.connect(loop.quit)
        timeout_ms = max(timeout_sec, 1) * 1000
        timer.start(timeout_ms)
        loop.exec()

        if timer.isActive():
            timer.stop()

        if "value" not in callback_result:
            stop_timer = getattr(self._automation, "_stop_timer", None)
            if callable(stop_timer):
                stop_timer("Automation timed out")
            return None

        self.progress.emit("automation", {"stage": "completed"})
        return callback_result["value"]


def build_worker(view: QWebEngineView) -> FBWorker:
    context = WorkerContext(view=view)
    return FBWorker(context)


__all__ = ["FBWorker", "SendResult", "build_worker"]
