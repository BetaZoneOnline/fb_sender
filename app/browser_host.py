from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QObject

from automation import MainWindow


class BrowserHost(QObject):
    """Manage the standalone browser window per active profile."""

    def __init__(self) -> None:
        super().__init__()
        self._window: Optional[MainWindow] = None
        self._current_path: Optional[Path] = None

    def ensure_window(self, storage_path: Path) -> MainWindow:
        """Ensure a browser window exists for the given storage path."""
        storage_path = Path(storage_path)
        storage_path.mkdir(parents=True, exist_ok=True)

        if self._window is not None:
            if self._current_path == storage_path:
                if not self._window.isVisible():
                    self._window.show()
                self._window.raise_()
                self._window.activateWindow()
                return self._window

            self._teardown_current_window()

        self._window = MainWindow(profile_dir=storage_path)
        self._current_path = storage_path
        self._window.destroyed.connect(self._handle_destroyed)
        self._window.show()
        self._window.raise_()
        self._window.activateWindow()
        return self._window

    def ensure_view(self, storage_path: Path):
        """Ensure the window exists and return its active QWebEngineView."""
        window = self.ensure_window(storage_path)
        return window.current_browser()

    def focus(self) -> None:
        if self._window is None:
            return
        if not self._window.isVisible():
            self._window.show()
        self._window.raise_()
        self._window.activateWindow()

    def _teardown_current_window(self) -> None:
        if self._window is None:
            return
        try:
            self._window.destroyed.disconnect(self._handle_destroyed)
        except Exception:
            pass
        self._window.close()
        self._window.deleteLater()
        self._window = None
        self._current_path = None

    def _handle_destroyed(self) -> None:
        self._window = None
        self._current_path = None


__all__ = ["BrowserHost"]
