from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from PyQt6.QtCore import QTimer, QUrl
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PyQt6.QtWebEngineWidgets import QWebEngineView

from app.profile_manager import ProfileManager
from app.storage import ImportReport, ProfileRow, Storage, UidRow
from app.task_engine import TaskEngine


class FBWebView(QWebEngineView):
    FACEBOOK_HOSTS = {
        "facebook.com",
        "www.facebook.com",
        "m.facebook.com",
        "web.facebook.com",
        "messenger.com",
        "www.messenger.com",
    }
    HOME_URL = QUrl("https://www.facebook.com/")
    _USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )

    def __init__(self, profile_root: Path) -> None:
        super().__init__()
        self._profile_root = Path(profile_root)
        self._profile_root.mkdir(parents=True, exist_ok=True)
        self._profiles: Dict[int, QWebEngineProfile] = {}
        self._current_profile_id: Optional[int] = None

    def event(self, event):  # type: ignore[override]
        if event.type() == event.Type.ToolTip and self.url().host() in self.FACEBOOK_HOSTS:
            return True
        return super().event(event)

    def set_active_profile(self, profile_id: int) -> None:
        storage_dir = self._profile_root / f"profile_{profile_id}"
        cache_dir = storage_dir / "cache"
        storage_dir.mkdir(parents=True, exist_ok=True)
        cache_dir.mkdir(parents=True, exist_ok=True)

        profile = self._profiles.get(profile_id)
        if profile is None:
            profile = QWebEngineProfile(f"profile_{profile_id}", self)
            profile.setPersistentCookiesPolicy(
                QWebEngineProfile.PersistentCookiesPolicy.AllowPersistentCookies
            )
            profile.setPersistentStoragePath(str(storage_dir))
            profile.setCachePath(str(cache_dir))
            profile.setHttpUserAgent(self._USER_AGENT)
            self._profiles[profile_id] = profile

        page = QWebEnginePage(profile, self)
        self.setPage(page)
        self._current_profile_id = profile_id
        self.open_home()

    def open_home(self) -> None:
        self.load(self.HOME_URL)

    def current_profile_id(self) -> Optional[int]:
        return self._current_profile_id


@dataclass
class DashboardCounts:
    success: int = 0
    fail: int = 0
    retryable: int = 0
    in_progress: int = 0
    fresh: int = 0


