from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt, QVariant
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QAction,
    QFormLayout,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableView,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from . import storage
from .profile_manager import ProfileManager
from .task_engine import TaskEngine
from .browser_window import BrowserWindow


@dataclass
class ErrorSummary:
    code: str
    count: int
    last_message: Optional[str]


class UIDTableModel(QAbstractTableModel):
    headers = [
        "#",
        "UID",
        "Status",
        "Attempts",
        "Last Error",
        "Last Updated",
        "Next Attempt",
        "Evidence",
    ]

    def __init__(self) -> None:
        super().__init__()
        self.rows: List[Dict[str, object]] = []

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # type: ignore[override]
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # type: ignore[override]
        return len(self.headers)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):  # type: ignore[override]
        if not index.isValid() or role not in {Qt.DisplayRole, Qt.ToolTipRole}:
            return QVariant()
        row = self.rows[index.row()]
        col = index.column()
        if col == 0:
            return index.row() + 1
        if col == 1:
            return row.get("normalized_uid")
        if col == 2:
            return row.get("status")
        if col == 3:
            return row.get("attempts")
        if col == 4:
            msg = row.get("last_error_msg") or ""
            code = row.get("last_error_code") or ""
            if code and msg:
                return f"{code}: {msg}"
            return msg or code
        if col == 5:
            return row.get("last_updated_at")
        if col == 6:
            return row.get("next_attempt_after") or "-"
        if col == 7:
            return row.get("last_evidence_path") or ""
        return QVariant()

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):  # type: ignore[override]
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.headers[section]
        return super().headerData(section, orientation, role)

    def set_rows(self, rows: List[Dict[str, object]]) -> None:
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def get_row(self, row_index: int) -> Optional[Dict[str, object]]:
        if 0 <= row_index < len(self.rows):
            return self.rows[row_index]
        return None


