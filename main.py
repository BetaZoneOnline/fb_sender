import sys
import os
import random
from urllib.parse import urlparse, parse_qs
from PyQt6.QtCore import *
from PyQt6.QtWidgets import *
from PyQt6.QtWebEngineWidgets import *
from PyQt6.QtWebEngineCore import QWebEngineProfile, QWebEnginePage
from PyQt6.QtGui import QIcon, QAction
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtCore import QEvent

from uid_manager import UIDTracker


class FBWebView(QWebEngineView):
    def event(self, e):
        if e.type() == QEvent.Type.ToolTip:
            if self.is_facebook_host(self.url().host()):
                return True   # eat the tooltip event on Facebook
        return super().event(e)
    
    def is_facebook_host(self, host):
        """Check if the current host is Facebook or related domains"""
        facebook_hosts = [
            'facebook.com',
            'www.facebook.com',
            'm.facebook.com',
            'web.facebook.com',
            'messenger.com',
            'www.messenger.com'
        ]
        return host in facebook_hosts

# Import automation module
from automation import create_automation


class MessengerAutomation:
    def __init__(self):
        self.load_config()
        self.load_messages()
        self.load_uids()
        self.tracker_file = 'uid_tracker.json'
        self.uid_tracker = UIDTracker(self.all_uids, self.tracker_file, self.config['MAX_MESSAGES_PER_DAY'])
        self._print_tracker_status()

        # Create main window
        self.window = MainWindow()
        self.automation = None
        
        # Current state
        self.current_uid = None
        self.current_message = None
        self.current_uid_status = None  # 'sent', 'error', 'attempting'
        self.current_uid_attempts = 0  # Track attempts per UID
        self._retry_same_uid = False
        self._automation_active = False
        self._pending_action = None
        self._deferred_action_timer = QTimer()
        self._deferred_action_timer.setSingleShot(True)
        self._deferred_action_timer.timeout.connect(self._run_pending_action)
        self.non_retryable_failure_reasons = (
            'Message input box not found',
            'Composer not found',
            'Composer not found with stable selectors'
        )
        
    def load_config(self):
        """Load configuration from .env file"""
        self.config = {
            'DELAY_BETWEEN_MESSAGES': 60,
            'MAX_MESSAGES_PER_DAY': 10,
            'MESSAGE_RETRY_ATTEMPTS': 3,
            'MESSAGE_RETRY_DELAY': 10,
            'PAGE_LOAD_WAIT_TIME': 10,
            'RETRY_DELAY_AFTER_FAILURE': 15
        }
        
        try:
            with open('.env', 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip()
                        
                        if key in self.config:
                            # Convert to appropriate type
                            if key in ['DELAY_BETWEEN_MESSAGES', 'MAX_MESSAGES_PER_DAY', 
                                     'MESSAGE_RETRY_ATTEMPTS', 'PAGE_LOAD_WAIT_TIME',
                                     'RETRY_DELAY_AFTER_FAILURE']:
                                self.config[key] = int(value)
                            elif key == 'MESSAGE_RETRY_DELAY':
                                self.config[key] = int(value)
        except FileNotFoundError:
            print("Warning: .env file not found, using default configuration")
        except Exception as e:
            print(f"Error loading config: {e}")
            
        print("Configuration loaded:", self.config)
    
    def load_uids(self):
        """Load, normalize, and de-duplicate UIDs from uids.txt"""
        raw_lines = []
        try:
            with open('uids.txt', 'r', encoding='utf-8') as f:
                raw_lines = [line.strip() for line in f if line.strip()]
        except FileNotFoundError:
            print("Error: uids.txt not found")
            sys.exit(1)
        except Exception as e:
            print(f"Error reading uids.txt: {e}")
            sys.exit(1)

        unique_uids = []
        duplicates = []
        invalid = []
        seen = set()

        for raw in raw_lines:
            if raw.startswith('#'):
                continue

            normalized = self._normalize_uid(raw)
            if not normalized:
                invalid.append(raw)
                continue

            if normalized in seen:
                duplicates.append(normalized)
                continue

            seen.add(normalized)
            unique_uids.append(normalized)

        if not unique_uids:
            print("Error: No valid UIDs found after normalization")
            sys.exit(1)

        self.all_uids = unique_uids
        self.uid_import_report = {
            'total_lines': len(raw_lines),
            'unique': len(unique_uids),
            'duplicates_skipped': len(duplicates),
            'invalid_entries': len(invalid),
        }

        print(f"Loaded {len(unique_uids)} unique UIDs from uids.txt")
        if duplicates:
            print(f"Skipped {len(duplicates)} duplicate UID entries")
        if invalid:
            print(f"Ignored {len(invalid)} invalid UID entries")

    def load_messages(self):
        """Load messages from messages.txt"""
        try:
            with open('messages.txt', 'r', encoding='utf-8') as f:
                self.messages = [line.strip() for line in f if line.strip()]
            
            if not self.messages:
                print("Error: No messages found in messages.txt")
                sys.exit(1)
                
            print(f"Loaded {len(self.messages)} messages from messages.txt")
            
        except FileNotFoundError:
            print("Error: messages.txt not found")
            sys.exit(1)
        except Exception as e:
            print(f"Error reading messages.txt: {e}")
            sys.exit(1)
    
    def get_available_uids(self):
        """Get list of UIDs that haven't been used yet"""
        return self.uid_tracker.available_uids()

    def can_send_more_today(self):
        """Check if we can send more messages today"""
        if not self.uid_tracker.can_send_more_today():
            remaining = self.uid_tracker.remaining_quota()
            sent = self.uid_tracker.today_summary().get('successful_sends', 0)
            if remaining <= 0:
                print(f"Daily limit reached: {sent}/{self.config['MAX_MESSAGES_PER_DAY']}")
            else:
                print("No more available UIDs to try")
            return False

        return True
    
    def select_next_uid_and_message(self):
        """Select next available UID and random message - process in file order"""
        next_uid = self.uid_tracker.next_uid()

        if not next_uid:
            print("No available UIDs left")
            return None, None

        self.current_uid = next_uid
        self.current_message = random.choice(self.messages)
        self.current_uid_status = 'attempting'

        remaining_after = max(0, len(self.uid_tracker.available_uids()) - 1)
        print(f"Selected UID: {self.current_uid} (in file order)")
        print(f"Selected message: {self.current_message}")
        print(f"Available UIDs remaining after this: {remaining_after}")

        return self.current_uid, self.current_message
    
    def record_uid_attempt(self, success, error_reason=None):
        """Record UID attempt result"""
        attempts = max(1, self.current_uid_attempts)
        self.uid_tracker.complete_uid(self.current_uid, success, attempts, error_reason)

        self.current_uid_status = 'sent' if success else 'error'
        if success:
            print(f"✅ UID {self.current_uid} - Message sent successfully")
        else:
            error_msg = f" - {error_reason}" if error_reason else ""
            print(f"❌ UID {self.current_uid} - Failed{error_msg}")

        today_stats = self.uid_tracker.today_summary()
        print(f"Progress: {today_stats['successful_sends']} sent, {today_stats['errors']} errors, {today_stats['total_attempted']} attempted")
        print(f"Available UIDs remaining: {len(self.get_available_uids())}")
    
    def start_automation(self):
        """Start the automation process"""
        if self._automation_active:
            print("Automation already running, skipping new start request")
            return

        retrying = False
        if self._retry_same_uid and self.current_uid:
            retrying = True
            uid = self.current_uid
            message = self.current_message
            print(f"Retrying UID {uid} (attempt {self.current_uid_attempts + 1})")
        else:
            if not self.can_send_more_today():
                print("Cannot send more messages today. Exiting.")
                return

            uid, message = self.select_next_uid_and_message()

            if not uid:
                print("No UIDs available to process")
                return

            self.current_uid_attempts = 0

        self._retry_same_uid = False
        self._automation_active = True

        # Disconnect any previous loadFinished connections to prevent stacking
        browser = self.window.current_browser()
        try:
            browser.loadFinished.disconnect(self.on_page_loaded)
        except (TypeError, RuntimeError):
            pass

        # Set up automation
        self.automation = create_automation(browser)
        self.automation.set_message(message)

        # Navigate to the selected UID with proper timing
        url = f'https://www.facebook.com/messages/t/{uid}'
        action_desc = "Retry navigation" if retrying else "Navigating to"
        print(f"{action_desc}: {url}")

        def _perform_navigation():
            browser.setUrl(QUrl(url))

        QTimer.singleShot(500, _perform_navigation)

        # Start automation after page loads (single connection)
        browser.loadFinished.connect(self.on_page_loaded, Qt.ConnectionType.UniqueConnection)
    
    def on_page_loaded(self, success):
        """Callback when page is loaded"""
        if success:
            print(f"Page loaded successfully, waiting {self.config['PAGE_LOAD_WAIT_TIME']} seconds for full load...")
            # Wait for page to fully load, then start automation
            self._schedule_action(self.config['PAGE_LOAD_WAIT_TIME'], self.start_message_automation, "begin message automation")
        else:
            print("Failed to load page")
            self.record_uid_attempt(False, "Page load failed")
            self._automation_active = False
            self._schedule_action(self.config['RETRY_DELAY_AFTER_FAILURE'], self.start_automation, "retry after load failure")
    
    def start_message_automation(self):
        """Start the message automation"""
        if self.automation:
            self.current_uid_attempts += 1
            self.automation.automate_messaging(
                message=self.current_message,
                delay=self.config['MESSAGE_RETRY_DELAY'],
                callback=self.on_message_completed
            )
    
    def on_message_completed(self, success, reason=None):
        """Callback when message automation completes"""
        reason_text = reason or "Unknown error"

        self._automation_active = False

        if success:
            self.record_uid_attempt(True)

            # Schedule next message after delay if we can send more
            if self.can_send_more_today():
                print(f"Waiting {self.config['DELAY_BETWEEN_MESSAGES']} seconds before next message...")
                self._schedule_action(self.config['DELAY_BETWEEN_MESSAGES'], self.start_automation, "next UID after success")
            else:
                print("Daily limit reached or no more UIDs. Automation stopped.")
            return

        # Handle failure scenarios
        print(f"Automation failed for UID {self.current_uid}: {reason_text}")

        if self._is_non_retryable(reason_text):
            print("Detected non-retryable failure (message box missing). Recording result and moving on.")
            self.record_uid_attempt(False, reason_text)

            if self.can_send_more_today():
                print(f"Waiting {self.config['RETRY_DELAY_AFTER_FAILURE']} seconds before next UID...")
                self._schedule_action(self.config['RETRY_DELAY_AFTER_FAILURE'], self.start_automation, "next UID after non-retryable failure")
            else:
                print("Daily limit reached or no more UIDs. Automation stopped.")
            return

        # Increment attempt counter for retryable errors
        print(f"Attempt {self.current_uid_attempts}/{self.config['MESSAGE_RETRY_ATTEMPTS']} for UID {self.current_uid}")

        if self.current_uid_attempts < self.config['MESSAGE_RETRY_ATTEMPTS']:
            print(f"Retrying UID {self.current_uid} after {self.config['RETRY_DELAY_AFTER_FAILURE']} seconds...")
            self._retry_same_uid = True
            self._schedule_action(self.config['RETRY_DELAY_AFTER_FAILURE'], self.start_automation, "retry same UID")
            return

        failure_reason = f"{reason_text} (after {self.config['MESSAGE_RETRY_ATTEMPTS']} attempts)"
        self.record_uid_attempt(False, failure_reason)

        if self.can_send_more_today():
            print(f"Max attempts reached for UID {self.current_uid}, trying next UID after {self.config['RETRY_DELAY_AFTER_FAILURE']} seconds...")
            self._schedule_action(self.config['RETRY_DELAY_AFTER_FAILURE'], self.start_automation, "next UID after retries exhausted")
        else:
            print("Daily limit reached or no more UIDs. Automation stopped.")

    def _is_non_retryable(self, reason):
        if not reason:
            return False

        normalized = reason.lower()
        if any(token in normalized for token in ('composer', 'message input box', 'message box')):
            return True

        return any(reason.startswith(prefix) for prefix in self.non_retryable_failure_reasons)
    
    def run(self):
        """Start the application"""
        self.window.showMaximized()

        # Start automation after window is shown
        self._schedule_action(2, self.start_automation, "initial automation start")

        return self.window

    def _print_tracker_status(self):
        today_stats = self.uid_tracker.today_summary()
        remaining = self.uid_tracker.remaining_quota()
        print(
            f"Today's Status: {today_stats['successful_sends']} sent, {today_stats['errors']} errors, {today_stats['total_attempted']} attempted"
        )
        print(f"Remaining daily quota: {remaining}")
        print(f"Available UIDs: {len(self.get_available_uids())}")

    def _normalize_uid(self, raw_uid: str):
        candidate = raw_uid.strip()
        if not candidate:
            return None

        if candidate.isdigit():
            return candidate

        if candidate.startswith('http://') or candidate.startswith('https://'):
            parsed = urlparse(candidate)
            if not parsed.path:
                return None

            if 'profile.php' in parsed.path:
                query = parse_qs(parsed.query)
                fb_id = query.get('id', [])
                if fb_id:
                    return fb_id[0]
                return None

            path = parsed.path.strip('/')
            if not path:
                return None

            return path.split('/')[0]

        return candidate

    def _schedule_action(self, delay_seconds, callback, description):
        if delay_seconds < 0:
            delay_seconds = 0

        if self._deferred_action_timer.isActive():
            self._deferred_action_timer.stop()

        self._pending_action = callback
        print(f"Scheduled {description} in {delay_seconds} seconds")
        self._deferred_action_timer.start(int(delay_seconds * 1000))

    def _run_pending_action(self):
        action = self._pending_action
        self._pending_action = None
        if callable(action):
            action()


class MainWindow(QMainWindow):
    def __init__(self):
        super(MainWindow, self).__init__()

        self.setWindowTitle('PyBro Messenger Automation')
        # set a custom icon for the window
        self.setWindowIcon(QIcon('icon.png'))

        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(False)   # remove the ✕, so no "Close" tooltip
        self.setCentralWidget(self.tabs)

        # Create portable profile directory
        self.profile_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'profile_data')
        if not os.path.exists(self.profile_path):
            os.makedirs(self.profile_path)
        
        # Create persistent profile
        self.profile = QWebEngineProfile("persistent_profile", self)
        self.profile.setPersistentStoragePath(self.profile_path)
        self.profile.setCachePath(self.profile_path)
        
        # Set a modern Chrome user agent
        modern_user_agent = (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/120.0.0.0 Safari/537.36'
        )
        self.profile.setHttpUserAgent(modern_user_agent)
        
        self.add_tab()

        # navbar
        navbar = QToolBar()
        self.addToolBar(navbar)

        
        back_btn = QAction('⮜', self)
        back_btn.triggered.connect(lambda: self.current_browser().back())
        navbar.addAction(back_btn)

        forward_btn = QAction('⮞', self)
        forward_btn.triggered.connect(lambda: self.current_browser().forward())
        navbar.addAction(forward_btn)

        reload_btn = QAction('⟳', self)
        reload_btn.triggered.connect(lambda: self.current_browser().reload())
        navbar.addAction(reload_btn)

        # Home Button
        home_btn = QAction('⌂', self)
        home_btn.triggered.connect(self.navigate_home)
        navbar.addAction(home_btn)

        # Add a new tab button
        add_tab_btn = QAction('+', self)
        add_tab_btn.triggered.connect(self.add_tab)
        navbar.addAction(add_tab_btn)

        # Add a url bar
        self.url_bar = QLineEdit()
        self.url_bar.returnPressed.connect(self.navigate_to_url)
        navbar.addWidget(self.url_bar)
        self.url_bar.setStyleSheet('width: 50%;')
        self.current_browser().urlChanged.connect(self.update_url)
        

    def add_tab(self):
        # Create browser with persistent profile using custom FBWebView
        browser = FBWebView()
        browser.setPage(QWebEnginePage(self.profile, browser))
        browser.setUrl(QUrl('https://www.facebook.com'))
        self.tabs.addTab(browser, 'facebook')
        self.tabs.setCurrentWidget(browser)
        self.tabs.setTabText(self.tabs.currentIndex(), 'Loading...')
        browser.titleChanged.connect(
            lambda title, browser=browser: self.tabs.setTabText(self.tabs.indexOf(browser), title))
        browser.urlChanged.connect(
            lambda url, browser=browser: self.update_url(url) if self.tabs.currentWidget() == browser else None)
        

    
    def close_tab(self, index):
        # Get the browser widget at the specified index
        browser_widget = self.tabs.widget(index)
    
        # Stop the video (if it is a video)
        if browser_widget.url().host() == "www.youtube.com":
            browser_widget.page().runJavaScript("document.getElementsByTagName('video')[0].pause();")
        
        # Remove the tab
        if self.tabs.count() < 2:
            # If this is the last tab, close the whole window
            self.close()
        else:
            # Remove the tab and delete the associated browser widget
            self.tabs.removeTab(index)
            browser_widget.deleteLater()


    def current_browser(self):
        return self.tabs.currentWidget()

    def navigate_home(self):
        self.current_browser().setUrl(QUrl('https://www.google.com'))

    def navigate_to_url(self):
        url = self.url_bar.text()
        if 'http' not in url:
            url = 'https://' + url
        self.current_browser().setUrl(QUrl(url))
    
    def update_url(self, q):
        if self.sender() == self.current_browser():
            self.url_bar.setText(q.toString())
            self.url_bar.setCursorPosition(0)

    def closeEvent(self, event):
        for i in range(self.tabs.count()):
            # get the browser widget in the current tab
            browser = self.tabs.widget(i)
            # get the video widget, if it exists
            video_widget = browser.findChild(QVideoWidget)
            if video_widget:
                # stop the video
                video_widget.player().stop()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName('PyBro Messenger Automation')
    app.setApplicationDisplayName('PyBro Messenger Automation')
    app.setOrganizationName('PyBro')
    
    automation = MessengerAutomation()
    window = automation.run()
    
    sys.exit(app.exec())
