from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtWidgets import QApplication

from app.automations.fb_worker import build_worker
from app.browser_window import BrowserWindow
from app.config.loader import load_config
from app.message_provider import MessageProvider
from app.profile_manager import ProfileManager
from app.storage import Storage
from app.task_engine import EngineConfig, TaskEngine
from app.uid_management_gui import UidManagementWindow


def main() -> int:
    app = QApplication(sys.argv)
    config = load_config()
    storage = Storage(config.db_path, config.timezone, config.daily_limit)
    profiles = storage.list_profiles()
    profile_row = profiles[0] if profiles else storage.create_profile("Profile 1", config.daily_limit)
    profile_manager = ProfileManager(
        storage=storage,
        timezone=config.timezone,
        profile_data_dir=config.profile_data_dir,
        profile_row=profile_row,
    )
    message_provider = MessageProvider(Path("messages.txt"))

    engine_config = EngineConfig(
        delay_between_uids_sec=config.delay_between_uids_sec,
        page_load_countdown_sec=config.page_load_countdown_sec,
        retry_max_attempts=config.retry_max_attempts,
        retry_backoff_sec=config.retry_backoff_sec,
        result_decrement_on=config.result_decrement_on,
    )

    browser_window = BrowserWindow()
    browser_window.set_profile_storage(profile_manager.profile_storage_path)
    browser_window.ensure_messages_tab()
    worker_factory = lambda: build_worker(browser_window.current_view())
    task_engine = TaskEngine(
        storage=storage,
        profile_manager=profile_manager,
        message_supplier=message_provider.next_message,
        worker_factory=worker_factory,
        config=engine_config,
    )

    window = UidManagementWindow(
        storage=storage,
        profile_manager=profile_manager,
        task_engine=task_engine,
        engine_config=engine_config,
        browser_window=browser_window,
    )
    window.show()
    browser_window.show_window()
    browser_window.ensure_messages_tab()
    window.raise_()
    window.activateWindow()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
