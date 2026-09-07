# Startup performance

This page tracks the work required to make StillPoint open consistently and to
keep startup regressions visible. It complements `page-load-performance.md`,
which begins after a page-open action.

## Baseline

An offscreen Linux measurement on 2026-09-07, before opening a vault, produced:

| Phase | Time |
| --- | ---: |
| Desktop entry-module import | 772 ms |
| Embedded API import and startup | 1,119 ms |
| `QApplication` creation | 24 ms |
| `MainWindow` construction | 1,722 ms |
| Total before vault setup | 3,637 ms |

This is a diagnostic baseline rather than a release target; packaged builds,
operating systems, font caches, enabled panels, and vault contents differ. A
profile of `MainWindow` construction identified about 2,500 global-config
reads/JSON parses caused by repeated theme lookups. Caching those reads in an
experiment reduced window construction from about 1.7 seconds to about 1.0
second in the same environment.

The first-pass no-vault rerun produced 740 ms for entry imports, 994 ms for the
embedded API, 6 ms for `QApplication`, and 1,023 ms for `MainWindow`: 2,763 ms
total before vault setup. That is about 873 ms (24%) faster than the baseline,
with window construction itself about 41% faster. Multiple real launches are
still needed before treating those figures as stable.

A subsequent real local-vault launch reached its first event-loop turn in
8,286 ms. The embedded API took 1,115 ms, `MainWindow` construction took 993 ms,
and the still-aggregated vault setup took 5,288 ms. That trace motivated the
vault-setup subspans below.

The next Homebase launch reached its first event-loop turn in 9,373 ms. Its
embedded API took 1,154 ms, `MainWindow` construction took 984 ms, and
`startup.vault_setup` took 6,383 ms. The 15 named `vault.setup.*` phases totaled
only 1,390 ms. The largest named phases were Homebase profile/services at 563
ms, theme application at 417 ms, panel setup at 246 ms, and navigation at 57
ms. This leaves approximately 4,993 ms outside the currently measured setup
phases, so the trace does not justify optimizing any named phase yet.

Startup optimization is paused because the application is responsive once
open and the main interaction paths meet their latency targets. If startup
becomes a priority, first measure the entry/exit work around `_set_vault()` and
the final `_pop_paint_block()` call. Only optimize the phase that accounts for
the unexplained interval.

## First implementation pass

- [x] Cache the parsed global config while its path, mtime, and size are
  unchanged; update the cache after StillPoint writes the file.
- [x] Resolve the effective theme only once per theme-cache lookup.
- [x] Remove the fixed 100 ms sleep after the embedded API socket is accepting
  connections.
- [x] Apply a saved Homebase profile once during vault selection rather than a
  second time in `startup()`.
- [x] Run the Homebase `/auth/me` permission lookup outside the Qt UI thread and
  discard results from an obsolete vault generation.
- [x] Add structured phase timings for imports, bootstrap, embedded API,
  `QApplication`, window construction, vault setup, window show, first event-loop
  turn, and vault-tree population.

## Capturing a trace

The human-readable phase log is useful for a quick run:

```bash
SP_LOG_STARTUP=1 python -m sp.app.main
```

For JSONL records that can be compared across runs:

```bash
SP_LOG_PERFORMANCE=1 \
SP_PERFORMANCE_PROFILE_PATH=/tmp/stillpoint-startup.jsonl \
python -m sp.app.main
```

Relevant span names are:

- `startup.module_import`
- `startup.bootstrap`
- `startup.embedded_api`
- `startup.qt_application`
- `startup.main_window`
- `startup.vault_setup`
- `startup.window_show`
- `startup.first_event_loop`
- `vault.tree_population`

Vault setup is decomposed further into:

- `vault.setup.previous_state`
- `vault.setup.config_context`
- `vault.setup.features_ai`
- `vault.setup.lock_and_task_reset`
- `vault.setup.api_select`
- `vault.setup.root_index_state`
- `vault.setup.index_context`
- `vault.setup.profile_services`
- `vault.setup.preferences_history`
- `vault.setup.theme`
- `vault.setup.navigation`
- `vault.setup.index`
- `vault.setup.panels`
- `vault.setup.geometry`
- `vault.setup.services`

These are nested inside `startup.vault_setup`; `vault.setup.navigation` also
contains the more specific `vault.tree_population` span. Small differences
between their sum and the parent can come from caller work and logging overhead,
but the latest roughly five-second difference is material and must be measured
at the outer `_set_vault()` and paint-release boundaries.

Capture at least five cold process starts and five warm starts for the same
vault. Compare median, p95, and worst-case values; do not draw conclusions from
one launch.

## Possible next passes (only if startup becomes user-visible)

1. Add spans immediately before and after `_set_vault()`, plus a span around the
   final editor paint-block release, to account for the current setup gap.
2. Re-profile imports and `MainWindow` after this pass. Lazy-import optional AI,
   RAG, OCR, diagram, and WebEngine dependencies that are not needed for the
   initial visible workspace.
3. Construct only the active right-side panel at startup. Instantiate Calendar,
   Link Navigator, Map, Attachments, and AI panels when first selected, while
   preserving saved tab selection and signal wiring.
4. [x] Split vault setup into stable spans for config/index context, Homebase
   services, tree/model construction, panel hydration, history/preferences,
   theme application, geometry, and services. Initial page rendering remains
   represented by the page-load trace because it is scheduled after the window
   is shown.
5. Delay non-visible panel refreshes and optional background services until
   after the first event-loop turn. Every delayed result must be scoped to a
   vault/page generation.
6. Add a repeatable subprocess benchmark and a generous regression threshold to
   CI once representative Linux and Windows baselines are known.

## Guardrails and targets

- Preserve the embedded API: it remains useful for the web server, printing,
  Excalidraw, integrations, and a single application boundary.
- Never mutate Qt objects from startup worker threads.
- A stale Homebase or vault-load result must not affect the current vault.
- Config caching must notice external edits without requiring a restart.
- Initial target: under 2.5 seconds to the first event-loop turn on the baseline
  development machine, excluding an interactive vault chooser.
- Follow-up target: under 1.5 seconds for a warm local-vault launch after lazy
  imports and panels are implemented.

Update the baseline table and checklist after each measured slice. Keep changes
small enough that correctness regressions can be attributed and reverted.
