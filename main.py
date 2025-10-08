from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtWidgets import QApplication

from app.automations.fb_worker import build_worker
from app.config.loader import load_config
from app.message_provider import MessageProvider
from app.profile_manager import ProfileManager
from app.storage import Storage
from app.task_engine import EngineConfig, TaskEngine
from app.uid_management_gui import FBWebView, UidManagementWindow


def main() -> int:
    app = QApplication(sys.argv)
    config = load_config()
    storage = Storage(config.db_path, config.timezone)
    profile_row = storage.get_profile()
    profile_manager = ProfileManager(profile_row, config.timezone, storage)
    message_provider = MessageProvider(Path("messages.txt"))

    engine_config = EngineConfig(
        delay_between_uids_sec=config.delay_between_uids_sec,
        page_load_countdown_sec=config.page_load_countdown_sec,
        retry_max_attempts=config.retry_max_attempts,
        retry_backoff_sec=config.retry_backoff_sec,
        result_decrement_on=config.result_decrement_on,
    )

    web_view = FBWebView()
    worker_factory = lambda message: build_worker(web_view, message)
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
        web_view=web_view,
    )
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
