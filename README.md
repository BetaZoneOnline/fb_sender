# PyBro Messenger Automation Controller

This project provides a PyQt6-based controller for orchestrating Facebook Messenger outreach with strict daily limits, per-UID tracking, and a persistent audit trail. It includes a UID management dashboard, a queue engine that processes recipients one-by-one, and a browser window powered by Qt WebEngine for manual login and automation visibility.

> **Important:** Use this project responsibly and in accordance with Facebook's platform policies. The automation logic is designed for consensual follow-ups with existing contacts and applies conservative message caps.

## Features

- **Dashboard-first workflow** – Launches to the UID Management screen with live counters, current UID status, and structured error summaries.
- **Deterministic queue engine** – Processes UIDs sequentially, marks successes and failures, and never retries a UID that reached a terminal state unless manually requested.
- **Daily limit enforcement** – Tracks sends per day in the Asia/Kathmandu timezone with a countdown to the next reset.
- **Robust storage** – Persists all state in SQLite, including UID events, per-day counters, and profile settings.
- **Manual controls** – Pause, resume, stop, and “login only” buttons, plus one-click CSV export and manual retry of selected UIDs.
- **Evidence capture** – Optional screenshots on failure stored under `data/evidence/`.

## Project Layout

```
app/
  automations/
    fb_worker.py        # Adapter around the legacy BrowserAutomation helper
  browser_window.py     # Persistent Qt WebEngine browser for Messenger
  config/defaults.json  # Runtime defaults (limits, timeouts, directories)
  profile_manager.py    # Loads defaults, manages profile + timezone info
  storage.py            # SQLite schema, migrations, and CRUD helpers
  task_engine.py        # Deterministic queue runner with retry policy
  uid_management_gui.py # Dashboard UI and table model
automation.py            # Legacy BrowserAutomation implementation
main.py                  # Application entry point
```

The SQLite database lives at `data/app.db`. Evidence captures are written to `data/evidence/`.

## Requirements

- Python 3.10+
- [PyQt6](https://pypi.org/project/PyQt6/)
- [PyQt6-WebEngine](https://pypi.org/project/PyQt6-WebEngine/)

Install dependencies with:

```bash
pip install PyQt6 PyQt6-WebEngine
```

## Running the App

1. Ensure you have populated `messages.txt` with the message templates you want to send.
2. Add any initial UIDs to `uids.txt` (or import directly from the dashboard).
3. Launch the application:

```bash
python main.py
```

4. Log into Facebook Messenger using the **Login Only** button if required.
5. Import UIDs or paste them directly in the dashboard, configure your limits, and press **Start**.

The controller will process UIDs sequentially, respecting the configured delay and retry policy. All outcomes are persisted so the same UID will never be processed twice in the same day unless you manually schedule a retry.

## Data & Reset Behaviour

- Daily counters reset automatically at midnight Asia/Kathmandu.
- Stale “in-progress” UIDs (e.g. after a crash) are automatically recovered and queued for retry on the next launch.
- Manual retries decrement the attempt counter and re-queue the selected UIDs immediately.

## Development

Run a bytecode compilation check to ensure the project imports cleanly:

```bash
python -m compileall .
```

Feel free to extend the dashboard, add additional profiles, or integrate extra automation safeguards. Pull requests and improvements are welcome.
