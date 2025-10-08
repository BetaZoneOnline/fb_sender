# UID Management & Messaging Controller — Phase 1 Specification

## Feature Summary
- Deliver a PyQt-based control center that ingests a Facebook UID list, processes each UID sequentially through the embedded browser automation, and enforces per-profile daily message limits without duplicating sends.
- Provide live operational visibility (progress, counters, countdowns) with operator controls for importing data, starting/stopping the engine, and exporting historical results.

## System Components
- `main.py`: Application entrypoint that loads the UID management dashboard before any automation starts.
- `app/uid_management_gui.py`: Qt widgets for the dashboard layout, controls, status counters, timers, and tables; mediates user interactions.
- `app/task_engine.py`: Core state machine that leases UIDs from storage, manages retries/delays, and dispatches work items to the browser worker.
- `app/automations/fb_worker.py`: Adapter that wraps the Facebook webview automation, exposes deterministic `send_message_to_uid` behavior, and emits granular progress signals.
- `app/storage.py`: SQLite data-access layer with migrations for profiles, UIDs, event logs, and daily counters; provides import, leasing, completion, and export helpers.
- `app/profile_manager.py`: Tracks active profile metadata (nickname, timezone, limits), calculates daily reset countdowns, and updates the UI with remaining capacity.
- `app/config/defaults.json` & `app/config/loader.py`: Configuration defaults and loader utilities for delays, limits, retry policies, and evidence paths.
- `app/message_provider.py`: Central place to retrieve message templates or static text for outgoing messages (placeholder for future template designer).
- `assets/` directory: Icons and visual assets referenced by the UI (play/pause, status chips, export, errors).

## UI/UX Design Plan
- **Header Bar**: Displays app title, editable profile nickname, engine-state badge (Idle, Running, Paused, Stopped), and a live clock showing Asia/Kathmandu time.
- **Left Panel — Import & Settings**:
  - Drag-and-drop area and multiline paste box for UID ingestion with `Validate & Add` button that opens an import summary modal.
  - Run-settings form with spin boxes for daily limit, delay between UIDs, page-load countdown, retry attempts, and retry backoff base; optional dropdown placeholder for message templates.
  - Engine control buttons: `Start`, `Pause`, `Resume`, `Stop`, and `Login Only` (opens browser without queue processing).
- **Center Panel — Live Dashboard**:
  - Daily limit card with circular progress indicator (`Remaining / Limit`) and countdown timer to next reset.
  - Counter row for Today’s metrics: Success ✅, Fail ❌, Retryable 🔄, In-Progress ⏳, Fresh 🆕, Duplicates 🚫.
  - Current UID card showing the UID, current stage text (e.g., “Opening chat…”), next action countdown, and elapsed time since start.
- **Right Panel — Errors & History**:
  - Error summary list grouped by error code with counts; selecting an error filters the history table.
  - Last error detail card showing error code, message, attempt number, timestamp, and evidence link (screenshot) if available.
  - Export buttons for CSV (Today / All / Selected rows).
- **Bottom Panel — UID Table**:
  - Virtualized table with columns: Index, UID, Status, Attempts, Last Error, Last Updated, Duration, Evidence (icon), Notes (editable).
  - Status filter chips for quick filtering (FRESH, IN_PROGRESS, SUCCESS, FAIL_PERM, FAIL_RETRYABLE).
  - Row actions: `Retry Now` (for retryable statuses), `Mark Permanent Fail`, `View Events` (modal timeline).
- **Visual Language**: Success states in green, permanent failures in red, retryable in amber, in-progress with animated blue pulse, fresh grey. Countdown timers update every second. Toast notifications surface major state changes (start, pause, limit reached, export complete).
- **Accessibility**: Keyboard shortcuts (`Ctrl+I` import, `Ctrl+S` start, `Ctrl+P` pause/resume, `Ctrl+E` export) and descriptive aria labels for controls.

## Workflow Diagram (Textual Flowchart)
1. User launches `python main.py` → Dashboard loads (engine idle).
2. User imports UIDs (paste or file) → UI validates, normalizes, deduplicates, persists to SQLite → Import summary modal appears.
3. User clicks `Start` → Engine checks daily remaining capacity via ProfileManager.
4. If remaining = 0 → Engine auto-pauses, UI shows “Daily limit reached” banner with reset countdown.
5. If remaining > 0 → TaskEngine leases next eligible UID (`FRESH` or retryable under attempt limit) and marks it `IN_PROGRESS`.
6. Engine emits `uid_started` → UI highlights row and updates Current UID card.
7. TaskEngine dispatches FBWorker in background thread → Worker emits stage progress (e.g., “Navigating”, “Waiting DOM”).
8. Worker returns `SendResult` (SUCCESS / FAIL_RETRYABLE / FAIL_PERM) with metadata (error code/message, evidence path).
9. TaskEngine persists result, updates counters/daily limit, logs event, and emits `uid_result`.
10. UI updates counters, removes highlight, appends to history; if SUCCESS or PERM fail, decrement remaining limit.
11. Engine waits configured cooldown (countdown displayed) before leasing next UID; on Pause, timers stop; on Resume, processing continues.
12. If queue empty → Engine transitions to Idle and notifies UI.
13. Errors trigger structured notifications, logging, and optional screenshot capture; manual retry available via table actions.

