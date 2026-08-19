# Page-load performance investigation

StillPoint page loading crosses API/file I/O, Markdown conversion, Qt document
population, syntax highlighting, inline-image decoding, indexing, and right-panel
refreshes. Optimizing only the HTTP request or Markdown parser can therefore hide
the actual stall. Measure the entire click-to-next-event-loop cycle first.

## Capture a full trace

Run the desktop application with structured performance logging enabled:

```bash
SP_LOG_PERFORMANCE=1 \
SP_PAGE_PROFILE_PATH=/tmp/stillpoint-page-load.jsonl \
python -m sp.app.main
```

Open a representative page at least five times: once cold, three times warm, and
once after opening other pages. The JSONL contains every existing page-open mark,
including API read, Markdown normalization/conversion, document population,
highlighting/image hydration, indexing, right-panel work, and the next Qt event
loop turn. Each completed span also reports wall time, process CPU time, estimated
wait time, and the five slowest steps. Logging is opt-in and output failures are
non-fatal.

For OS/native attribution, capture the same interaction with a sampling profiler:

* Linux: `py-spy record --native -o page-load.svg -- python -m sp.app.main`
* Linux Qt/font/file activity: `strace -f -ttT -o page-load.strace python -m sp.app.main`
* Windows: Windows Performance Recorder/Analyzer with CPU Sampling, Disk I/O, and
  File I/O enabled.
* macOS: Instruments Time Profiler plus File Activity.

Keep the JSONL timestamps beside the native trace. A large
`unattributed_wait_ms` or delayed `qt loop resumed post-open` usually means native
painting/layout, image/font I/O, lock contention, or other event-loop work rather
than Python CPU.

## Optimization options, in recommended order

1. **Move unrelated panel refreshes behind first paint.** Index refresh, task/link
   panels, attachments, maps, calendar sync, config persistence, and tree syncing
   currently remain on the synchronous open path. Schedule immutable work after a
   zero-delay Qt turn, then apply it only if its captured page-load generation is
   still current. This offers the best low-risk perceived-latency win.
2. **Hydrate images after text is editable.** Initially leave image Markdown (or a
   fixed-size placeholder), then decode/scale raster images in a worker and insert
   results in small GUI-thread batches. Decode to the displayed size, cache by
   absolute path + mtime + target width, and prioritize images in/near the viewport.
   `QTextDocument` mutations must remain on the GUI thread.
3. **Defer syntax highlighting in bounded batches.** Populate plain display text,
   permit first paint, then highlight visible blocks followed by idle batches.
   Cache block results. Do not detach or replace the live document after it becomes
   interactive; stale highlighter callbacks are a native-lifetime risk.
4. **Reduce document/layout work.** Profile `_to_display`, `setPlainText`,
   `rehighlight`, and image insertion separately. Avoid incremental text insertion
   unless measurements prove it wins: repeated edits can cause repeated document
   layout. If large heading-heavy pages are dominated by highlighting, optimize
   highlighter rules/caches before attempting document virtualization.
5. **Prefetch likely navigation targets.** Read and normalize adjacent/history
   pages in a bounded background cache. Never create or touch Qt objects there.
   Validate file revision/mtime before using a prefetched result.
6. **Virtualize only as a later redesign.** A viewport-only document can yield the
   largest extreme-page improvement but complicates selection, search, cursor
   restoration, undo, image positions, and Markdown round-tripping. It has the
   highest correctness and crash risk.

## Non-negotiable crash-safety contract

Every deferred callback must carry the editor's monotonic load token and return
before touching state when the token is stale. Workers may produce Python strings
or detached image bytes only; they must not own, mutate, or destroy Qt documents,
widgets, cursors, formats, or pixmaps. Keep strong ownership of worker results until
the GUI-thread handoff completes, cancel timers/work on teardown, and retain the
existing in-flight/post-load paint guards.

Ship each optimization behind an environment/feature flag first. Add stress tests
that rapidly alternate large image-heavy, heading-heavy, empty, and missing-image
pages while forcing paints and closing/reopening windows. Compare cold/warm p50,
p95, worst-case next-event-loop latency, and crashes across at least Windows and
Linux before making a deferred path the default.

## Suggested acceptance targets

For the agreed representative corpus, target p95 text-editable latency below
150 ms for local warm loads and below 300 ms for local cold loads, with optional
panels and below-fold images hydrating afterward. Treat zero crashes, stale-page
updates, content loss, or image/heading drift during a 10,000-navigation stress run
as a release gate—not as a performance tradeoff.