class UidManagementWindow(QMainWindow):
    def __init__(
        self,
        storage: Storage,
        profile_manager: ProfileManager,
        task_engine: TaskEngine,
        engine_config,
        web_view: FBWebView,
    ) -> None:
        super().__init__()
        self._storage = storage
        self._profile_manager = profile_manager
        self._engine = task_engine
        self._engine_config = engine_config
        self._web_view = web_view
        self._profile_rows: Dict[int, ProfileRow] = {}
        self.setWindowTitle("UID Management Controller")
        self.resize(1500, 900)
        self._build_ui()
        self._connect_engine()
        self._refresh_profile_options(self._profile_manager.profile_id)
        self._web_view.set_active_profile(self._profile_manager.profile_id)
        self._update_profile_fields()
        self._refresh_counts()
        self._refresh_table()
        self._update_limit_display()
        self._update_clock()

    def _build_ui(self) -> None:
        toolbar = QToolBar("Main toolbar")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self.action_start = QAction("Start", self)
        self.action_pause = QAction("Pause", self)
        self.action_resume = QAction("Resume", self)
        self.action_stop = QAction("Stop", self)
        self.action_login = QAction("Login Only", self)
        self.action_export = QAction("Export CSV", self)

        toolbar.addAction(self.action_start)
        toolbar.addAction(self.action_pause)
        toolbar.addAction(self.action_resume)
        toolbar.addAction(self.action_stop)
        toolbar.addSeparator()
        toolbar.addAction(self.action_login)
        toolbar.addSeparator()
        toolbar.addAction(self.action_export)

        self.status_bar = self.statusBar()
        self.status_bar.showMessage("Idle")

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        header = self._build_header()
        layout.addLayout(header)

        splitter = QSplitter()
        layout.addWidget(splitter, 1)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.addWidget(self._build_import_group())
        left_layout.addWidget(self._build_settings_group())
        left_layout.addStretch()
        splitter.addWidget(left_panel)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.addLayout(self._build_dashboard())
        right_layout.addWidget(self._build_current_uid_card())
        right_layout.addWidget(self._build_webview_container(), 1)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(1, 2)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels([
            "#",
            "UID",
            "Status",
            "Attempts",
            "Last Error",
            "Updated",
            "Evidence",
        ])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table, 2)

        self.action_start.triggered.connect(self._start_engine)
        self.action_pause.triggered.connect(self._engine.pause)
        self.action_resume.triggered.connect(self._engine.resume)
        self.action_stop.triggered.connect(self._engine.stop)
        self.action_login.triggered.connect(self._login_only_mode)
        self.action_export.triggered.connect(self._export_csv)

        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start(1000)

    def _build_header(self):
        layout = QHBoxLayout()
        profile_caption = QLabel("Profile:")
        profile_caption.setStyleSheet("font-weight: bold;")
        layout.addWidget(profile_caption)

        self.profile_combo = QComboBox()
        self.profile_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.profile_combo.currentIndexChanged.connect(self._on_profile_changed)
        layout.addWidget(self.profile_combo)

        self.new_profile_btn = QPushButton("New Profile")
        self.new_profile_btn.clicked.connect(self._create_profile)
        layout.addWidget(self.new_profile_btn)

        self.engine_state_label = QLabel("Engine: IDLE")
        layout.addWidget(self.engine_state_label)

        layout.addStretch()
        self.clock_label = QLabel()
        layout.addWidget(self.clock_label)
        return layout

    def _refresh_profile_options(self, select_id: Optional[int] = None) -> None:
        profiles = self._storage.list_profiles()
        if not profiles:
            return
        self._profile_rows = {row.id: row for row in profiles}
        target_id = select_id
        if target_id is None or target_id not in self._profile_rows:
            current_id = self._profile_manager.profile_id
            target_id = current_id if current_id in self._profile_rows else profiles[0].id

        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        for row in profiles:
            self.profile_combo.addItem(row.nickname, row.id)
        idx = self.profile_combo.findData(target_id)
        if idx == -1:
            idx = 0
        self.profile_combo.setCurrentIndex(idx)
        self.profile_combo.blockSignals(False)

    def _update_profile_fields(self) -> None:
        self.nickname_edit.setText(self._profile_manager.nickname)
        self.daily_limit_spin.setValue(self._profile_manager.daily_limit)
        idx = self.profile_combo.findData(self._profile_manager.profile_id)
        if idx >= 0:
            self.profile_combo.blockSignals(True)
            self.profile_combo.setCurrentIndex(idx)
            self.profile_combo.blockSignals(False)

    def _on_profile_changed(self, index: int) -> None:
        if index < 0:
            return
        profile_id = self.profile_combo.itemData(index)
        if profile_id is None:
            return
        profile = self._profile_rows.get(profile_id)
        if profile is None:
            profile = self._storage.get_profile(profile_id)
            self._profile_rows[profile_id] = profile
        if profile_id == self._profile_manager.profile_id:
            return
        self._activate_profile(profile, show_message=True)

    def _activate_profile(self, profile: ProfileRow, show_message: bool = False) -> None:
        self._engine.stop()
        self._profile_manager.switch_profile(profile)
        self._profile_rows[profile.id] = profile
        self._web_view.set_active_profile(profile.id)
        self._update_profile_fields()
        self._refresh_counts()
        self._refresh_table()
        self._update_limit_display()
        if show_message:
            self.status_bar.showMessage(f"Switched to {profile.nickname}", 5000)

    def _create_profile(self) -> None:
        name, ok = QInputDialog.getText(self, "New Profile", "Profile nickname:", text="")
        if not ok:
            return
        nickname = name.strip() or f"Profile {len(self._profile_rows) + 1}"
        limit, ok = QInputDialog.getInt(
            self,
            "New Profile",
            "Daily limit:",
            value=self._profile_manager.daily_limit,
            min=1,
            max=500,
        )
        if not ok:
            return
        new_profile = self._storage.create_profile(nickname, limit, self._profile_manager.timezone_key)
        self._profile_rows[new_profile.id] = new_profile
        self._refresh_profile_options(new_profile.id)
        self._activate_profile(new_profile, show_message=True)

    def _login_only_mode(self) -> None:
        self._engine.login_only()
        self._web_view.open_home()
        self.status_bar.showMessage("Login-only mode: browser ready", 5000)

    def _build_import_group(self) -> QGroupBox:
        group = QGroupBox("Import UIDs")
        layout = QVBoxLayout(group)
        self.import_text = QTextEdit()
        self.import_text.setPlaceholderText("Paste UIDs here, one per line")
        layout.addWidget(self.import_text)
        buttons_layout = QHBoxLayout()
        self.btn_import_text = QPushButton("Validate & Add")
        self.btn_import_file = QPushButton("Import from File")
        buttons_layout.addWidget(self.btn_import_text)
        buttons_layout.addWidget(self.btn_import_file)
        layout.addLayout(buttons_layout)
        self.import_summary = QLabel("No imports yet")
        layout.addWidget(self.import_summary)

        self.btn_import_text.clicked.connect(self._import_from_text)
        self.btn_import_file.clicked.connect(self._import_from_file)
        return group

    def _build_settings_group(self) -> QGroupBox:
        group = QGroupBox("Run Settings")
        form = QFormLayout(group)

        self.nickname_edit = QLineEdit(self._profile_manager.nickname)
        form.addRow("Nickname", self.nickname_edit)

        self.daily_limit_spin = QSpinBox()
        self.daily_limit_spin.setRange(1, 500)
        self.daily_limit_spin.setValue(self._profile_manager.daily_limit)
        form.addRow("Daily limit", self.daily_limit_spin)

        self.delay_spin = QSpinBox()
        self.delay_spin.setRange(1, 600)
        self.delay_spin.setValue(self._engine_config.delay_between_uids_sec)
        form.addRow("Delay between UIDs (s)", self.delay_spin)

        self.countdown_spin = QSpinBox()
        self.countdown_spin.setRange(5, 120)
        self.countdown_spin.setValue(self._engine_config.page_load_countdown_sec)
        form.addRow("Page load wait (s)", self.countdown_spin)

        self.retry_spin = QSpinBox()
        self.retry_spin.setRange(1, 10)
        self.retry_spin.setValue(self._engine_config.retry_max_attempts)
        form.addRow("Retry attempts", self.retry_spin)

        self.backoff_spin = QSpinBox()
        self.backoff_spin.setRange(1, 600)
        self.backoff_spin.setValue(self._engine_config.retry_backoff_sec)
        form.addRow("Retry backoff (s)", self.backoff_spin)

        save_btn = QPushButton("Save Profile")
        save_btn.clicked.connect(self._save_profile)
        form.addRow(save_btn)
        return group

    def _build_dashboard(self):
        layout = QHBoxLayout()
        self.daily_limit_label = QLabel("Daily remaining: 0 / 0")
        self.daily_limit_label.setStyleSheet("font-size: 16px; font-weight: bold;")
        layout.addWidget(self.daily_limit_label)

        self.reset_label = QLabel("Resets in: --")
        layout.addWidget(self.reset_label)

        layout.addStretch()

        self.count_success = QLabel("Success: 0")
        self.count_fail = QLabel("Fail: 0")
        self.count_retry = QLabel("Retryable: 0")
        self.count_in_progress = QLabel("In progress: 0")
        self.count_fresh = QLabel("Fresh: 0")

        for label in [
            self.count_success,
            self.count_fail,
            self.count_retry,
            self.count_in_progress,
            self.count_fresh,
        ]:
            layout.addWidget(label)

        return layout

    def _build_current_uid_card(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        self.current_uid_label = QLabel("Current UID: -")
        self.current_stage_label = QLabel("Stage: Idle")
        self.next_action_label = QLabel("Next action in: -")
        layout.addWidget(self.current_uid_label)
        layout.addWidget(self.current_stage_label)
        layout.addWidget(self.next_action_label)
        return widget

    def _build_webview_container(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.addWidget(QLabel("Web session"))
        layout.addWidget(self._web_view)
        return container

    def _connect_engine(self) -> None:
        self._engine.engine_state.connect(self._on_engine_state)
        self._engine.uid_started.connect(self._on_uid_started)
        self._engine.uid_progress.connect(self._on_uid_progress)
        self._engine.uid_result.connect(self._on_uid_result)
        self._engine.limit_update.connect(self._on_limit_update)
        self._engine.current_uid_changed.connect(lambda uid: self._set_current_uid(uid))
        self._engine.countdown_tick.connect(self._on_countdown)

    def _start_engine(self) -> None:
        self._engine_config.delay_between_uids_sec = self.delay_spin.value()
        self._engine_config.page_load_countdown_sec = self.countdown_spin.value()
        self._engine_config.retry_max_attempts = self.retry_spin.value()
        self._engine_config.retry_backoff_sec = self.backoff_spin.value()
        self._engine.start()

    def _on_engine_state(self, state: str) -> None:
        self.engine_state_label.setText(f"Engine: {state}")
        self.status_bar.showMessage(state)

    def _on_uid_started(self, uid: str) -> None:
        self.current_uid_label.setText(f"Current UID: {uid}")
        self.current_stage_label.setText("Stage: Navigating")

    def _on_uid_progress(self, uid: str, stage: str, info: dict) -> None:
        self.current_stage_label.setText(f"Stage: {stage}")

    def _on_uid_result(self, uid: str, status: str, err_code, err_msg, evidence) -> None:
        self.current_stage_label.setText("Stage: Completed")
        self._refresh_counts()
        self._refresh_table()
        if status == "SUCCESS":
            self.status_bar.showMessage(f"UID {uid} sent successfully", 5000)
        else:
            reason = err_msg or err_code or "Unknown"
            self.status_bar.showMessage(f"UID {uid} failed: {reason}", 8000)

    def _on_limit_update(self, remaining: int, resets_in: int) -> None:
        status = self._profile_manager.compute_daily_status()
        self.daily_limit_label.setText(f"Daily remaining: {remaining} / {status.limit}")
        self.reset_label.setText(f"Resets in: {resets_in}s")

    def _on_countdown(self, seconds: int) -> None:
        if seconds <= 0:
            self.next_action_label.setText("Next action in: ready")
        else:
            self.next_action_label.setText(f"Next action in: {seconds}s")

    def _set_current_uid(self, uid: Optional[UidRow]) -> None:
        if uid is None:
            self.current_uid_label.setText("Current UID: -")
            self.current_stage_label.setText("Stage: Idle")
        else:
            self.current_uid_label.setText(f"Current UID: {uid.normalized_uid}")

    def _refresh_counts(self) -> None:
        rows = self._storage.list_uids(self._profile_manager.profile_id)
        counts = DashboardCounts()
        for row in rows:
            if row.status == "SUCCESS":
                counts.success += 1
            elif row.status == "FAIL_PERM":
                counts.fail += 1
            elif row.status == "FAIL_RETRYABLE":
                counts.retryable += 1
            elif row.status == "IN_PROGRESS":
                counts.in_progress += 1
            else:
                counts.fresh += 1
        self.count_success.setText(f"Success: {counts.success}")
        self.count_fail.setText(f"Fail: {counts.fail}")
        self.count_retry.setText(f"Retryable: {counts.retryable}")
        self.count_in_progress.setText(f"In progress: {counts.in_progress}")
        self.count_fresh.setText(f"Fresh: {counts.fresh}")

    def _refresh_table(self) -> None:
        rows = self._storage.list_uids(self._profile_manager.profile_id)
        self.table.setRowCount(len(rows))
        for idx, row in enumerate(rows):
            self.table.setItem(idx, 0, QTableWidgetItem(str(idx + 1)))
            self.table.setItem(idx, 1, QTableWidgetItem(row.normalized_uid))
            self.table.setItem(idx, 2, QTableWidgetItem(row.status))
            self.table.setItem(idx, 3, QTableWidgetItem(str(row.attempts)))
            last_error = row.last_error_msg or row.last_error_code or ""
            self.table.setItem(idx, 4, QTableWidgetItem(last_error))
            self.table.setItem(idx, 5, QTableWidgetItem(row.last_updated_at))
            self.table.setItem(idx, 6, QTableWidgetItem(row.last_evidence_path or ""))
        self.table.resizeColumnsToContents()

    def _update_clock(self) -> None:
        now = datetime.now()
        self.clock_label.setText(now.strftime("%Y-%m-%d %H:%M:%S"))

    def _import_from_text(self) -> None:
        text = self.import_text.toPlainText()
        if not text.strip():
            QMessageBox.information(self, "Import", "No UIDs to import")
            return
        lines = text.splitlines()
        report = self._storage.add_uids(self._profile_manager.profile_id, lines)
        self._show_import_summary(report)
        self.import_text.clear()
        self._refresh_counts()
        self._refresh_table()

    def _import_from_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import UIDs", "", "Text Files (*.txt)")
        if not path:
            return
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
        report = self._storage.add_uids(self._profile_manager.profile_id, lines)
        self._show_import_summary(report)
        self._refresh_counts()
        self._refresh_table()

    def _show_import_summary(self, report: ImportReport) -> None:
        summary = f"Added: {report.added}, duplicates: {report.duplicates}"
        if report.invalid:
            summary += f", invalid: {len(report.invalid)}"
        self.import_summary.setText(summary)

    def _save_profile(self) -> None:
        nickname = self.nickname_edit.text().strip() or self._profile_manager.nickname
        limit = self.daily_limit_spin.value()
        self._profile_manager.update_profile(nickname, limit)
        updated_profile = self._storage.get_profile(self._profile_manager.profile_id)
        self._profile_rows[self._profile_manager.profile_id] = updated_profile
        idx = self.profile_combo.findData(updated_profile.id)
        if idx >= 0:
            self.profile_combo.setItemText(idx, updated_profile.nickname)
        self.nickname_edit.setText(updated_profile.nickname)
        QMessageBox.information(self, "Profile", "Profile updated")
        self._refresh_counts()
        self._update_limit_display()

    def _export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export CSV", "uid_export.csv", "CSV Files (*.csv)")
        if not path:
            return
        target = Path(path)
        self._storage.export_csv(target)
        QMessageBox.information(self, "Export", f"Exported to {target}")

    def _update_limit_display(self) -> None:
        status = self._profile_manager.compute_daily_status()
        remaining = status.remaining
        self.daily_limit_label.setText(f"Daily remaining: {remaining} / {status.limit}")
        self.reset_label.setText(f"Resets in: {int(status.resets_in.total_seconds())}s")


__all__ = ["UidManagementWindow", "FBWebView"]
