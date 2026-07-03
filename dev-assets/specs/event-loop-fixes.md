# Event Loop CPU Churn Fixes

## Background

StillPoint had an idle CPU churn issue in the desktop app. External tracing showed the Qt main event loop being woken continuously. `strace` showed repeated writes to an event-loop wakeup fd followed by immediate `poll` wakeups and reads. `gdb` showed the main thread inside PySide6/Shiboken Qt event filtering, which pointed to app-side posted Qt events, timers, signals, or UI mutations.

## Diagnostics Added

Temporary opt-in diagnostics were added behind `SP_LOG_EVENT_LOOP=1`:

- `QApplication.notify` counts per second by event type and target.
- Application-wide event-filter sampling by event type and target.
- `QTimer` creation, interval, start, and `singleShot` logging with caller locations.
- UI mutation counting for high-frequency methods such as `update`, `repaint`, `setText`, `setStyleSheet`, `resize`, `show`, `hide`, `setVisible`, and `setIcon`.
- Existing fd target logging remained available for checking whether the watched fd was an eventfd, X11 socket, or another Qt wake source.

Useful runtime flags:

```bash
SP_LOG_EVENT_LOOP=1 SP_EVENT_LOOP_SAMPLE_EVENTS=1 ./tools/sv.sh
```

Optional flags:

```bash
SP_EVENT_LOOP_DUMP_FDS=1
SP_EVENT_LOOP_PROBE_UI_METHODS=0
SP_EVENT_LOOP_LOG_TIMERS=0
```

## Findings

The first diagnostic captures showed a clear posted-event storm:

- `MarkdownEditor._refresh_hr_selections` repeatedly scheduled `QTimer.singleShot(0, ...)`.
- `MarkdownEditor._queue_post_load_repaint` repeatedly scheduled very short repaint retries.
- Idle `QApplication.notify` samples were dominated by `MetaCall`, `Timer`, and `DeferredDelete` events.
- The top repeated call sites were in `sp/app/ui/markdown_editor.py`.

After the first fix, a second idle vault-picker capture showed the rate was much lower, but there was still a steady idle loop. The root cause was that a fresh editor had both:

```text
_load_in_flight_token == 0
current_load_token() == 0
```

The paint guard treated that as an active load and kept rearming post-load repaint and HR refresh retry timers even though no page was loading.

## Fixes Implemented

### Markdown HR Refresh Retry

Changed HR refresh retries from recursive zero-delay `QTimer.singleShot(0, ...)` calls to a coalesced single-shot retry timer.

Current behavior:

- If mutations are blocked, schedule one retry.
- Do not schedule more retries while a retry is already pending.
- Retry after a modest delay instead of immediately waking the event loop.
- Keep the current load token so stale retries are dropped safely.

### Post-Load Paint Guard

Changed post-load repaint scheduling from 1ms retries to frame-ish delays.

Current behavior:

- Normal deferred repaint minimum delay is 16ms.
- If a real load is still in flight, retry at 50ms.
- A load is considered in flight only when `_load_in_flight_token != 0`.
- A fresh editor with token `0` no longer arms the post-load paint guard forever.

### Worker-To-UI Coalescing

Reduced avoidable wakeups from existing queue/stream timers:

- AI chat stream and condense flush timers: 40ms to 100ms.
- Task remote-result drain timer: 75ms to 200ms.
- Calendar remote-result drain timer: 75ms to 200ms.
- Local filesystem refresh-result polling: 50ms to 100ms while active.

## Verification

Focused tests passed after the fixes:

```text
99 passed
```

Coverage includes:

- HR overlay behavior.
- HR retry coalescing while mutations are blocked.
- Fresh editor paint guard inactive when no page load exists.
- Page-load paint guard behavior.
- Markdown link rendering.
- Calendar click path behavior.
- Local filesystem refresh behavior.

Manual diagnostic reruns showed:

- No recurring `QTimer.singleShot delay_ms=0` storm from `MarkdownEditor`.
- Idle vault-picker baseline around 18 events/sec, mostly expected timer events.
- Startup, dialog, and page-load bursts still occur, but they settle instead of continuing indefinitely.
- Python process CPU usage no longer sits around the previous idle churn level.

## Current Status

The main Qt event-loop CPU churn appears solved.

The current logs look like normal Qt behavior:

- High event rates during startup, theme application, dialogs, page changes, and window activation.
- Low event rates when idle.
- No single call site continuously streaming posted events while idle.

## Future Work

These are optional follow-ups, not blockers:

- Make idle queue-drain timers sleep completely when no work is in flight, especially task/calendar remote-result timers.
- Consider moving the event-loop diagnostics behind an even more explicit development-only entry point before release.
- Add a small diagnostic summary parser for `loop-data.txt` that reports top timer call sites and idle notify rates.
- Review repeated theme/style bursts during page/dialog transitions for possible cosmetic performance wins.
- Re-run profiling with event-loop diagnostics disabled to measure the final production idle baseline.
