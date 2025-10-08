import time
import json
import os
from PyQt6.QtCore import QTimer, QEventLoop

def make_typing_script(message, autosend=True):
    msg_js = json.dumps(message)  # safe escaping
    return f"""
(async () => {{
  const text = {msg_js};
  const autosend = {str(autosend).lower()};

  const sleep = (ms) => new Promise(r => setTimeout(r, ms));

  // Collect all same-origin documents (main + iframes)
  function sameOriginDocs() {{
    const docs = [document];
    const walk = (win) => {{
      for (let i = 0; i < win.frames.length; i++) {{
        const f = win.frames[i];
        try {{
          // Same-origin check: accessing .document will throw if cross-origin
          const d = f.document || f.contentDocument;
          if (d && docs.indexOf(d) === -1) {{
            docs.push(d);
            walk(f);
          }}
        }} catch (e) {{}}
      }}
    }};
    walk(window);
    return docs;
  }}

  function visible(el) {{
    if (!el) return false;
    const r = el.getBoundingClientRect();
    const style = el.ownerDocument.defaultView.getComputedStyle(el);
    return r.width > 0 && r.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  }}

  function findEditorIn(doc) {{
    // Robust selectors: prefer Lexical + textbox + contenteditable
    const candidates = [
      '[contenteditable="true"][data-lexical-editor="true"]',
      '[contenteditable="true"][role="textbox"]',
      '[role="textbox"][contenteditable="true"]',
      // any contenteditable as last fallback
      'div[contenteditable="true"]'
    ];
    for (const sel of candidates) {{
      const list = Array.from(doc.querySelectorAll(sel));
      for (const el of list) {{
        if (!el.getAttribute) continue;
        if (el.getAttribute('aria-disabled') === 'true') continue;
        if (visible(el)) return el;
      }}
    }}
    return null;
  }}

  async function waitForEditor(timeoutMs=20000) {{
    const start = performance.now();
    while (performance.now() - start < timeoutMs) {{
      const docs = sameOriginDocs();
      for (const d of docs) {{
        const el = findEditorIn(d);
        if (el) return el;
      }}
      await sleep(100);
    }}
    return null;
  }}

  let box = await waitForEditor(20000);
  if (!box) {{
    console.log('Composer not found in any same-origin frame');
    return {{ success: false, reason: 'Composer not found' }};
  }}

  // Focus correct browsing context (frame) if needed
  const ownerWin = box.ownerDocument.defaultView;
  try {{ ownerWin.focus(); }} catch (e) {{}}
  try {{ box.scrollIntoView({{block:'center'}}); }} catch (e) {{}}
  try {{ box.focus(); box.click(); }} catch (e) {{}}
  await sleep(80);

  // 1) Try execCommand (usually works best on rich editors)
  let inserted = false;
  try {{
    if (box.ownerDocument && box.ownerDocument.queryCommandSupported &&
        box.ownerDocument.queryCommandSupported('insertText')) {{
      inserted = box.ownerDocument.execCommand('insertText', false, text);
    }} else if (document.execCommand) {{
      inserted = document.execCommand('insertText', false, text);
    }}
  }} catch (e) {{ inserted = false; }}

  // 2) Fallback: beforeinput (Lexical listens to this)
  if (!inserted) {{
    try {{
      const be = new InputEvent('beforeinput', {{
        inputType: 'insertText',
        data: text,
        bubbles: true,
        cancelable: true,
        composed: true
      }});
      box.dispatchEvent(be);
      await sleep(50);
    }} catch (e) {{}}
  }}

  // 3) Last fallback: set DOM + move caret + fire 'input'
  const contentNow = (box.innerText || '').trim();
  if (!contentNow) {{
    try {{
      const p = box.ownerDocument.createElement('p');
      p.setAttribute('dir','auto');
      p.textContent = text;
      box.innerHTML = '';
      box.appendChild(p);

      const range = box.ownerDocument.createRange();
      range.selectNodeContents(box);
      range.collapse(false);
      const sel = ownerWin.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);

      box.dispatchEvent(new Event('input', {{ bubbles: true }}));
    }} catch (e) {{
      // if even this fails, bail
    }}
  }}

  await sleep(150);

  if (autosend) {{
    // Prefer the explicit "Press Enter to send" control if present
    const sendBtn =
      box.ownerDocument.querySelector('[aria-label="Press Enter to send"], [aria-label="Press enter to send"]') ||
      box.ownerDocument.querySelector('[aria-label="Send"]'); // extra fallback
    if (sendBtn && visible(sendBtn)) {{
      try {{ sendBtn.click(); }} catch (e) {{}}
    }} else {{
      // Simulate Enter (keydown + keyup) on the editor
      const kd = new KeyboardEvent('keydown', {{
        key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true
      }});
      const ku = new KeyboardEvent('keyup', {{
        key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: false
      }});
      box.dispatchEvent(kd);
      box.dispatchEvent(ku);
    }}
  }}

  console.log('Typed:', text, ' autosend:', autosend);
  return {{ success: true }};
}})();
"""

