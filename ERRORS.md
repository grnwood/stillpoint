**Issue Reporting + Crash Handling Summary (for docs)**

**Goal**
Provide users a clear way to report file‑operation errors and post‑crash diagnostics to GitHub, with prefilled context (OS, version, exception, stacktrace).

**GitHub Issue Reporting**
- Issue URL template is in `sp/__init__.py`:
  - `GITHUB_OWNER`, `GITHUB_PROJECT`, `GITHUB_ISSUE_URL`.
- UI builds a prefilled GitHub issue URL with:
  - Title: `Stillpoint version <VERSION> Issue`
  - Body fields:
    - `OS level`
    - `version`
    - `exception`
    - `stacktrace`
    - `User notes` (blank for user)
- When file‑related API errors occur, the UI shows a dialog with:
  - A summary message.
  - Detailed exception + stacktrace.
  - “Report Issue” button that opens the prefilled GitHub issue URL.

**Structured File‑Error Payloads**
- File‑related API endpoints return structured error details:
  - `message`, `exception`, `traceback`
- UI detects this structure and triggers the issue‑report dialog.
- File‑op preflight failures (rename/move/delete) are also wrapped into the structured payload so they trigger reporting.

**Crash Handling (faulthandler)**
- On startup, `faulthandler` logs to a temp file (e.g., `stillpoint-faulthandler.log`).
- The path is exported via `STILLPOINT_FAULTHANDLER_LOG` (internal).
- On next startup, the app checks the log:
  - If it’s new (not previously seen), it prompts the user to report.
  - It uses the same GitHub issue flow, with the log tail as “stacktrace”.
- A marker `~/.stillpoint/last-crash.json` prevents repeated prompts for the same log.
- If the user clicks “Report Issue,” the faulthandler log is cleared.

**Crash Test (optional debug)**
- A `Debug: Crash (Segfault)` menu item is available only when:
  - `STILLPOINT_ENABLE_CRASH_TEST=1`
- This forcibly crashes the process to validate the post‑crash prompt.

**Env Vars**
- `STILLPOINT_ENABLE_CRASH_TEST=1` → enable crash test menu.
- `SP_DISABLE_FAULTHANDLER=1` → disable faulthandler logging.

**User Experience Flow**
1. File operation fails → error dialog with Report Issue.
2. App hard‑crashes (e.g., segfault) → on next launch, user is prompted to report.
3. Reporting opens a prefilled GitHub issue with system and crash context.