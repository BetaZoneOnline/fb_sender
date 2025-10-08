from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtWidgets import QApplication

from app.browser_window import BrowserWindow
from app.profile_manager import ProfileManager
from app.automations.fb_worker import FBWorker
from app.task_engine import TaskEngine
from app.uid_management_gui import UIDManagementWindow


def main() -> int:
    base_dir = Path(__file__).resolve().parent
    app = QApplication(sys.argv)
    app.setApplicationName("PyBro UID Controller")
    app.setOrganizationName("PyBro")

    profile_manager = ProfileManager(base_dir)
    browser_window = BrowserWindow(str(base_dir))
    worker = FBWorker(browser_window, profile_manager)
    engine = TaskEngine(profile_manager, worker)

    window = UIDManagementWindow(profile_manager, engine, browser_window)
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
