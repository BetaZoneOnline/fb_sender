from __future__ import annotations

import os

from PyQt6.QtCore import QEvent, QUrl
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QLineEdit, QMainWindow, QTabWidget, QToolBar


class FBWebView(QWebEngineView):
    """Custom WebEngine view that suppresses Facebook tooltips."""

    def event(self, event):  # type: ignore[override]
        if event.type() == QEvent.Type.ToolTip:
            if self.is_facebook_host(self.url().host()):
                return True
        return super().event(event)

    @staticmethod
    def is_facebook_host(host: str) -> bool:
        facebook_hosts = {
            "facebook.com",
            "www.facebook.com",
            "m.facebook.com",
            "web.facebook.com",
            "messenger.com",
            "www.messenger.com",
        }
        return host in facebook_hosts


class BrowserWindow(QMainWindow):
    """Standalone browser window backed by a persistent profile."""

    def __init__(self, base_dir: str):
        super().__init__()

        self.setWindowTitle("PyBro Messenger Browser")
        self.setWindowIcon(QIcon(os.path.join(base_dir, "icon.png")))

        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(False)
        self.setCentralWidget(self.tabs)

        self.profile_path = os.path.join(base_dir, "profile_data")
        os.makedirs(self.profile_path, exist_ok=True)

        self.profile = QWebEngineProfile("persistent_profile", self)
        self.profile.setPersistentStoragePath(self.profile_path)
        self.profile.setCachePath(self.profile_path)

        self.profile.setHttpUserAgent(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )

        self.add_tab()
        self._build_toolbar()

    # ------------------------------------------------------------------
    def _build_toolbar(self) -> None:
        toolbar = QToolBar()
        self.addToolBar(toolbar)

        back_btn = QAction("⮜", self)
        back_btn.triggered.connect(lambda: self.current_browser().back())
        toolbar.addAction(back_btn)

        forward_btn = QAction("⮞", self)
        forward_btn.triggered.connect(lambda: self.current_browser().forward())
        toolbar.addAction(forward_btn)

        reload_btn = QAction("⟳", self)
        reload_btn.triggered.connect(lambda: self.current_browser().reload())
        toolbar.addAction(reload_btn)

        home_btn = QAction("⌂", self)
        home_btn.triggered.connect(self.navigate_home)
        toolbar.addAction(home_btn)

        add_tab_btn = QAction("+", self)
        add_tab_btn.triggered.connect(self.add_tab)
        toolbar.addAction(add_tab_btn)

        self.url_bar = QLineEdit()
        self.url_bar.returnPressed.connect(self.navigate_to_url)
        toolbar.addWidget(self.url_bar)
        self.url_bar.setStyleSheet("width: 50%;")
        self.current_browser().urlChanged.connect(self.update_url)

    # ------------------------------------------------------------------
    def add_tab(self) -> None:
        browser = FBWebView()
        browser.setPage(QWebEnginePage(self.profile, browser))
        browser.setUrl(QUrl("https://www.facebook.com"))
        self.tabs.addTab(browser, "facebook")
        self.tabs.setCurrentWidget(browser)
        self.tabs.setTabText(self.tabs.currentIndex(), "Loading...")
        browser.titleChanged.connect(
            lambda title, b=browser: self.tabs.setTabText(self.tabs.indexOf(b), title)
        )
        browser.urlChanged.connect(
            lambda url, b=browser: self.update_url(url) if self.tabs.currentWidget() == b else None
        )

    def current_browser(self) -> QWebEngineView:
        return self.tabs.currentWidget()  # type: ignore[return-value]

    def navigate_home(self) -> None:
        self.current_browser().setUrl(QUrl("https://www.google.com"))

    def navigate_to_url(self) -> None:
        url = self.url_bar.text()
        if not url:
            return
        if "http" not in url:
            url = "https://" + url
        self.current_browser().setUrl(QUrl(url))

    def update_url(self, url: QUrl) -> None:
        if self.sender() == self.current_browser():
            self.url_bar.setText(url.toString())
            self.url_bar.setCursorPosition(0)

    # Convenience helpers ----------------------------------------------
    def open_chat(self, uid: str) -> None:
        url = QUrl(f"https://www.facebook.com/messages/t/{uid}")
        self.current_browser().setUrl(url)

    def grab_current_frame(self):
        return self.current_browser().grab()

    # Qt overrides -----------------------------------------------------
    def closeEvent(self, event):  # type: ignore[override]
        for i in range(self.tabs.count()):
            browser = self.tabs.widget(i)
            if hasattr(browser, "url") and browser.url().host() == "www.youtube.com":
                browser.page().runJavaScript("document.getElementsByTagName('video')[0].pause();")
            video_widget = browser.findChild(QVideoWidget)
            if video_widget:
                video_widget.player().stop()
        event.accept()
