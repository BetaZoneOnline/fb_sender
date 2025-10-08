# -*- coding: utf-8 -*-
"""
PyBro Messenger Automation — FULL SINGLE FILE
- Modernized QtWebEngine browser (Chrome 138 UA, GPU flags, persistent cookies)
- Facebook-only tooltip killer (blocks the "Close" bubble) at the Qt event level
- Optional FB overlay/close-button removers via injected JS/CSS
- Safe fallback stub for `automation.create_automation` if automation.py is missing

Run:
    pip install --upgrade PyQt6 PyQt6-WebEngine
    python this_file.py
"""

import os, sys, json, random
from datetime import date

# ---------- Chromium flags (set BEFORE creating QApplication) ----------
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", " ".join([
    "--enable-gpu-rasterization",
    "--enable-zero-copy",
    "--ignore-gpu-blocklist",
    "--enable-accelerated-video-decode",
    "--enable-accelerated-2d-canvas",
    "--enable-features=VaapiVideoDecoder,CanvasOopRasterization,NetworkServiceInProcess2,AcceptCHFrame,MediaSessionService",
    "--autoplay-policy=no-user-gesture-required",
    "--enable-quic",
    "--disable-features=AudioServiceOutOfProcess",
]))
if os.name == "nt":
    # Helps on some Windows setups while developing; remove if not needed.
    os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

from PyQt6.QtCore import *
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import *
from PyQt6.QtWebEngineCore import (
    QWebEngineProfile, QWebEnginePage, QWebEngineScript, QWebEngineSettings
)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtMultimediaWidgets import QVideoWidget

# ---------- Optional automation module (safe stub if missing) ----------
try:
    from automation import create_automation  # your real module, if present
except Exception as e:
    print(f"[WARN] automation module not available ({e}). Using NO-ACTION demo stub.")
    def create_automation(_view):
        class NoOpAutomation:
            is_demo = True  # marker so we can stop after first run
            def set_message(self, *args, **kwargs):
                pass
            def automate_messaging(self, message=None, delay=0, callback=lambda *_: None):
                print(f"[DEMO] Skipping real automation. Would have sent: {message!r}")
                # Pretend success so upstream doesn't retry with delays.
                QTimer.singleShot(0, lambda: callback(True))
        return NoOpAutomation()

# ---------- Utilities ----------
def is_facebook_host(host: str) -> bool:
    if not host:
        return False
    host = host.lower()
    return host == "facebook.com" or host.endswith(".facebook.com")

# ---------- HARD FIX: swallow tooltip events on Facebook ----------
class FBWebView(QWebEngineView):
    def event(self, e):
        if e.type() == QEvent.Type.ToolTip:
            try:
                if is_facebook_host(self.url().host()):
                    return True  # block tooltip (e.g., "Close") on FB only
            except Exception:
                pass
        return super().event(e)

# ---------- Tight page: block noisy popups/dialogs ----------
class TightPage(QWebEnginePage):
    def createWindow(self, _type):
        return None  # block window.open popups
    def javaScriptAlert(self, url, msg):
        pass
    def javaScriptConfirm(self, url, msg):
        return False
    def javaScriptPrompt(self, url, msg, default):
        return False, ""

# ---------- Facebook-only injected scripts (backup to the hard fix) ----------
FB_HOST_CHECK_JS = r"""(function(){ try { return /(^|\.)facebook\.com$/i.test(location.hostname); }catch(e){return false;} })();"""

HIDE_OVERLAYS_CSS_JS = r"""
(function(){ if (!(%s)) return;
  const s=document.createElement('style');
  s.textContent=`
    [role="dialog"],[role="alertdialog"],[aria-modal="true"],
    div[style*="position: fixed"][style*="z-index"]
    { display:none!important; visibility:hidden!important; pointer-events:none!important; }
  `;
  document.documentElement.appendChild(s);
})();""" % FB_HOST_CHECK_JS