class BrowserAutomation:
    def __init__(self, browser):
        self.browser = browser
        self.message_sent = False
        self.attempt_count = 0
        self.max_attempts = 10
        self.csp_disabled = False
        self.current_message = "hi"  # Default message
        self.callback = None  # Callback function for completion
        self.error_detected = False  # Initialize error detection flag
        self.message_box_present = False  # Initialize message box presence flag
    
    def setup_permanent_popup_blocking(self):
        """Set up permanent popup blocking that runs on every page load"""
        # This script runs on every page load to block popups permanently
        popup_block_script = """
        // Permanent popup blocking - runs on every page
        (function() {
            // Block all browser dialogs permanently
            window.alert = function() { console.log('Alert blocked permanently'); };
            window.confirm = function() { console.log('Confirm blocked permanently'); return true; };
            window.prompt = function() { console.log('Prompt blocked permanently'); return ''; };
            
            // Disable right-click context menu and view source
            document.addEventListener('contextmenu', function(e) {
                e.preventDefault();
                console.log('Right-click context menu blocked');
                return false;
            });
            
            // Disable keyboard shortcuts for view source
            document.addEventListener('keydown', function(e) {
                // Ctrl+U (View Source)
                if (e.ctrlKey && e.key === 'u') {
                    e.preventDefault();
                    console.log('View source shortcut blocked');
                    return false;
                }
                // F12 (Developer Tools)
                if (e.key === 'F12') {
                    e.preventDefault();
                    console.log('Developer Tools blocked');
                    return false;
                }
                // Ctrl+Shift+I (Developer Tools)
                if (e.ctrlKey && e.shiftKey && e.key === 'I') {
                    e.preventDefault();
                    console.log('Developer Tools shortcut blocked');
                    return false;
                }
            });
            
            // Function to close all popups - SAFE VERSION (only clicks close buttons, no removal of containers)
            function closeAllPopups() {
                try {
                    // SAFE: Only click explicit close buttons, don't remove generic containers
                    const closeButtons = document.querySelectorAll(
                        'button[aria-label="Close"], [aria-label="Close dialog"], [data-testid="close"]'
                    );
                    closeButtons.forEach(btn => { 
                        try { 
                            if (btn && btn.click) {
                                btn.click(); 
                                console.log('Clicked close button');
                            }
                        } catch(e){} 
                    });
                    
                    // Remove CSP meta tags (safe to remove)
                    document.querySelectorAll('meta[http-equiv="Content-Security-Policy"]').forEach(meta => meta.remove());
                    
                } catch (error) {
                    console.log('Error in popup blocking:', error);
                }
            }
            
            // Run immediately
            closeAllPopups();
            
            // Run every 2 seconds to catch new popups
            setInterval(closeAllPopups, 2000);
            
            // Also run on DOM changes
            const observer = new MutationObserver(function(mutations) {
                mutations.forEach(function(mutation) {
                    if (mutation.addedNodes.length > 0) {
                        setTimeout(closeAllPopups, 100);
                    }
                });
            });
            
            observer.observe(document.body, {
                childList: true,
                subtree: true
            });
            
            console.log('Permanent popup blocking activated');
        })();
        """
        
        # Inject the permanent popup blocking script
        self.browser.page().runJavaScript(popup_block_script)
        
    def disable_csp_and_popups(self):
        """Disable Content Security Policy and block popups for the browser - run on every attempt"""
        # Set up permanent popup blocking (runs on every page)
        self.setup_permanent_popup_blocking()
        
        # Additional one-time CSP removal
        if not self.csp_disabled:
            csp_script = """
            (function() {
                try {
                    // Remove CSP meta tags
                    document.querySelectorAll('meta[http-equiv="Content-Security-Policy"]').forEach(meta => meta.remove());
                    console.log('CSP disabled successfully');
                    return true;
                } catch (error) {
                    console.log('Error disabling CSP:', error);
                    return false;
                }
            })()
            """
            self.browser.page().runJavaScript(csp_script)
            self.csp_disabled = True
    
    def type_message(self, message="hi", autosend=True):
        """Type (and optionally send) a message in Facebook Messenger."""
        script = make_typing_script(message, autosend)
        self.browser.page().runJavaScript(script, self._type_message_callback)
    
    def set_message(self, message):
        """Set the message to be typed"""
        self.current_message = message
    
    def set_callback(self, callback):
        """Set the callback function for completion"""
        self.callback = callback
    
    def _type_message_callback(self, result):
        """Callback for the typing script"""
        success = False
        if result and result.get('success'):
            print("Message typing successful - stopping automation timer")
            self.message_sent = True
            success = True
            # Stop the timer immediately when message is sent successfully
            if hasattr(self, 'timer') and self.timer.isActive():
                self.timer.stop()
                print("Automation timer stopped")
        else:
            reason = result.get('reason', 'Unknown error') if result else 'No result returned'
            print(f"Message typing failed - {reason}")
            self.attempt_count += 1
            if self.attempt_count >= self.max_attempts and hasattr(self, 'timer') and self.timer.isActive():
                print("Max attempts reached, stopping automation")
                self.timer.stop()
        
        # Call the external callback if set
        if self.callback:
            self.callback(success)
    
    def attempt_typing(self):
        """Attempt to type the message"""
        # Prevent multiple attempts if message already sent or max attempts reached
        if self.message_sent:
            print("Message already sent, skipping attempt")
            return
        
        if self.attempt_count >= self.max_attempts:
            print("Max attempts reached, skipping attempt")
            if hasattr(self, 'timer') and self.timer.isActive():
                self.timer.stop()
            return
        
        # Always disable CSP and popups on every attempt (reset for new pages)
        self.csp_disabled = False  # Reset to ensure CSP is disabled on new pages
        self.disable_csp_and_popups()
        
        # Directly check if message typing box is present - synchronous check
        self._check_message_box_present()
        
        # Only proceed with typing if message box is present
        if self.message_box_present:
            print("Message input box found, proceeding with message typing - stopping timer")
            # Stop the timer immediately to prevent multiple attempts
            if hasattr(self, 'timer') and self.timer.isActive():
                self.timer.stop()
                print("Automation timer stopped to prevent multiple attempts")
            
            # Try to type the message
            self.type_message(self.current_message)
            
            self.attempt_count += 1
            print(f"Attempt {self.attempt_count}/{self.max_attempts}")
        else:
            print("Message input box not found, skipping message typing")
            self.attempt_count += 1
            if self.attempt_count >= self.max_attempts and hasattr(self, 'timer') and self.timer.isActive():
                print("Max attempts reached, stopping automation")
                self.timer.stop()
    
    def _check_message_box_present(self):
        """Directly check if message typing box is present - synchronous check"""
        # Use a much simpler script first to test if JavaScript execution works
        simple_test_script = """
        (function() {
            try {
                console.log('=== SIMPLE TEST: JavaScript is executing ===');
                return {success: true, message: 'JavaScript is working'};
            } catch (error) {
                return {success: false, error: error.toString()};
            }
        })()
        """

        print("Testing JavaScript execution...")
        test_result = self._run_javascript_sync(simple_test_script, timeout_ms=4000)
        print(f"JavaScript test result: {test_result}")

        if not test_result or not test_result.get('success'):
            error_msg = test_result.get('error') if isinstance(test_result, dict) else 'No result returned'
            print(f"ERROR: JavaScript execution is failing - {error_msg}")
            self.message_box_present = False
            return

        # Now try the actual detection with a simpler approach
        detection_script = """
        (function() {
            try {
                console.log('=== MESSAGE BOX DETECTION STARTED ===');

                const selectors = [
                    '[aria-label="Message"][role="textbox"][contenteditable="true"]',
                    '[contenteditable="true"][data-lexical-editor="true"][role="textbox"]',
                    'div[aria-label="Message"][contenteditable="true"]',
                    '[role="textbox"][contenteditable="true"]',
                    'div[contenteditable="true"]'
                ];

                const visible = (el) => {
                    if (!el) return false;
                    const rect = el.getBoundingClientRect();
                    const style = el.ownerDocument.defaultView.getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
                };

                const collectDocs = () => {
                    const docs = [];
                    const visit = (win) => {
                        if (!win || docs.includes(win.document)) {
                            return;
                        }
                        docs.push(win.document);
                        for (let i = 0; i < win.frames.length; i++) {
                            try {
                                visit(win.frames[i]);
                            } catch (err) {
                                // Ignore cross-origin frames
                            }
                        }
                    };
                    try { visit(window); } catch (err) {}
                    return docs;
                };

                const docs = collectDocs();
                const matches = [];

                for (const doc of docs) {
                    for (const selector of selectors) {
                        const elements = Array.from(doc.querySelectorAll(selector));
                        for (const el of elements) {
                            if (!visible(el)) continue;
                            if (el.getAttribute('aria-disabled') === 'true') continue;

                            matches.push({
                                selector,
                                attributes: {
                                    tagName: el.tagName,
                                    className: el.className,
                                    ariaLabel: el.getAttribute('aria-label'),
                                    role: el.getAttribute('role'),
                                    dataLexicalEditor: el.getAttribute('data-lexical-editor'),
                                    ariaDescribedBy: el.getAttribute('aria-describedby')
                                },
                                frameUrl: (el.ownerDocument && el.ownerDocument.defaultView) ? el.ownerDocument.defaultView.location.href : null
                            });
                        }
                        if (matches.length) break;
                    }
                    if (matches.length) break;
                }

                if (matches.length) {
                    const first = matches[0];
                    console.log('Message composer detected using selector', first.selector, first.attributes);
                    return {present: true, details: first};
                }

                console.log('No suitable contenteditable message composer located');
                return {present: false, reason: 'Composer not found with stable selectors'};
            } catch (error) {
                console.log('Error in detection script:', error);
                return {present: false, reason: 'Script error: ' + error.toString()};
            }
        })()
        """

        print("Running simple message box detection...")
        result = self._run_javascript_sync(detection_script, timeout_ms=6000)
        print(f"Simple detection result: {result}")

        if result and result.get('present'):
            self.message_box_present = True
            details = result.get('details', {})
            print("Message input box is present and ready")
            if details:
                print(f"Detection selector: {details.get('selector')}")
                print(f"Element attributes: {details.get('attributes')}")
                if details.get('frameUrl'):
                    print(f"Frame URL: {details.get('frameUrl')}")
        else:
            self.message_box_present = False
            reason = result.get('reason', 'No result returned') if isinstance(result, dict) else 'No result returned'
            print(f"Message input box not available: {reason}")
    
    def _check_for_errors_sync(self):
        """Synchronous error checking - waits for result before proceeding"""
        # Read error list from file
        error_list = self._read_error_list()
        
        # Create JavaScript array from error list
        error_list_js = json.dumps(error_list)
        
        error_check_script = f"""
        (function() {{
            try {{
                console.log('=== Starting error detection ===');
                
                // Check for visible error elements first (most reliable)
                const visibleErrorSelectors = [
                    '[role="alert"][aria-live="assertive"]',
                    '[data-testid="error_message"]',
                    '.error:not(.encryption)',
                    '.error_message',
                    '.messenger_error',
                    '[aria-label*="error" i]',
                    '[class*="error" i]'
                ];
                
                for (const selector of visibleErrorSelectors) {{
                    const elements = document.querySelectorAll(selector);
                    console.log('Checking selector "' + selector + '": ' + elements.length + ' elements');
                    for (const element of elements) {{
                        // Only check if element is actually visible
                        const rect = element.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) {{
                            const elementText = element.innerText || element.textContent || '';
                            if (elementText) {{
                                console.log('Found visible error element:', elementText);
                                return {{error: true, reason: 'Visible error: ' + elementText}};
                            }}
                        }}
                    }}
                }}
                
                // Check for specific error messages in visible text (not source code)
                const visibleText = document.body.innerText || document.body.textContent || '';
                console.log('Visible text length:', visibleText.length);
                
                const criticalErrors = {error_list_js};
                console.log('Checking against', criticalErrors.length, 'error patterns');
                
                // Only check for "Facebook user" if it's in a prominent position
                const prominentElements = document.querySelectorAll('h1, h2, h3, [role="heading"], .title, .header');
                let hasFacebookUserError = false;
                for (const element of prominentElements) {{
                    const text = element.innerText || element.textContent || '';
                    if (text.includes('Facebook user')) {{
                        hasFacebookUserError = true;
                        break;
                    }}
                }}
                
                if (hasFacebookUserError) {{
                    console.log('Found "Facebook user" error in prominent element');
                    return {{error: true, reason: 'Error: Facebook user'}};
                }}
                
                // Check all critical errors in visible text
                for (const errorMsg of criticalErrors) {{
                    if (visibleText.includes(errorMsg)) {{
                        console.log('Found critical error in visible text:', errorMsg);
                        return {{error: true, reason: 'Error: ' + errorMsg}};
                    }}
                }}
                
                // Check if message input box exists
                const box = document.querySelector('[contenteditable="true"][data-lexical-editor="true"]')
                       || document.querySelector('[contenteditable="true"][role="textbox"]')
                       || document.querySelector('[role="textbox"][contenteditable="true"]')
                       || document.querySelector('div[contenteditable="true"]');
                
                console.log('Message input box found:', !!box);
                
                if (!box) {{
                    console.log('Message input box not found');
                    return {{error: true, reason: 'Message input box not found'}};
                }}
                
                // Check if box is visible and enabled
                const rect = box.getBoundingClientRect();
                const style = box.ownerDocument.defaultView.getComputedStyle(box);
                const isVisible = rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
                console.log('Message input box visible:', isVisible);
                
                if (!isVisible) {{
                    console.log('Message input box not visible');
                    return {{error: true, reason: 'Message input box not visible'}};
                }}
                
                const isDisabled = box.getAttribute('aria-disabled') === 'true';
                console.log('Message input box disabled:', isDisabled);
                
                if (isDisabled) {{
                    console.log('Message input box is disabled');
                    return {{error: true, reason: 'Message input box is disabled'}};
                }}
                
                console.log('No errors detected, message input box is ready');
                return {{error: false}};
                
            }} catch (error) {{
                console.log('Error in error checking script:', error);
                return {{error: true, reason: 'Error checking failed: ' + error}};
            }}
        }})()
        """
        
        # Run the error check script synchronously
        print("Running synchronous error detection...")
        result = self._run_javascript_sync(error_check_script, timeout_ms=6000)
        print(f"Error detection result: {result}")

        # Process the result
        if result and result.get('error'):
            self.error_detected = True
            reason = result.get('reason', 'Unknown error')
            print(f"Error detected: {reason}")
        else:
            self.error_detected = False
            print("No errors detected, proceeding with message typing")
    
    def check_for_errors(self):
        """Check for Facebook error messages before attempting to type"""
        # Read error list from file
        error_list = self._read_error_list()
        
        # Create JavaScript array from error list
        error_list_js = json.dumps(error_list)
        
        error_check_script = f"""
        (function() {{
            try {{
                // Check for visible error elements first (most reliable)
                const visibleErrorSelectors = [
                    '[role="alert"][aria-live="assertive"]',
                    '[data-testid="error_message"]',
                    '.error:not(.encryption)',
                    '.error_message',
                    '.messenger_error',
                    '[aria-label*="error" i]',
                    '[class*="error" i]'
                ];
                
                for (const selector of visibleErrorSelectors) {{
                    const elements = document.querySelectorAll(selector);
                    for (const element of elements) {{
                        // Only check if element is actually visible
                        const rect = element.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) {{
                            const elementText = element.innerText || element.textContent || '';
                            if (elementText) {{
                                console.log('Found visible error element:', elementText);
                                return {{error: true, reason: 'Visible error: ' + elementText}};
                            }}
                        }}
                    }}
                }}
                
                // Check for specific error messages in visible text (not source code)
                const visibleText = document.body.innerText || document.body.textContent || '';
                const criticalErrors = {error_list_js};
                
                // Only check for "Facebook user" if it's in a prominent position
                const prominentElements = document.querySelectorAll('h1, h2, h3, [role="heading"], .title, .header');
                let hasFacebookUserError = false;
                for (const element of prominentElements) {{
                    const text = element.innerText || element.textContent || '';
                    if (text.includes('Facebook user')) {{
                        hasFacebookUserError = true;
                        break;
                    }}
                }}
                
                if (hasFacebookUserError) {{
                    console.log('Found "Facebook user" error in prominent element');
                    return {{error: true, reason: 'Error: Facebook user'}};
                }}
                
                // Check all critical errors in visible text
                for (const errorMsg of criticalErrors) {{
                    if (visibleText.includes(errorMsg)) {{
                        console.log('Found critical error in visible text:', errorMsg);
                        return {{error: true, reason: 'Error: ' + errorMsg}};
                    }}
                }}
                
                // Check if message input box exists
                const box = document.querySelector('[contenteditable="true"][data-lexical-editor="true"]')
                       || document.querySelector('[contenteditable="true"][role="textbox"]')
                       || document.querySelector('[role="textbox"][contenteditable="true"]')
                       || document.querySelector('div[contenteditable="true"]');
                
                if (!box) {{
                    console.log('Message input box not found');
                    return {{error: true, reason: 'Message input box not found'}};
                }}
                
                // Check if box is visible and enabled
                const rect = box.getBoundingClientRect();
                const style = box.ownerDocument.defaultView.getComputedStyle(box);
                if (!(rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none')) {{
                    console.log('Message input box not visible');
                    return {{error: true, reason: 'Message input box not visible'}};
                }}
                
                if (box.getAttribute('aria-disabled') === 'true') {{
                    console.log('Message input box is disabled');
                    return {{error: true, reason: 'Message input box is disabled'}};
                }}
                
                console.log('No errors detected, message input box is ready');
                return {{error: false}};
                
            }} catch (error) {{
                console.log('Error in error checking script:', error);
                return {{error: true, reason: 'Error checking failed: ' + error}};
            }}
        }})()
        """
        
        # Run the error check script
        self.browser.page().runJavaScript(error_check_script, self._error_check_callback)
    
    def _read_error_list(self):
        """Read error list from file"""
        try:
            with open('error_list.txt', 'r', encoding='utf-8') as f:
                errors = [line.strip() for line in f if line.strip()]
            print(f"Loaded {len(errors)} error patterns from error_list.txt")
            return errors
        except FileNotFoundError:
            print("Error: error_list.txt not found, using default error list")
            return [
                "This person is unavailable on Messenger.",
                "can't access this chat yet", 
                "You've reached the message request limit",
                "This person isn't available right now",
                "You can't message this account",
                "Message request limit reached",
                "This person isn't available",
                "unavailable on Messenger",
                "can't access this chat",
                "Facebook user",
                "This chat is now secured with end-to-end encryption."
            ]
    
    def _error_check_callback(self, result):
        """Callback for error checking"""
        if result and result.get('error'):
            self.error_detected = True
            reason = result.get('reason', 'Unknown error')
            print(f"Error detected: {reason}")
        else:
            self.error_detected = False
            print("No errors detected, proceeding with message typing")
    
    def automate_messaging(self, message="hi", delay=3, callback=None):
        """Automate the messaging process with retries"""
        print(f"Starting automation with {delay} second delay between attempts")
        
        # Set the message and callback
        self.current_message = message
        self.callback = callback
        
        # Reset state for new automation
        self.message_sent = False
        self.attempt_count = 0
        
        # Set up a timer to attempt typing periodically
        self.timer = QTimer()
        self.timer.timeout.connect(self.attempt_typing)
        self.timer.start(delay * 1000)  # Check every 'delay' seconds

        # Stop after max attempts
        QTimer.singleShot(self.max_attempts * delay * 1000, lambda: self.timer.stop() if hasattr(self, 'timer') else None)

    def _run_javascript_sync(self, script, timeout_ms=5000):
        """Execute JavaScript and wait synchronously for the result."""
        loop = QEventLoop()
        result_container = {}
        timed_out = {'value': False}

        def handle_result(result):
            if timed_out['value']:
                return
            result_container['result'] = result
            timeout_timer.stop()
            loop.quit()

        def handle_timeout():
            timed_out['value'] = True
            timeout_timer.stop()
            loop.quit()

        timeout_timer = QTimer()
        timeout_timer.setSingleShot(True)
        timeout_timer.timeout.connect(handle_timeout)

        self.browser.page().runJavaScript(script, handle_result)
        timeout_timer.start(timeout_ms)

        loop.exec()

        if timed_out['value']:
            print(f"JavaScript execution timed out after {timeout_ms} ms")
            return None

        return result_container.get('result')

# Utility function to create automation instance
def create_automation(browser):
    return BrowserAutomation(browser)