## Process Logic Explanation
- Maintain a deterministic queue that selects UIDs in FIFO order from `FRESH`, prioritizing retryable entries that have not exceeded `max_attempts` and remain within freshness window.
- Before each lease, consult ProfileManager to enforce daily limit (counting terminal results only). If limit reached, set engine state to Paused and surface reset countdown.
- For each leased UID:
  - Transition to `IN_PROGRESS` atomically with heartbeat timestamp to recover from crashes.
  - Launch FBWorker in dedicated `QRunnable`/`QThreadPool` slot to avoid blocking UI.
  - Worker executes navigation, input detection, message send, and returns normalized `SendResult` without raising exceptions.
  - TaskEngine consumes result: increments attempts, records error data, sets final status (`SUCCESS`, `FAIL_RETRYABLE`, `FAIL_PERM`), and schedules retry with exponential backoff for retryable outcomes (up to `max_attempts`).
  - Countdown timers for page load, action delays, and cooldowns are handled via `QTimer` so the UI remains responsive.
- On Pause: stop leasing new UIDs and freeze countdowns; running worker finishes current UID. On Resume: resume timers and continue leasing. On Stop: gracefully halt after current UID completes, resetting engine state to Idle.
- On application restart: reclaim orphaned `IN_PROGRESS` rows older than heartbeat threshold by converting them to `FAIL_RETRYABLE` with error code `ENGINE_CRASH`.

## Error & Exception Handling
- FBWorker maps all automation issues to predefined error codes (e.g., `UI_NOT_FOUND`, `NAV_TIMEOUT`, `CHAT_BLOCKED`, `AUTH_REQUIRED`, `RATE_LIMITED`, `UNKNOWN`).
- TaskEngine catches worker exceptions, converts to `FAIL_RETRYABLE` with code `WORKER_EXCEPTION`, and logs structured event.
- UI displays error summaries grouped by error code, with toasts showing concise messages and detail panels including timestamps and attempt counts.
- For retryable errors, UI shows scheduled retry countdown; for permanent errors, UI marks row red and removes from active queue.
- Global issues (authentication required, limit reached) raise prominent banners and pause the engine automatically.
- Optional screenshot capture on failure stored under evidence directory; UI provides clickable icon to open path.

## Data Tracking & Reporting
- Track per-UID fields: status, attempts, last error code/message, last evidence path, first seen, last updated.
- Maintain UID event log with structured JSON payload for timeline view (queue, start, stage updates, result, retry scheduled).
- Daily counters per profile: successes, permanent failures, retryable outcomes processed, duplicates skipped during import, remaining limit, time to reset.
- UI dashboard surfaces live totals, daily remaining, and stage progress; history table supports filtering and exporting.
- CSV export includes normalized UID, status, attempts, last error, timestamps, duration, and evidence link.

## Performance & Optimization Notes
- Use SQLite transactions for batch imports and UID leasing to guarantee atomic state changes.
- Employ `QThreadPool` with bounded workers (typically 1) to ensure sequential processing while keeping the UI thread free.
- Leverage `QTimer` for countdown updates instead of blocking `sleep` calls; store next-action timestamps to resume accurately after pause.
- Implement exponential backoff with cap to avoid rapid retries while still recovering from transient issues.
- Use virtualized Qt table views to handle large UID lists without rendering lag; update models incrementally.

## Scalability Plan
- Design storage schema and profile manager to support multiple profiles by adding profile selection UI and filtering UIDs by profile ID.
- Extend TaskEngine to handle multiple concurrent profile queues with round-robin scheduling; each profile maintains its own daily counters and limits.
- Store profile-specific cookies/credentials in a separate table; allow UI to switch active profile and load corresponding counters.
- Modularize FBWorker to accept profile context (cookie path, message template) so new profiles reuse same automation core.

## Step-by-Step Coding Implementation Plan
1. **Database Layer**: Implement `storage.py` migrations for profiles, uids, uid_events, daily_counters; add helper methods for imports, leasing, completions, exports.
2. **Configuration Loader**: Create `config/defaults.json` and `config/loader.py` for loading runtime settings and ensuring directories (evidence, database) exist.
3. **Profile Manager**: Build profile manager to initialize default profile, track nickname/limits, and compute daily reset timers.
4. **Message Provider**: Stub message provider returning current template text; keep ready for future templating features.
5. **FBWorker Adapter**: Wrap existing automation logic into deterministic `SendResult` return value with progress signals and error-code mapping.
6. **Task Engine**: Develop queue selection, leasing, retry scheduling, delay handling, and signal emissions; integrate timers and heartbeat recovery.
7. **UI Construction**: Build `uid_management_gui.py` layout (panels, cards, tables), bind controls to TaskEngine/ProfileManager signals, and implement import/export dialogs.
8. **Main Entrypoint**: Update `main.py` to bootstrap configuration, storage, profile manager, task engine, and launch the UID dashboard.
9. **Polish & Assets**: Add icons, status styling, toasts, keyboard shortcuts, and ensure responsive layouts.
10. **Optional Enhancements**: Hook screenshot capture, timeline modals, and message template dropdown as needed.

## Testing & Debugging Plan
- **Unit Tests**: Validate UID normalization, duplicate detection, leasing atomicity, retry backoff calculations, daily counter rollover at Asia/Kathmandu midnight, and status transitions.
- **Integration Tests**: Simulate imports with duplicates/invalid entries, run engine through success and error paths, verify UI updates (using Qt Test or manual QA) and ensure pause/resume/stop behaviors work.
- **Dry-Run Scenarios**:
  - Process list of UIDs with mixed results (success, missing composer, blocked) and confirm counters/logs.
  - Hit daily limit by configuring low limit; ensure engine pauses and countdown displays.
  - Trigger retryable errors to observe backoff countdown and eventual permanent classification after max attempts.
  - Restart application during processing to test heartbeat recovery of `IN_PROGRESS` UIDs.
- **Debugging Tools**: Enable verbose logging to structured file (JSON) for post-mortem, provide developer toggles for mock FBWorker responses, and verify screenshot captures where applicable.