REMOVE_CLOSE_TOOLTIPS_JS = r"""
(function(){ if (!(%s)) return;
  function strip(root){
    root.querySelectorAll('[title="Close"],[aria-label="Close"],[data-testid*="close"]').forEach(el=>{
      if (el.getAttribute('title')==='Close') el.removeAttribute('title');
      if (el.getAttribute('aria-label')==='Close') el.setAttribute('aria-label','');
    });
  }
  strip(document);
  const mo=new MutationObserver(ms=>{
    for(const m of ms){
      if(m.type==='childList'){ m.addedNodes.forEach(n=>{ if(n&&n.nodeType===1) strip(n); }); }
      if(m.type==='attributes'){
        const t=m.target; if(!t||t.nodeType!==1) continue;
        if(m.attributeName==='title' && t.getAttribute('title')==='Close') t.removeAttribute('title');
        if(m.attributeName==='aria-label' && t.getAttribute('aria-label')==='Close') t.setAttribute('aria-label','');
      }
    }
  });
  mo.observe(document.documentElement,{subtree:true,childList:true,attributes:true,attributeFilter:['title','aria-label']});
})();""" % FB_HOST_CHECK_JS

HIDE_CLOSE_BUTTONS_JS = r"""
(function(){ if (!(%s)) return;
  const s=document.createElement('style');
  s.textContent=`[aria-label="Close"],[title="Close"],[data-testid*="close"]{display:none!important;visibility:hidden!important;pointer-events:none!important;}`;
  document.documentElement.appendChild(s);
})();""" % FB_HOST_CHECK_JS

