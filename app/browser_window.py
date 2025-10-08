from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QEvent, QUrl
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import QLineEdit, QMainWindow, QTabWidget, QToolBar
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PyQt6.QtWebEngineWidgets import QWebEngineView


class FBWebView(QWebEngineView):
    FACEBOOK_HOSTS = {
        "facebook.com",
        "www.facebook.com",
        "m.facebook.com",
        "web.facebook.com",
        "messenger.com",
        "www.messenger.com",
    }

    def event(self, event):  # type: ignore[override]
        if event.type() == QEvent.Type.ToolTip and self.url().host() in self.FACEBOOK_HOSTS:
            return True
        return super().event(event)


class BrowserWindow(QMainWindow):
    """Standalone browser window that mirrors the legacy Messenger shell."""

    DEFAULT_URL = QUrl("https://www.facebook.com/messages")
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("PyBro Messenger Automation")
        self.setWindowIcon(QIcon("icon.png"))

        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(False)
        self.setCentralWidget(self.tabs)

        self._profile: Optional[QWebEngineProfile] = None
        self._profile_storage: Optional[Path] = None

        self._toolbar = QToolBar()
        self.addToolBar(self._toolbar)
        self._build_toolbar()

        self.url_bar = QLineEdit()
        self.url_bar.returnPressed.connect(self._navigate_to_url)
        self.url_bar.setPlaceholderText("Enter URL or search term")
        self._toolbar.addWidget(self.url_bar)
        self.url_bar.setStyleSheet("width: 50%;")

        self.tabs.currentChanged.connect(self._sync_url_bar)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_profile_storage(self, storage_path: Path) -> None:
        """Configure persistent storage/cache for the browser profile."""
        storage_path = Path(storage_path)
        if self._profile_storage == storage_path:
            return

        storage_path.mkdir(parents=True, exist_ok=True)
        cache_path = storage_path / "cache"
        cache_path.mkdir(parents=True, exist_ok=True)

        profile_name = f"profile_{storage_path.name}"
        profile = QWebEngineProfile(profile_name, self)
        profile.setPersistentStoragePath(str(storage_path))
        profile.setCachePath(str(cache_path))
        profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
        )
        profile.setHttpUserAgent(self.USER_AGENT)

        self._profile = profile
        self._profile_storage = storage_path
        self._rebuild_tabs()

    def current_view(self) -> FBWebView:
        """Return the active QWebEngineView used for automation."""
        if self.tabs.count() == 0:
            self._add_tab(self.DEFAULT_URL)
        view = self.tabs.currentWidget()
        if not isinstance(view, FBWebView):  # pragma: no cover - defensive
            raise RuntimeError("Active tab is not a FBWebView")
        return view

    def show_window(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def load_default(self) -> None:
        view = self.current_view()
        view.setUrl(self.DEFAULT_URL)

    def ensure_messages_tab(self) -> None:
        view = self.current_view()
        current_url = view.url().toString()
        if not current_url or current_url == "about:blank":
            view.setUrl(self.DEFAULT_URL)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _build_toolbar(self) -> None:
        back_btn = QAction("⮜", self)
        back_btn.triggered.connect(lambda: self.current_view().back())
        self._toolbar.addAction(back_btn)

        forward_btn = QAction("⮞", self)
        forward_btn.triggered.connect(lambda: self.current_view().forward())
        self._toolbar.addAction(forward_btn)

        reload_btn = QAction("⟳", self)
        reload_btn.triggered.connect(lambda: self.current_view().reload())
        self._toolbar.addAction(reload_btn)

        home_btn = QAction("⌂", self)
        home_btn.triggered.connect(lambda: self.current_view().setUrl(QUrl("https://www.google.com")))
        self._toolbar.addAction(home_btn)

        add_tab_btn = QAction("+", self)
        add_tab_btn.triggered.connect(lambda: self._add_tab(QUrl("https://www.facebook.com")))
        self._toolbar.addAction(add_tab_btn)

    def _add_tab(self, url: QUrl) -> FBWebView:
        if self._profile is None:
            raise RuntimeError("Browser profile not configured")
        browser = FBWebView()
        browser.setPage(QWebEnginePage(self._profile, browser))
        browser.setUrl(url)
        index = self.tabs.addTab(browser, "Loading...")
        self.tabs.setCurrentIndex(index)
        browser.titleChanged.connect(
            lambda title, b=browser: self._set_tab_title(b, title)
        )
        browser.urlChanged.connect(
            lambda qurl, b=browser: self._update_url_bar(b, qurl)
        )
        return browser

    def _rebuild_tabs(self) -> None:
        while self.tabs.count():
            widget = self.tabs.widget(0)
            self.tabs.removeTab(0)
            widget.deleteLater()
        self._add_tab(self.DEFAULT_URL)

    def _set_tab_title(self, view: FBWebView, title: str) -> None:
        index = self.tabs.indexOf(view)
        if index >= 0:
            self.tabs.setTabText(index, title or "(Untitled)")

    def _update_url_bar(self, view: FBWebView, url: QUrl) -> None:
        if view is self.tabs.currentWidget():
            self.url_bar.setText(url.toString())
            self.url_bar.setCursorPosition(0)

    def _navigate_to_url(self) -> None:
        url = self.url_bar.text().strip()
        if not url:
            return
        if "://" not in url:
            url = "https://" + url
        self.current_view().setUrl(QUrl(url))

    def _sync_url_bar(self, index: int) -> None:
        if index < 0:
            return
        widget = self.tabs.widget(index)
        if isinstance(widget, FBWebView):
            self.url_bar.setText(widget.url().toString())
            self.url_bar.setCursorPosition(0)


__all__ = ["BrowserWindow", "FBWebView"]
