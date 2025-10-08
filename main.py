import sys
import os
import random
import json
import time
from datetime import datetime, date
from PyQt6.QtCore import *
from PyQt6.QtWidgets import *
from PyQt6.QtWebEngineWidgets import *
from PyQt6.QtWebEngineCore import QWebEngineProfile, QWebEnginePage
from PyQt6.QtGui import QIcon, QAction
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtCore import QEvent


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
        self.load_uids()
        self.load_messages()
        self.load_tracker()
        
        # Create main window
        self.window = MainWindow()
        self.automation = None
        
        # Current state
        self.current_uid = None
        self.current_message = None
        self.current_uid_status = None  # 'sent', 'error', 'attempting'
        self.current_uid_attempts = 0  # Track attempts per UID
        
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
        """Load UIDs from uids.txt"""
        try:
            with open('uids.txt', 'r', encoding='utf-8') as f:
                self.all_uids = [line.strip() for line in f if line.strip()]
            
            if not self.all_uids:
                print("Error: No UIDs found in uids.txt")
                sys.exit(1)
                
            print(f"Loaded {len(self.all_uids)} UIDs from uids.txt")
            
        except FileNotFoundError:
            print("Error: uids.txt not found")
            sys.exit(1)
        except Exception as e:
            print(f"Error reading uids.txt: {e}")
            sys.exit(1)
    
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
    
    def load_tracker(self):
        """Load UID tracking data"""
        self.tracker_file = 'uid_tracker.json'
        today = date.today().isoformat()
        
        try:
            with open(self.tracker_file, 'r') as f:
                self.tracker = json.load(f)
                
            # Check if we need to reset for new day
            if self.tracker['last_reset_date'] != today:
                print(f"New day detected: {today}, resetting daily counters")
                self.tracker['last_reset_date'] = today
                self.tracker['daily_stats'][today] = {
                    "total_attempted": 0,
                    "successful_sends": 0,
                    "errors": 0,
                    "used_uids": []
                }
                self.save_tracker()
            else:
                # Ensure today's stats exist
                if today not in self.tracker['daily_stats']:
                    self.tracker['daily_stats'][today] = {
                        "total_attempted": 0,
                        "successful_sends": 0,
                        "errors": 0,
                        "used_uids": []
                    }
                    self.save_tracker()
                    
        except FileNotFoundError:
            # Initialize new tracker
            self.tracker = {
                "last_reset_date": today,
                "used_uids": [],
                "daily_stats": {
                    today: {
                        "total_attempted": 0,
                        "successful_sends": 0,
                        "errors": 0,
                        "used_uids": []
                    }
                }
            }
            self.save_tracker()
        except Exception as e:
            print(f"Error loading tracker: {e}")
            sys.exit(1)
            
        # Print current status
        today_stats = self.tracker['daily_stats'][today]
        print(f"Today's Status: {today_stats['successful_sends']} sent, {today_stats['errors']} errors, {today_stats['total_attempted']} attempted")
        print(f"Total used UIDs: {len(self.tracker['used_uids'])}")
        print(f"Available UIDs: {len(self.all_uids) - len(self.tracker['used_uids'])}")
    
    def save_tracker(self):
        """Save UID tracking data"""
        try:
            with open(self.tracker_file, 'w') as f:
                json.dump(self.tracker, f, indent=4)
        except Exception as e:
            print(f"Error saving tracker: {e}")
    
    def get_available_uids(self):
        """Get list of UIDs that haven't been used yet"""
        used_set = set(self.tracker['used_uids'])
        available = [uid for uid in self.all_uids if uid not in used_set]
        return available
    
    def can_send_more_today(self):
        """Check if we can send more messages today"""
        today = date.today().isoformat()
        today_stats = self.tracker['daily_stats'][today]
        
        if today_stats['successful_sends'] >= self.config['MAX_MESSAGES_PER_DAY']:
            print(f"Daily limit reached: {today_stats['successful_sends']}/{self.config['MAX_MESSAGES_PER_DAY']}")
            return False
        
        available_uids = self.get_available_uids()
        if not available_uids:
            print("No more available UIDs to try")
            return False
            
        return True
    
    def select_next_uid_and_message(self):
        """Select next available UID and random message - process in file order"""
        available_uids = self.get_available_uids()
        
        if not available_uids:
            print("No available UIDs left")
            return None, None
            
        # Process UIDs in the order they appear in the original file
        # This ensures systematic processing from top to bottom
        for uid in self.all_uids:
            if uid in available_uids:
                self.current_uid = uid
                self.current_message = random.choice(self.messages)
                self.current_uid_status = 'attempting'
                
                print(f"Selected UID: {self.current_uid} (in file order)")
                print(f"Selected message: {self.current_message}")
                print(f"Available UIDs remaining: {len(available_uids) - 1}")
                
                return self.current_uid, self.current_message
        
        # Fallback if no UID found (shouldn't happen)
        return None, None
    
    def record_uid_attempt(self, success, error_reason=None):
        """Record UID attempt result"""
        today = date.today().isoformat()
        
        # Add to used UIDs if not already there
        if self.current_uid not in self.tracker['used_uids']:
            self.tracker['used_uids'].append(self.current_uid)
        
        # Update daily stats
        self.tracker['daily_stats'][today]['total_attempted'] += 1
        
        if success:
            self.tracker['daily_stats'][today]['successful_sends'] += 1
            self.current_uid_status = 'sent'
            print(f"✅ UID {self.current_uid} - Message sent successfully")
        else:
            self.tracker['daily_stats'][today]['errors'] += 1
            self.current_uid_status = 'error'
            error_msg = f" - {error_reason}" if error_reason else ""
            print(f"❌ UID {self.current_uid} - Failed{error_msg}")
        
        # Add to today's used UIDs
        if self.current_uid not in self.tracker['daily_stats'][today]['used_uids']:
            self.tracker['daily_stats'][today]['used_uids'].append(self.current_uid)
        
        self.save_tracker()
        
        # Print updated status
        today_stats = self.tracker['daily_stats'][today]
        print(f"Progress: {today_stats['successful_sends']} sent, {today_stats['errors']} errors, {today_stats['total_attempted']} attempted")
        print(f"Available UIDs remaining: {len(self.get_available_uids())}")
    
    def start_automation(self):
        """Start the automation process"""
        if not self.can_send_more_today():
            print("Cannot send more messages today. Exiting.")
            return
            
        uid, message = self.select_next_uid_and_message()
        
        if not uid:
            print("No UIDs available to process")
            return
            
        # Reset attempt counter for new UID
        self.current_uid_attempts = 0
        
        # Disconnect any previous loadFinished connections to prevent stacking
        try:
            self.window.current_browser().loadFinished.disconnect()
        except:
            pass
        
        # Set up automation
        self.automation = create_automation(self.window.current_browser())
        self.automation.set_message(message)
        
        # Navigate to the selected UID with proper timing
        url = f'https://www.facebook.com/messages/t/{uid}'
        print(f"Navigating to: {url}")
        
        # Use a small delay before navigation to ensure browser is ready
        QTimer.singleShot(500, lambda: self.window.current_browser().setUrl(QUrl(url)))
        
        # Start automation after page loads (single connection)
        self.window.current_browser().loadFinished.connect(self.on_page_loaded, Qt.ConnectionType.QueuedConnection)
    
    def on_page_loaded(self, success):
        """Callback when page is loaded"""
        if success:
            print(f"Page loaded successfully, waiting {self.config['PAGE_LOAD_WAIT_TIME']} seconds for full load...")
            # Wait for page to fully load, then start automation
            QTimer.singleShot(self.config['PAGE_LOAD_WAIT_TIME'] * 1000, self.start_message_automation)
        else:
            print("Failed to load page")
            self.record_uid_attempt(False, "Page load failed")
            QTimer.singleShot(self.config['RETRY_DELAY_AFTER_FAILURE'] * 1000, self.start_automation)
    
    def start_message_automation(self):
        """Start the message automation"""
        if self.automation:
            self.automation.automate_messaging(
                message=self.current_message,
                delay=self.config['MESSAGE_RETRY_DELAY'],
                callback=self.on_message_completed
            )
    
    def on_message_completed(self, success):
        """Callback when message automation completes"""
        if success:
            self.record_uid_attempt(True)
            
            # Schedule next message after delay if we can send more
            if self.can_send_more_today():
                delay_ms = self.config['DELAY_BETWEEN_MESSAGES'] * 1000
                print(f"Waiting {self.config['DELAY_BETWEEN_MESSAGES']} seconds before next message...")
                QTimer.singleShot(delay_ms, self.start_automation)
            else:
                print("Daily limit reached or no more UIDs. Automation stopped.")
        else:
            # Increment attempt counter for current UID
            self.current_uid_attempts += 1
            print(f"Attempt {self.current_uid_attempts}/{self.config['MESSAGE_RETRY_ATTEMPTS']} for UID {self.current_uid}")
            
            # Check if we should retry the same UID or move to next
            if self.current_uid_attempts < self.config['MESSAGE_RETRY_ATTEMPTS']:
                # Retry same UID
                print(f"Retrying UID {self.current_uid} after {self.config['RETRY_DELAY_AFTER_FAILURE']} seconds...")
                QTimer.singleShot(self.config['RETRY_DELAY_AFTER_FAILURE'] * 1000, self.start_automation)
            else:
                # Max attempts reached for this UID, record failure and move to next
                self.record_uid_attempt(False, f"Message typing failed after {self.config['MESSAGE_RETRY_ATTEMPTS']} attempts")
                
                # Try next UID after delay if we can send more
                if self.can_send_more_today():
                    print(f"Max attempts reached for UID {self.current_uid}, trying next UID after {self.config['RETRY_DELAY_AFTER_FAILURE']} seconds...")
                    QTimer.singleShot(self.config['RETRY_DELAY_AFTER_FAILURE'] * 1000, self.start_automation)
                else:
                    print("Daily limit reached or no more UIDs. Automation stopped.")
    
    def run(self):
        """Start the application"""
        self.window.showMaximized()
        
        # Start automation after window is shown
        QTimer.singleShot(2000, self.start_automation)
        
        return self.window


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