class UIDManagementWindow(QMainWindow):
    """Primary UI that orchestrates UID imports, task execution, and dashboard stats."""

    def __init__(self, profile_manager: ProfileManager, task_engine: TaskEngine, browser_window: BrowserWindow):
        super().__init__()
        self.profile_manager = profile_manager
        self.task_engine = task_engine
        self.browser_window = browser_window

        self.setWindowTitle("UID Management Dashboard")
        self.resize(1400, 900)

        self.table_model = UIDTableModel()
        self.error_summaries: Dict[str, ErrorSummary] = {}

        self._build_ui()
        self._connect_signals()

        self.refresh_table()
        self.refresh_dashboard()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        central = QWidget()
        central_layout = QVBoxLayout()
        central.setLayout(central_layout)
        self.setCentralWidget(central)

        header = self._build_header()
        central_layout.addWidget(header)

        splitter = QSplitter()
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_main_panel())
        splitter.addWidget(self._build_error_panel())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setStretchFactor(2, 2)
        central_layout.addWidget(splitter)

    def _build_header(self) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        widget.setLayout(layout)

        self.lbl_profile = QLabel(f"Profile: {self.profile_manager.nickname}")
        self.lbl_state = QLabel("State: IDLE")
        self.lbl_clock = QLabel("Timezone: %s" % self.profile_manager.timezone.key)

        layout.addWidget(self.lbl_profile)
        layout.addWidget(self.lbl_state)
        layout.addStretch(1)
        layout.addWidget(self.lbl_clock)

        return widget

    def _build_left_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout()
        panel.setLayout(layout)

        import_group = QGroupBox("Import UIDs")
        import_layout = QVBoxLayout()
        self.import_text = QTextEdit()
        self.import_text.setPlaceholderText("Paste UIDs here, one per line")
        import_layout.addWidget(self.import_text)

        buttons_row = QHBoxLayout()
        btn_add = QPushButton("Validate & Add")
        btn_add.clicked.connect(self._handle_import_text)
        buttons_row.addWidget(btn_add)

        btn_file = QPushButton("Import from File")
        btn_file.clicked.connect(self._handle_import_file)
        buttons_row.addWidget(btn_file)
        import_layout.addLayout(buttons_row)

        import_group.setLayout(import_layout)
        layout.addWidget(import_group)

        settings_group = QGroupBox("Run Settings")
        form = QFormLayout()
        self.spin_daily_limit = QSpinBox()
        self.spin_daily_limit.setRange(1, 500)
        self.spin_daily_limit.setValue(self.profile_manager.daily_limit)
        self.spin_daily_limit.valueChanged.connect(self._update_daily_limit)
        form.addRow("Daily limit", self.spin_daily_limit)

        settings = self.profile_manager.settings

        self.spin_delay = QSpinBox()
        self.spin_delay.setRange(1, 600)
        self.spin_delay.setValue(settings.delay_between_uids_sec)
        self.spin_delay.valueChanged.connect(lambda v: self.profile_manager.update_setting("delay_between_uids_sec", v))
        form.addRow("Delay between UIDs (s)", self.spin_delay)

        self.spin_pageload = QSpinBox()
        self.spin_pageload.setRange(0, 120)
        self.spin_pageload.setValue(settings.page_load_countdown_sec)
        self.spin_pageload.valueChanged.connect(lambda v: self.profile_manager.update_setting("page_load_countdown_sec", v))
        form.addRow("Page-load wait (s)", self.spin_pageload)

        self.spin_retry = QSpinBox()
        self.spin_retry.setRange(1, 10)
        self.spin_retry.setValue(settings.retry_max_attempts)
        self.spin_retry.valueChanged.connect(lambda v: self.profile_manager.update_setting("retry_max_attempts", v))
        form.addRow("Retry attempts", self.spin_retry)

        self.spin_backoff = QSpinBox()
        self.spin_backoff.setRange(1, 300)
        self.spin_backoff.setValue(settings.retry_backoff_sec)
        self.spin_backoff.valueChanged.connect(lambda v: self.profile_manager.update_setting("retry_backoff_sec", v))
        form.addRow("Retry backoff (s)", self.spin_backoff)

        settings_group.setLayout(form)
        layout.addWidget(settings_group)

        controls_group = QGroupBox("Engine Controls")
        controls_layout = QVBoxLayout()
        btn_start = QPushButton("Start")
        btn_start.clicked.connect(self.task_engine.start)
        controls_layout.addWidget(btn_start)

        btn_pause = QPushButton("Pause")
        btn_pause.clicked.connect(self.task_engine.pause)
        controls_layout.addWidget(btn_pause)

        btn_resume = QPushButton("Resume")
        btn_resume.clicked.connect(self.task_engine.resume)
        controls_layout.addWidget(btn_resume)

        btn_stop = QPushButton("Stop")
        btn_stop.clicked.connect(self.task_engine.stop)
        controls_layout.addWidget(btn_stop)

        btn_login = QPushButton("Login Only")
        btn_login.clicked.connect(self.browser_window.show)
        controls_layout.addWidget(btn_login)

        self.btn_retry_selected = QPushButton("Retry Selected")
        self.btn_retry_selected.clicked.connect(self._retry_selected)
        controls_layout.addWidget(self.btn_retry_selected)

        controls_group.setLayout(controls_layout)
        layout.addWidget(controls_group)

        btn_export = QPushButton("Export CSV")
        btn_export.clicked.connect(self._export_csv)
        layout.addWidget(btn_export)

        layout.addStretch(1)
        return panel

    def _build_main_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout()
        panel.setLayout(layout)

        dashboard = QGroupBox("Today")
        dash_layout = QVBoxLayout()

        counts_row = QHBoxLayout()
        self.lbl_remaining = QLabel("Remaining: 0")
        self.lbl_reset = QLabel("Resets in: --:--:--")
        counts_row.addWidget(self.lbl_remaining)
        counts_row.addWidget(self.lbl_reset)
        counts_row.addStretch(1)
        dash_layout.addLayout(counts_row)

        stats_row = QHBoxLayout()
        self.count_labels: Dict[str, QLabel] = {}
        for label, key in [
            ("Fresh", storage.STATUS_FRESH),
            ("In progress", storage.STATUS_IN_PROGRESS),
            ("Success", storage.STATUS_SUCCESS),
            ("Fail (retry)", storage.STATUS_FAIL_RETRYABLE),
            ("Fail (perm)", storage.STATUS_FAIL_PERM),
        ]:
            widget = QLabel(f"{label}: 0")
            self.count_labels[key] = widget
            stats_row.addWidget(widget)
        stats_row.addStretch(1)
        dash_layout.addLayout(stats_row)

        current_row = QHBoxLayout()
        self.lbl_current_uid = QLabel("Current UID: -")
        self.lbl_current_stage = QLabel("Stage: idle")
        current_row.addWidget(self.lbl_current_uid)
        current_row.addWidget(self.lbl_current_stage)
        current_row.addStretch(1)
        dash_layout.addLayout(current_row)

        dashboard.setLayout(dash_layout)
        layout.addWidget(dashboard)

        table_group = QGroupBox("UID Queue")
        table_layout = QVBoxLayout()
        self.table_view = QTableView()
        self.table_view.setModel(self.table_model)
        self.table_view.horizontalHeader().setStretchLastSection(True)
        self.table_view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table_view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table_view.setAlternatingRowColors(True)
        table_layout.addWidget(self.table_view)
        table_group.setLayout(table_layout)
        layout.addWidget(table_group)

        return panel

    def _build_error_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout()
        panel.setLayout(layout)

        summary_group = QGroupBox("Error Summary")
        summary_layout = QVBoxLayout()
        self.error_list = QListWidget()
        summary_layout.addWidget(self.error_list)
        summary_group.setLayout(summary_layout)
        layout.addWidget(summary_group)

        last_group = QGroupBox("Last Result")
        last_layout = QVBoxLayout()
        self.lbl_last_result = QLabel("No results yet")
        last_layout.addWidget(self.lbl_last_result)
        last_group.setLayout(last_layout)
        layout.addWidget(last_group)

        layout.addStretch(1)
        return panel

    # ------------------------------------------------------------------
    def _connect_signals(self) -> None:
        self.task_engine.uid_started.connect(self._on_uid_started)
        self.task_engine.uid_progress.connect(self._on_uid_progress)
        self.task_engine.uid_result.connect(self._on_uid_result)
        self.task_engine.engine_state.connect(self._on_engine_state)
        self.task_engine.limit_update.connect(self._on_limit_update)
        self.task_engine.stats_updated.connect(self._on_stats_updated)
        self.task_engine.queue_empty.connect(lambda: self.lbl_current_stage.setText("Stage: queue empty"))

    # Event handlers ---------------------------------------------------
    def _on_uid_started(self, uid: str) -> None:
        self.lbl_current_uid.setText(f"Current UID: {uid}")
        self.lbl_current_stage.setText("Stage: navigating")

    def _on_uid_progress(self, uid: str, stage: str, info: dict) -> None:
        if uid == "__engine__" and stage == "cooldown":
            remaining = info.get("remaining", 0)
            self.lbl_current_stage.setText(f"Stage: cooldown ({remaining}s)")
            return
        if stage == "page_load_wait":
            remaining = info.get("remaining", 0)
            self.lbl_current_stage.setText(f"Stage: waiting for DOM ({remaining}s)")
        elif stage == "navigate":
            self.lbl_current_stage.setText("Stage: navigating")
        elif stage == "sent":
            self.lbl_current_stage.setText("Stage: sent")
        elif stage == "failed":
            self.lbl_current_stage.setText("Stage: failed")

    def _on_uid_result(self, uid: str, status: str, error_code, error_msg, evidence) -> None:
        msg = f"{uid}: {status}"
        if error_code:
            msg += f" ({error_code})"
        if error_msg:
            msg += f" - {error_msg}"
        self.lbl_last_result.setText(msg)
        self.refresh_table()
        self.refresh_error_summary()

    def _on_engine_state(self, state: str) -> None:
        self.lbl_state.setText(f"State: {state}")

    def _on_limit_update(self, remaining: int, resets_in: int) -> None:
        self.lbl_remaining.setText(f"Remaining: {remaining}")
        hours = resets_in // 3600
        minutes = (resets_in % 3600) // 60
        seconds = resets_in % 60
        self.lbl_reset.setText(f"Resets in: {hours:02d}:{minutes:02d}:{seconds:02d}")

    def _on_stats_updated(self, counts: Dict[str, int]) -> None:
        for key, label in self.count_labels.items():
            label.setText(f"{label.text().split(':')[0]}: {counts.get(key, 0)}")

    # Actions ----------------------------------------------------------
    def refresh_table(self) -> None:
        rows = storage.list_uids(self.profile_manager.profile_id)
        self.table_model.set_rows(rows)

    def refresh_dashboard(self) -> None:
        counts = storage.get_uid_counts(self.profile_manager.profile_id)
        total = storage.count_total_uids(self.profile_manager.profile_id)
        for key, label in self.count_labels.items():
            label.setText(f"{label.text().split(':')[0]}: {counts.get(key, 0)}")
        remaining, _ = self.profile_manager.remaining_today()
        self._on_limit_update(remaining, self.profile_manager.next_reset_seconds())
        self.lbl_profile.setText(f"Profile: {self.profile_manager.nickname} (Total {total})")
        self.refresh_error_summary()

    def refresh_error_summary(self) -> None:
        rows = storage.list_uids(self.profile_manager.profile_id, [storage.STATUS_FAIL_PERM, storage.STATUS_FAIL_RETRYABLE])
        summary: Dict[str, ErrorSummary] = {}
        for row in rows:
            code = row.get("last_error_code") or "UNKNOWN"
            msg = row.get("last_error_msg")
            if code not in summary:
                summary[code] = ErrorSummary(code=code, count=0, last_message=None)
            summary[code].count += 1
            if msg:
                summary[code].last_message = msg
        self.error_list.clear()
        for item in sorted(summary.values(), key=lambda s: (-s.count, s.code)):
            text = f"{item.code}: {item.count}"
            if item.last_message:
                text += f" | {item.last_message}"
            QListWidgetItem(text, self.error_list)

    def _handle_import_text(self) -> None:
        lines = self.import_text.toPlainText().splitlines()
        if not lines:
            QMessageBox.warning(self, "Import", "Paste at least one UID")
            return
        report = storage.add_uids(self.profile_manager.profile_id, lines)
        self.import_text.clear()
        self._show_import_report(report)
        self.refresh_table()
        self.refresh_dashboard()

    def _handle_import_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select UID file", str(Path.cwd()), "Text Files (*.txt);;All Files (*)")
        if not path:
            return
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
        report = storage.add_uids(self.profile_manager.profile_id, lines)
        self._show_import_report(report)
        self.refresh_table()
        self.refresh_dashboard()

    def _show_import_report(self, report) -> None:
        message = [
            f"Added: {report.added}",
            f"Duplicates: {report.duplicates}",
            f"Invalid: {len(report.invalid)}",
        ]
        QMessageBox.information(self, "Import summary", "\n".join(message))

    def _update_daily_limit(self, value: int) -> None:
        self.profile_manager.set_daily_limit(value)
        self.refresh_dashboard()

    def _retry_selected(self) -> None:
        selection = self.table_view.selectionModel().selectedRows()
        if not selection:
            return
        for index in selection:
            row = self.table_model.get_row(index.row())
            if not row:
                continue
            storage.force_retry(int(row["id"]))
        self.refresh_table()
        self.refresh_dashboard()

    def _export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export CSV", str(Path.cwd() / "uid_export.csv"), "CSV (*.csv)")
        if not path:
            return
        rows = storage.list_uids(self.profile_manager.profile_id)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(self.table_model.headers)
            for idx, row in enumerate(rows, start=1):
                writer.writerow(
                    [
                        idx,
                        row.get("normalized_uid"),
                        row.get("status"),
                        row.get("attempts"),
                        row.get("last_error_msg") or row.get("last_error_code", ""),
                        row.get("last_updated_at"),
                        row.get("next_attempt_after") or "",
                        row.get("last_evidence_path") or "",
                    ]
                )
        QMessageBox.information(self, "Export", f"Exported {len(rows)} rows to {path}")
