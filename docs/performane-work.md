# StillPoint performance and long-running stability work

This page is the working backlog for making StillPoint feel immediate during
editing, navigation, saving, and long sessions. Changes should favor bounded
work, explicit invalidation, and stable Qt object ownership over speculative
concurrency.

## Stopping point (2026-09-07)

The current performance pass is intentionally complete. In the latest real-use
trace, 18 page loads reached editable text at p50 31.8 ms, p95 84.3 ms, and a
worst case of 84.3 ms. Active-page chicklet updates were p50 2.6 ms and p95
8.4 ms; history reconciliation was p50 6.6 ms; and 115 sampled
keystroke-to-paint spans were p50 5.2 ms and p95 19.1 ms. The application also
feels crisp in normal use.

Do not continue tuning these paths solely to improve benchmark numbers. Resume
performance work when a user-visible delay, stability problem, or repeatable
regression appears. Preserve a before/after trace and prefer one bounded change
over a broad rewrite.

The two measured candidates worth retaining for later are:

- Link Navigator work can dominate secondary hydration when several visible
  link panels refresh the same page. One trace contained a 591 ms refresh; a
  different page performed about 770 ms of repeated link refresh work. If this
  becomes noticeable, coalesce same-path requests, refresh only visible/current
  panels, and share computed link data between panels.
- A Homebase startup reached the first event-loop turn in 9.37 seconds, of which
  6.38 seconds was attributed to `startup.vault_setup`. The named vault setup
  phases accounted for only 1.39 seconds. Before changing startup behavior, add
  boundary spans around `_set_vault()` and the final editor paint-block release
  to locate the remaining roughly 4.99 seconds.

## Current baseline

An offscreen editor benchmark found that an explicit full-document syntax
highlight costs roughly 80 ms for 1,000 simple lines and 190 ms for 2,500 simple
lines. On link- and heading-heavy content, the same pass ranged from about 1.7
seconds for 1,000 lines to 4.9 seconds for 2,500 lines. Document population was
much smaller in the same samples, so redundant highlighting is the clearest
first target.

The code audit also found three sources of avoidable UI churn:

- vi cursor selections were rebuilt during paint and update events;
- focus changes reapplied large stylesheets to every primary pane;
- every save refreshed tasks, links, calendars, and detached panels even when
  only ordinary prose changed.

## First implementation slice

- [x] Trust `QSyntaxHighlighter`'s changed-block invalidation when line count
  changes; do not force an additional whole-document pass.
- [x] Stop rebuilding the vi cursor selection from paint/update events and cache
  the vault accent used by its colors.
- [x] Cache focus-pane styles and call `setStyleSheet()` only when the effective
  style actually changes.
- [x] Classify saved-content metadata changes and refresh only affected task,
  link, tag, and calendar views.
- [x] Avoid the duplicate right-link-panel refresh during deferred page
  hydration.
- [x] Add focused regression tests for invalidation and repaint behavior.

## Completed and deferred slices

### 1. Measure real interaction latency

Use the existing page-load JSONL tracing described in
`docs/page-load-performance.md`. Lightweight spans now cover save finalization
and indexing, task mutation/reload, tag/link/task refreshes, heading and
horizontal-rule scans, and editor keystroke-to-paint-start latency.
Track p50, p95, and worst-case duration on representative small, large,
syntax-heavy, image-heavy, and task-heavy pages.

Capture both page-load and interaction spans in one file:

```bash
SP_LOG_PERFORMANCE=1 \
SP_PERFORMANCE_PROFILE_PATH=/tmp/stillpoint-performance.jsonl \
python -m sp.app.main
```

Records with `type: performance_span` contain a stable `name`, `duration_ms`,
and relevant page/document fields. The currently emitted span names are:

- `editor.keystroke_to_paint_start`
- `editor.heading_outline_scan`
- `editor.horizontal_rule_scan`
- `save.finalize`
- `save.index_page`
- `task_mutation.to_editor_reload`
- `panel.tags.load`
- `panel.tags.results_refresh`
- `panel.links.refresh`
- `panel.tasks.refresh`
- `panel.tasks.data`
- `panel.tasks.tree_build`
- `top_nav.active_chicklets`
- `top_nav.history_rebuild`
- `navigation.focus_handoff`
- `save.metadata_diff`
- `save.affected_panels`
- `save.history_persistence`
- `save.undo_snapshot`

Page-load summaries are single-shot. Active history/bookmark chicklet updates
are deferred until after the guarded first paint, history rebuilding no longer
pollutes the API-read measurement, and unchanged chicklet styles are not
reapplied or explicitly repolished.

### 2. Bound remaining document-wide editor work (implemented)

Heading-outline refresh now skips body edits that cannot alter cached headings
or their positions. Horizontal-rule decoration updates only the edited blocks
and immediate neighbors, while explicit page-load/theme refreshes retain the
full correctness pass. Automatic edit invalidations share one trailing-edge
idle callback. All live `QTextDocument`, cursor, and format work remains on the
GUI thread.

### 2a. Make navigation and focus updates incremental (implemented)

The recent-history strip now reconciles its bounded set of buttons, retaining
unchanged widgets instead of deleting and recreating the entire strip on every
page. Back/forward navigation no longer performs a redundant pre-load history
refresh, and active styling is limited to the previously active and newly
active paths. Scroll geometry is updated once when membership changes.

Loading markdown no longer transfers keyboard focus. The navigation source
owns that decision explicitly, preserving existing Enter-to-editor and
Shift+Enter-to-pane behavior while avoiding redundant synchronous focus events
during page rendering.

### 3. Make save and indexing incremental (deferred)

The first slice prevents unnecessary panel rebuilds, but indexing still parses
the saved page. A later slice can compute one reusable metadata snapshot per
save, pass it to the index update, and refresh panels from compact change events.
Coalesce rapid autosaves so only the newest generation can publish UI updates.

### 4. Move blocking I/O off interaction paths (deferred)

Page reads, expensive image decoding, and remote requests are candidates for
bounded background work. Results must carry a page/load generation and be
dropped when stale. Workers must never own or mutate Qt GUI objects.

### 5. Cap long-session memory (deferred)

Audit caches, detached panes, undo snapshots, image resources, timers, signal
connections, and closed mode windows. Give every cache a size/byte limit, clear
vault-scoped state on vault change, and add a navigation stress test that records
RSS and Python heap growth over thousands of page switches.

## Performance guardrails

- Local warm page loads: p95 text-editable latency below 150 ms.
- Local cold page loads: p95 text-editable latency below 300 ms.
- Ordinary prose saves: no task, link, tag, or calendar model rebuild.
- No synchronous full-document work on a normal keystroke unless the operation
  explicitly requires the whole document.
- No stale deferred callback may update a newer page.
- No crashes, content loss, focus loss, or unbounded memory growth in a
  10,000-navigation stress run.

Performance changes should ship in small slices with correctness tests and a
before/after trace. A visually smoother result is useful only when editing,
selection, history, task state, and index correctness remain intact.