# ---------- Main Browser Window ----------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PyBro Messenger Automation")
        self.setWindowIcon(QIcon("icon.png"))

        # Tabs
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(False)  # no Qt tab close button (and its tooltip)
        self.setCentralWidget(self.tabs)

        # Persistent profile setup
        self.profile_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "profile_data")
        os.makedirs(self.profile_path, exist_ok=True)
        self.profile = QWebEngineProfile("persistent_profile", self)
        self.profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.AllowPersistentCookies)
        self.profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)
        self.profile.setPersistentStoragePath(self.profile_path)
        self.profile.setCachePath(self.profile_path)
        self.profile.setHttpCacheMaximumSize(512 * 1024 * 1024)  # 512MB cache

        # Modern UA & language
        self.profile.setHttpUserAgent(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
        )
        self.profile.setHttpAcceptLanguage("en-US,en;q=0.9")

        # Allow 3rd-party cookies if available (helps with FB flows)
        try:
            self.profile.setThirdPartyCookiePolicy(QWebEngineProfile.ThirdPartyCookiePolicy.AlwaysAllowThirdPartyCookies)
        except Exception:
            pass

        # Global feature settings
        s = self.profile.settings()
        for attr in [
            QWebEngineSettings.WebAttribute.JavascriptEnabled,
            QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows,
            QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard,
            QWebEngineSettings.WebAttribute.LocalStorageEnabled,
            QWebEngineSettings.WebAttribute.Accelerated2dCanvasEnabled,
            QWebEngineSettings.WebAttribute.WebGLEnabled,
            QWebEngineSettings.WebAttribute.AutoLoadImages,
            QWebEngineSettings.WebAttribute.PluginsEnabled,
            QWebEngineSettings.WebAttribute.FullScreenSupportEnabled,
            QWebEngineSettings.WebAttribute.ScreenCaptureEnabled,
            QWebEngineSettings.WebAttribute.SpatialNavigationEnabled,
            QWebEngineSettings.WebAttribute.TouchIconsEnabled,
            QWebEngineSettings.WebAttribute.FocusOnNavigationEnabled,
        ]:
            s.setAttribute(attr, True)
        # Autoplay media without gesture
        s.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)

        # Install FB-only helper scripts (backup; main tooltip block is in FBWebView.event)
        self._install_fb_scripts()

        # Toolbar
        tb = QToolBar(); self.addToolBar(tb)
        act_back = QAction("⮜", self); act_back.triggered.connect(lambda: self.current_browser().back()); tb.addAction(act_back)
        act_fwd = QAction("⮞", self); act_fwd.triggered.connect(lambda: self.current_browser().forward()); tb.addAction(act_fwd)
        act_reload = QAction("⟳", self); act_reload.triggered.connect(lambda: self.current_browser().reload()); tb.addAction(act_reload)
        act_home = QAction("⌂", self); act_home.triggered.connect(self.navigate_home); tb.addAction(act_home)
        act_new = QAction("+", self); act_new.triggered.connect(self.add_tab); tb.addAction(act_new)

        self.url_bar = QLineEdit(); self.url_bar.returnPressed.connect(self.navigate_to_url)
        tb.addWidget(self.url_bar); self.url_bar.setStyleSheet("width: 50%;")

        # First tab
        self.add_tab()

    def _install_fb_scripts(self):
        def insert(name, source):
            scr = QWebEngineScript()
            scr.setName(name)
            scr.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
            scr.setRunsOnSubFrames(True)
            scr.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
            scr.setSourceCode(source)
            self.profile.scripts().insert(scr)
        insert("FB_HideOverlays", HIDE_OVERLAYS_CSS_JS)
        insert("FB_RemoveCloseTooltips", REMOVE_CLOSE_TOOLTIPS_JS)
        insert("FB_HideCloseButtons", HIDE_CLOSE_BUTTONS_JS)

    def _wire_feature_permissions(self, page: QWebEnginePage):
        def on_perm(origin, feature):
            host = origin.host()
            allow = is_facebook_host(host)  # grant FB access to mic/cam/notify/geo/clipboard
            policy = (QWebEnginePage.PermissionPolicy.PermissionGrantedByUser
                      if allow else QWebEnginePage.PermissionPolicy.PermissionDeniedByUser)
            page.setFeaturePermission(origin, feature, policy)
        page.featurePermissionRequested.connect(on_perm)

    def add_tab(self):
        view = FBWebView()
        page = TightPage(self.profile, view)
        view.setPage(page)
        self._wire_feature_permissions(page)

        page.settings().setAttribute(QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, True)
        page.settings().setAttribute(QWebEngineSettings.WebAttribute.ScreenCaptureEnabled, True)

        view.setUrl(QUrl("https://www.facebook.com"))
        self.tabs.addTab(view, "Facebook")
        self.tabs.setCurrentWidget(view)
        self.tabs.setTabText(self.tabs.currentIndex(), "Loading...")
        view.titleChanged.connect(lambda t, v=view: self.tabs.setTabText(self.tabs.indexOf(v), t))
        view.urlChanged.connect(lambda url, v=view: self._sync_urlbar(url, v))

    def _sync_urlbar(self, url: QUrl, view: QWebEngineView):
        if self.tabs.currentWidget() == view:
            self.url_bar.setText(url.toString())
            self.url_bar.setCursorPosition(0)

    def current_browser(self) -> QWebEngineView:
        return self.tabs.currentWidget()

    def navigate_home(self):
        self.current_browser().setUrl(QUrl("https://www.google.com"))

    def navigate_to_url(self):
        url = self.url_bar.text().strip()
        if not url:
            return
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        self.current_browser().setUrl(QUrl(url))

    def closeEvent(self, event):
        for i in range(self.tabs.count()):
            browser = self.tabs.widget(i)
            vw = browser.findChild(QVideoWidget)
            if vw:
                vw.player().stop()
        event.accept()

