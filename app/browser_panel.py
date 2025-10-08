from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QEvent, QUrl
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QLineEdit, QToolBar, QVBoxLayout, QWidget, QTabWidget
from PyQt6.QtWebEngineCore import QWebEngineProfile
from PyQt6.QtWebEngineWidgets import QWebEnginePage, QWebEngineView


class BrowserTab(QWebEngineView):
    _FACEBOOK_HOSTS = {
        "facebook.com",
        "www.facebook.com",
        "m.facebook.com",
        "web.facebook.com",
        "messenger.com",
        "www.messenger.com",
    }

    def __init__(self, profile: QWebEngineProfile, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setPage(QWebEnginePage(profile, self))

    def event(self, event):  # type: ignore[override]
        if event.type() == QEvent.Type.ToolTip and self.url().host() in self._FACEBOOK_HOSTS:
            return True
        return super().event(event)


@dataclass
class ProfileConfig:
    storage_path: Path
    cache_path: Path
    profile_name: str


class BrowserPanel(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._profile: Optional[QWebEngineProfile] = None
        self._config: Optional[ProfileConfig] = None

        self._toolbar = QToolBar(self)
        self._toolbar.setMovable(False)

        self._url_bar = QLineEdit(self)
        self._url_bar.returnPressed.connect(self._navigate_from_bar)

        self._tabs = QTabWidget(self)
        self._tabs.setTabsClosable(False)
        self._tabs.currentChanged.connect(self._on_current_tab_changed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._toolbar)
        layout.addWidget(self._tabs, 1)

        self._build_toolbar()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_profile_storage(self, storage_path: Path) -> None:
        storage_path = Path(storage_path)
        storage_path.mkdir(parents=True, exist_ok=True)
        cache_path = storage_path / "cache"
        cache_path.mkdir(parents=True, exist_ok=True)
        profile_name = f"profile_{storage_path.name}"

        profile = QWebEngineProfile(profile_name, self)
        profile.setPersistentStoragePath(str(storage_path))
        profile.setCachePath(str(cache_path))
        profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
        profile.setHttpUserAgent(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )

        self._profile = profile
        self._config = ProfileConfig(storage_path=storage_path, cache_path=cache_path, profile_name=profile_name)
        self._reset_tabs()

    def current_view(self) -> Optional[QWebEngineView]:
        widget = self._tabs.currentWidget()
        if isinstance(widget, QWebEngineView):
            return widget
        return None

    def load_default(self) -> None:
        view = self.current_view()
        if view is None:
            view = self._add_tab()
        if view is not None:
            view.setUrl(QUrl("https://www.facebook.com"))

    def ensure_messenger_loaded(self) -> None:
        view = self.current_view()
        if view is None:
            view = self._add_tab()
        if view is None:
            return
        current = view.url().toString()
        if not current or current == "about:blank":
            view.setUrl(QUrl("https://www.facebook.com/messages"))

    def navigate_to(self, url: str) -> None:
        view = self.current_view()
        if view is None:
            view = self._add_tab()
        if view is not None:
            view.setUrl(QUrl(url))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _build_toolbar(self) -> None:
        back_action = QAction("⮜", self)
        back_action.triggered.connect(lambda: self._invoke_on_current(lambda view: view.back()))
        self._toolbar.addAction(back_action)

        forward_action = QAction("⮞", self)
        forward_action.triggered.connect(lambda: self._invoke_on_current(lambda view: view.forward()))
        self._toolbar.addAction(forward_action)

        reload_action = QAction("⟳", self)
        reload_action.triggered.connect(lambda: self._invoke_on_current(lambda view: view.reload()))
        self._toolbar.addAction(reload_action)

        home_action = QAction("⌂", self)
        home_action.triggered.connect(lambda: self.navigate_to("https://www.facebook.com"))
        self._toolbar.addAction(home_action)

        add_tab_action = QAction("+", self)
        add_tab_action.triggered.connect(self._add_tab)
        self._toolbar.addAction(add_tab_action)

        self._toolbar.addSeparator()
        self._toolbar.addWidget(self._url_bar)

    def _invoke_on_current(self, func) -> None:
        view = self.current_view()
        if view is not None:
            func(view)

    def _add_tab(self):
        if self._profile is None:
            return None
        view = BrowserTab(self._profile, self)
        index = self._tabs.addTab(view, "Loading…")
        self._tabs.setCurrentIndex(index)
        view.titleChanged.connect(lambda title, v=view: self._update_tab_title(v, title))
        view.urlChanged.connect(lambda url, v=view: self._on_url_changed(v, url))
        view.setUrl(QUrl("https://www.facebook.com"))
        return view

    def _reset_tabs(self) -> None:
        while self._tabs.count():
            widget = self._tabs.widget(0)
            self._tabs.removeTab(0)
            widget.deleteLater()
        self._add_tab()

    def _update_tab_title(self, view: QWebEngineView, title: str) -> None:
        index = self._tabs.indexOf(view)
        if index != -1:
            self._tabs.setTabText(index, title or "(Untitled)")

    def _on_url_changed(self, view: QWebEngineView, url: QUrl) -> None:
        if view is self.current_view():
            self._url_bar.setText(url.toString())
            self._url_bar.setCursorPosition(0)

    def _on_current_tab_changed(self, index: int) -> None:
        view = self.current_view()
        if view is not None:
            self._url_bar.setText(view.url().toString())
            self._url_bar.setCursorPosition(0)

    def _navigate_from_bar(self) -> None:
        text = self._url_bar.text().strip()
        if not text:
            return
        if "http" not in text:
            text = "https://" + text
        self.navigate_to(text)


__all__ = ["BrowserPanel", "BrowserTab"]
