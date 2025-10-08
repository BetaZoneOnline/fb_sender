from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

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
from PyQt6.QtWebEngineWidgets import QWebEnginePage, QWebEngineProfile, QWebEngineView

from app.profile_manager import ProfileManager
from app.storage import ImportReport, Storage, UidRow
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

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._profile: Optional[QWebEngineProfile] = None
        self._profile_path: Optional[Path] = None

    def event(self, event):  # type: ignore[override]
        if event.type() == event.Type.ToolTip and self.url().host() in self.FACEBOOK_HOSTS:
            return True
        return super().event(event)

    def set_profile_storage(self, path: Path) -> None:
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        cache_path = path / "cache"
        cache_path.mkdir(parents=True, exist_ok=True)

        profile = QWebEngineProfile(str(path), self)
        profile.setPersistentStoragePath(str(path))
        profile.setCachePath(str(cache_path))
        profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)

        page = QWebEnginePage(profile, self)
        self.setPage(page)
        self._profile = profile
        self._profile_path = path

    def open_home(self) -> None:
        self.load(QUrl("https://www.facebook.com/messages"))

    @property
    def profile_path(self) -> Optional[Path]:
        return self._profile_path


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
        self.setWindowTitle("UID Management Controller")
        self.resize(1500, 900)
        self._loading_profiles = False
        self._build_ui()
        self._load_profiles()
        self._connect_engine()
        self._refresh_counts()
        self._refresh_table()
        self._update_limit_display()
        self.status_bar.showMessage(f"Active profile: {self._profile_manager.nickname}", 4000)
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
        self.action_login.triggered.connect(self._login_only)
        self.action_export.triggered.connect(self._export_csv)

        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start(1000)

    def _load_profiles(self, selected_id: Optional[int] = None) -> None:
        profiles = self._profile_manager.list_profiles()
        if not profiles:
            return

        target_id = selected_id or self._profile_manager.profile_id
        self._loading_profiles = True
        self.profile_combo.clear()
        for profile in profiles:
            display = profile.nickname
            self.profile_combo.addItem(display, profile.id)

        index = self.profile_combo.findData(target_id)
        if index < 0:
            index = 0
            target_id = profiles[0].id
        else:
            data = self.profile_combo.itemData(index)
            target_id = int(data) if data is not None else profiles[0].id
        self.profile_combo.setCurrentIndex(index)
        self._loading_profiles = False
        if target_id is not None:
            self._apply_profile_selection(int(target_id))

    def _build_header(self):
        layout = QHBoxLayout()
        layout.addWidget(QLabel("Profile:"))
        self.profile_combo = QComboBox()
        layout.addWidget(self.profile_combo)
        self.btn_new_profile = QPushButton("Add Profile")
        layout.addWidget(self.btn_new_profile)

        self.engine_state_label = QLabel("Engine: IDLE")
        layout.addWidget(self.engine_state_label)

        layout.addStretch()
        self.clock_label = QLabel()
        layout.addWidget(self.clock_label)

        self.profile_combo.currentIndexChanged.connect(self._on_profile_combo_changed)
        self.btn_new_profile.clicked.connect(self._create_profile)
        return layout

    def _on_profile_combo_changed(self) -> None:
        if self._loading_profiles:
            return
        profile_id = self.profile_combo.currentData()
        if profile_id is None:
            return
        self._apply_profile_selection(int(profile_id))

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

    def _apply_profile_selection(self, profile_id: int, load_view: bool = True) -> None:
        if profile_id != self._profile_manager.profile_id and self._engine.state == "RUNNING":
            self._engine.stop()
            self.status_bar.showMessage("Engine stopped due to profile switch", 5000)

        profile = self._profile_manager.select_profile(profile_id)
        self.nickname_edit.setText(profile.nickname)
        self.daily_limit_spin.setValue(profile.daily_limit)

        if load_view:
            current_path = self._web_view.profile_path
            if current_path != profile.data_path:
                self._web_view.set_profile_storage(profile.data_path)
            self._web_view.open_home()

        self._set_current_uid(None)
        self._refresh_counts()
        self._refresh_table()
        self._update_limit_display()
        self.status_bar.showMessage(f"Active profile: {profile.nickname}", 4000)

    def _create_profile(self) -> None:
        name, ok = QInputDialog.getText(self, "New Profile", "Profile nickname:")
        if not ok:
            return
        nickname = name.strip()
        if not nickname:
            QMessageBox.warning(self, "Profile", "Nickname cannot be empty")
            return
        limit, ok = QInputDialog.getInt(
            self,
            "New Profile",
            "Daily limit:",
            self.daily_limit_spin.value(),
            1,
            500,
        )
        if not ok:
            return
        profile = self._profile_manager.create_profile(nickname, limit)
        self._load_profiles(selected_id=profile.id)
        self.status_bar.showMessage(f"Created profile '{profile.nickname}'", 5000)

    def _login_only(self) -> None:
        self._engine.login_only()
        current_path = self._web_view.profile_path
        target_path = self._profile_manager.profile_data_path
        if current_path != target_path:
            self._web_view.set_profile_storage(target_path)
        self._web_view.open_home()
        self.status_bar.showMessage("Login session ready", 5000)

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
        nickname = self.nickname_edit.text().strip()
        if not nickname:
            QMessageBox.warning(self, "Profile", "Nickname cannot be empty")
            return
        limit = self.daily_limit_spin.value()
        self._profile_manager.update_profile(nickname, limit)
        self._load_profiles(selected_id=self._profile_manager.profile_id)
        QMessageBox.information(self, "Profile", "Profile updated")

    def _export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export CSV", "uid_export.csv", "CSV Files (*.csv)")
        if not path:
            return
        target = Path(path)
        self._storage.export_csv(target, self._profile_manager.profile_id)
        QMessageBox.information(self, "Export", f"Exported to {target}")

    def _update_limit_display(self) -> None:
        status = self._profile_manager.compute_daily_status()
        remaining = status.remaining
        self.daily_limit_label.setText(f"Daily remaining: {remaining} / {status.limit}")
        self.reset_label.setText(f"Resets in: {int(status.resets_in.total_seconds())}s")


__all__ = ["UidManagementWindow", "FBWebView"]