# ---------- Automation wrapper ----------
class MessengerAutomation:
    def __init__(self):
        self.load_config(); self.load_stats()
        self.current_uid = None; self.current_message = None
        self.messages_sent_in_session = 0
        self.uids = self._read_lines("uids.txt")
        self.messages = self._read_lines("messages.txt")
        if not self.uids:
            print("Error: No UIDs found in uids.txt"); # don't exit; allow browser-only run
        if not self.messages:
            print("Error: No messages found in messages.txt");
        self.window = MainWindow()
        self.automation = None

    def load_config(self):
        self.config = {
            "DELAY_BETWEEN_MESSAGES": 60,
            "MAX_MESSAGES_PER_DAY": 10,
            "MESSAGE_RETRY_ATTEMPTS": 3,
            "MESSAGE_RETRY_DELAY": 10,
            "PAGE_LOAD_WAIT_TIME": 10,
            "RETRY_DELAY_AFTER_FAILURE": 15
        }

    def load_stats(self):
        self.stats_file = "stats.json"
        try:
            with open(self.stats_file, "r") as f:
                self.stats = json.load(f)
        except FileNotFoundError:
            self.stats = {}
        today = date.today().isoformat()
        if self.stats.get("last_reset_date") != today:
            self.stats["last_reset_date"] = today
            self.stats["daily_messages_sent"] = 0
            with open(self.stats_file, "w") as f: json.dump(self.stats, f, indent=4)

    def _read_lines(self, fp):
        try:
            with open(fp, "r", encoding="utf-8") as f:
                return [ln.strip() for ln in f if ln.strip()]
        except Exception:
            return []

    def can_send(self):
        return self.stats.get("daily_messages_sent", 0) < self.config["MAX_MESSAGES_PER_DAY"]

    def select_uid_msg(self):
        if not self.uids or not self.messages:
            return None, None
        self.current_uid = random.choice(self.uids)
        self.current_message = random.choice(self.messages)
        return self.current_uid, self.current_message

    def start_automation(self):
        uid, message = self.select_uid_msg()
        if not uid or not message:
            print("[INFO] No UID/message lists. Running browser only.")
            return
        if not self.can_send():
            print("Daily limit reached.")
            return

        self.automation = create_automation(self.window.current_browser())
        self.automation.set_message(message)
        self.window.current_browser().setUrl(QUrl(f"https://www.facebook.com/messages/t/{uid}"))
        self.window.current_browser().loadFinished.connect(self._after_load)

    def _after_load(self, ok):
        if not ok: return
        click = "(function(){try{const b=document.body; if(b&&b.click) b.click();}catch(e){}})();"
        self.window.current_browser().page().runJavaScript(click)
        QTimer.singleShot(1000, lambda: QTimer.singleShot(self.config["PAGE_LOAD_WAIT_TIME"]*1000, self._send))

    def _send(self):
        if self.automation:
            self.automation.automate_messaging(
                message=self.current_message,
                delay=self.config["MESSAGE_RETRY_DELAY"],
                callback=self._sent_cb
            )

    def _sent_cb(self, ok):
        if getattr(self.automation, "is_demo", False):
            print("[DEMO] Automation stub finished once. Not looping further.")
            return
        if ok:
            self.stats["daily_messages_sent"] = self.stats.get("daily_messages_sent", 0) + 1
            with open(self.stats_file, "w") as f: json.dump(self.stats, f, indent=4)
            if self.can_send():
                QTimer.singleShot(self.config["DELAY_BETWEEN_MESSAGES"]*1000, self.start_automation)
        else:
            QTimer.singleShot(self.config["RETRY_DELAY_AFTER_FAILURE"]*1000, self.start_automation)

    def run(self):
        self.window.showMaximized()
        # Kick off automation after initial UI shows (if lists exist)
        QTimer.singleShot(1500, self.start_automation)
        return self.window

# ---------- Entry ----------
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("PyBro Messenger Automation")
    app.setApplicationDisplayName("PyBro Messenger Automation")
    app.setOrganizationName("PyBro")

    # Print versions to confirm runtime
    from PyQt6.QtCore import QT_VERSION_STR
    print("Qt version:", QT_VERSION_STR)

    automation = MessengerAutomation()
    window = automation.run()
    sys.exit(app.exec())
