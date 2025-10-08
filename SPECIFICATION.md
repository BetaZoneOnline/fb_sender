# UID Management & Messaging Controller — Phase 1 Specification

## Feature Summary
Build a PyQt-driven control center that manages Facebook UID messaging end-to-end: import and normalize UIDs, schedule them sequentially through an automation worker, enforce daily send limits, expose live progress metrics, handle retries with countdowns, and guarantee no duplicate processing.

## System Components
- `main.py`: Launches the Qt application and displays the UID management dashboard on startup.
- `uid_management_gui.py`: Contains the dashboard window, control panels, tables, and signal bindings for live status updates.
- `task_engine.py`: Owns the processing loop, leasing UIDs sequentially, orchestrating retries/delays, and enforcing daily limits.
- `storage.py`: SQLite data-access layer handling migrations, UID imports, leasing, completion, and event logging.
- `profile_manager.py`: Maintains the active profile’s nickname, timezone, limit counters, and reset calculations.
- `automations/fb_worker.py`: Adapter that wraps the browser automation to send a message to one UID and report structured results.
- `config/defaults.json` & `config/loader.py`: Provide runtime configuration for delays, limits, retry settings, database paths, and evidence folders.
- `message_provider.py`: Supplies the message payload/template used by the worker.
- `assets/` (icons) & `data/` (database/evidence): Resource directories referenced by the UI and storage layer.

## UI/UX Design Plan
1. **Header Bar**
   - App title + editable profile nickname field.
   - Engine state badge (Idle, Running, Paused, Stopped, Login-Only) with color coding (gray, green, amber, red, blue).
   - Clock showing Asia/Kathmandu time.
2. **Left Control Panel**
   - **UID Import Card**: Drag-and-drop zone plus multiline paste textarea, “Validate & Add” button, and summary dialog (added/duplicates/invalid).
   - **Run Settings Card**: Spin boxes for daily limit, delay between UIDs, page-load countdown, retry max attempts, retry backoff base; dropdown placeholder for message template.
   - **Engine Controls Card**: Buttons for Start, Pause, Resume, Stop (graceful), Login Only (launch browser without queue). Buttons change enabled state based on engine mode.
3. **Center Dashboard**
   - Daily limit gauge (circular progress) showing Remaining / Limit and countdown to midnight reset.
   - Counter chips: Fresh, In-Progress, Success, Fail (Permanent), Retryable Queue, Duplicates skipped. Chips update live and change colors per status.
   - Current UID card showing the UID, current stage text (“Opening chat…”, etc.), elapsed timer, and next-action countdown progress bar.
4. **Right Panel**
   - Error summary list grouped by error code with counts; selecting a code filters the history table.
   - Last error detail box showing UID, error code, message, attempt number, timestamp, and evidence link if available.
   - Export controls with buttons “Export Today”, “Export All”, “Export Selection”.
5. **Bottom UID Table**
   - Virtualized table columns: Index, UID, Status (chip), Attempts, Last Error Code, Last Updated, Duration, Evidence icon, Notes column (editable optional field).
   - Filter chips for statuses and search box for UID substring.
6. **Toasts & Notifications**
   - Toasts for major events: start/pause/resume, daily limit reached, errors requiring login. Colors match severity.
7. **Keyboard Shortcuts**
   - Ctrl+I (Import dialog), Ctrl+S (Start), Ctrl+P (Pause/Resume toggle), Ctrl+E (Export dialog).

## Workflow Diagram (Textual Flowchart)
1. User launches application → `main.py` shows UID Management dashboard.
2. Operator imports UIDs via file drag/drop or paste → `uid_management_gui` sends lines to `storage.add_uids` → storage normalizes and writes rows → UI refreshes counts/table → summary dialog returned.
3. Operator adjusts settings and presses Start → `task_engine.start()` verifies daily remaining via `profile_manager`.
4. Engine requests next UID: `storage.lease_next_uid` marks record `IN_PROGRESS` with heartbeat timestamp → emits `uid_started` signal → UI highlights row and updates counters.
5. Engine spins up worker thread invoking `fb_worker.send_message_to_uid` with delays/timeouts from config → worker emits progress signals → UI stage text updates.
6. Worker returns `SendResult` → engine interprets status:
   - SUCCESS: `storage.complete_uid` marks row `SUCCESS`, increments attempts, logs event, updates daily counters.
   - FAIL_RETRYABLE: increments attempts, schedules retry with backoff; if attempts exceed max, convert to FAIL_PERM.
   - FAIL_PERM: mark terminal failure and update counters immediately.
7. Engine emits `uid_result` → UI updates table row, counters, and error summaries.
8. Engine enforces cooldown delay before next UID using QTimer countdown updates; once complete, loop returns to step 4 if queue and daily limit allow.
9. If daily limit exhausted, engine transitions to Paused state, emits notification and countdown to reset from `profile_manager`; operator can resume after reset.
10. Stop button or fatal error transitions engine to Stopped, cancels pending timers, and releases `IN_PROGRESS` rows back to retryable with `ENGINE_ABORTED` event.

## Process Logic Explanation
- Queue: Pulls rows where status is `FRESH` or `FAIL_RETRYABLE` with attempts < max; retryables are prioritized if their scheduled retry timestamp is due.
- One-at-a-time: Engine processes a single UID; no parallelism. Each iteration sets status to `IN_PROGRESS` and starts stage timer.
- Countdown timers: UI uses QTimer per stage/delay to display remaining seconds for page load and between UIDs.
- Retries: For `FAIL_RETRYABLE`, calculate next attempt delay = `retry_backoff * 2^(attempt-1)` with cap (e.g., 120s). Schedule using internal queue; UI shows countdown.
- Daily limit: `profile_manager` tracks messages sent today; engine checks remaining before leasing new UID. On terminal statuses, decrement remaining and persist to `daily_counters`.
- Heartbeat: While worker runs, engine updates heartbeat timestamp; if app crashes, startup routine resets stale `IN_PROGRESS` rows to `FAIL_RETRYABLE` with `ENGINE_CRASH`.
- Login-only mode: Start button disabled; login-only triggers worker to open browser without processing queue, allowing manual authentication.

## Error & Exception Handling
- Worker maps browser issues to structured error codes (`UI_NOT_FOUND`, `NAV_TIMEOUT`, `CHAT_BLOCKED`, `AUTH_REQUIRED`, `RATE_LIMITED`, `UNKNOWN`).
- Engine captures worker exceptions and converts to `FAIL_RETRYABLE` with `UNKNOWN` code, logging stack details in `uid_events`.
- UI displays toast for critical errors (e.g., `AUTH_REQUIRED` triggers prompt to use Login Only mode).
- Error summary panel aggregates counts by code; selecting an error shows affected UIDs in table.
- Evidence capture (optional screenshot) stored via worker and linked in table; clicking opens file.
- All errors persisted in `uid_events` and `uids.last_error_*` fields for audits; console logging kept minimal.

## Data Tracking & Reporting
- Counters stored in `daily_counters` (success, fail) and derived metrics: remaining limit, sent today.
- Per-UID data: status, attempts, last error code/message, evidence path, timestamps (first seen, last updated), duration per attempt.
- Import reports: counts of added, duplicates, invalid entries; duplicates flagged but not inserted.
- Event log: each transition recorded (QUEUE, START, STAGE, SUCCESS, FAIL, RETRY_SCHEDULED) for debugging.
- UI displays live chips and table reflecting storage queries refreshed via signals; exports generate CSV with relevant columns for selected timeframe.

## Performance & Optimization Notes
- Use Qt’s signal/slot architecture to avoid blocking UI; worker runs in separate `QRunnable` or `QThread`.
- Employ a virtualized table (QTableView with custom model) to handle thousands of rows without lag.
- Limit heavy database writes by batching event inserts within transactions and using indexed columns (`status`, `profile_id`, `normalized_uid`).
- Countdown timers implemented via lightweight QTimer updates rather than busy loops.
- Avoid repeated DOM lookups by caching selectors and using deterministic JS execution in `fb_worker`.

## Scalability Plan
- Profiles table already supports multiple rows; future UI can add profile selector and per-profile controls.
- Storage methods accept `profile_id`, enabling isolated queues and daily counters per profile.
- Task engine can instantiate per-profile instances or manage a profile rotation schedule (round-robin) while reusing existing worker and UI components.
- Evidence and configuration directories parameterized to allow per-profile subfolders and settings.
- Message templates stored in database or config to support per-profile content.

## Step-by-Step Coding Implementation Plan
1. Create configuration loader (`config/defaults.json`, `config/loader.py`) and ensure runtime settings accessible globally.
2. Implement `storage.py` with migrations for `profiles`, `uids`, `uid_events`, `daily_counters`, plus helper methods (import, lease, complete, export).
3. Build `profile_manager.py` to load/create default profile, compute daily reset, and emit limit updates.
4. Stub `fb_worker.py` adapter to wrap existing automation and define `SendResult` along with progress signals.
5. Develop `task_engine.py` with state machine, timers, retry scheduling, and signal definitions.
6. Construct `uid_management_gui.py` to lay out header, controls, dashboard, error panel, and table; connect buttons to engine/profile/storage APIs.
7. Integrate import workflows (drag-drop, paste) with validation summaries and UI refresh.
8. Wire engine signals to UI updates (counters, progress cards, table model) and handle pause/resume/stop logic.
9. Implement export functionality and evidence links in the table.
10. Finalize login-only mode, toast notifications, keyboard shortcuts, and accessibility polish.

## Testing & Debugging Plan
- **Unit Tests**: UID normalization, duplicate detection, lease/complete cycle, retry backoff calculation, daily counter rollover at Asia/Kathmandu midnight.
- **Integration Tests**: Import sample file with duplicates/invalid lines; run engine through success/fail scenarios using mocked worker responses; verify UI counters via model inspection.
- **Manual Dry Runs**:
  - Start with empty queue → ensure Start disabled until UIDs exist.
  - Import 15 UIDs; mark 10 as success via worker stub, 5 as missing input; confirm they move to terminal states and are excluded next day until reset.
  - Simulate `AUTH_REQUIRED` error to ensure engine pauses and UI prompts login.
  - Reach daily limit to confirm auto-pause and countdown to reset.
  - Crash simulation: mark an `IN_PROGRESS` row and restart app; verify it becomes retryable with `ENGINE_CRASH` event.
- **Debugging Tools**: Structured logging via Python’s logging module (debug mode), UI developer console to inspect table model data, optional screenshot review for failures.

