
# Fixes
• Implemented the lower-wake filesystem path.

  Changed:

  - Removed active recursive QFileSystemWatcher usage; local vaults now use coarse snapshot scans in sp/app/ui/main_window.py:5731.
  - Added configurable scan cadence, default 120s, minimum 15s, in sp/app/config.py:1327.
  - Scans run periodically, on app activation, and after detected resume gaps; current-file metadata gets a cheap immediate check.
  - External local changes now reindex, refresh UI, mark Homebase unsynced, and schedule Homebase sync.
  - Homebase status polling is adaptive: 1s while active, 5s idle, 15s hibernated.
  - Event-loop diagnostics remain available via SP_LOG_EVENT_LOOP; the expensive full event sampler now also requires SP_EVENT_LOOP_SAMPLE_EVENTS=1.

  Verified:

  - 8 passed in tests/test_local_filesystem_refresh.py
  - 31 passed in Homebase metadata/badge/summary tests
  - 6 passed in tests/test_search_index_sync.py
  - py_compile passed
  - git diff --check passed

  One caveat: this removes the aggressive filesystem watching path, but your captured ~10k/s MetaCall/X11 wake loop was not coming from the local FS timers, since they were inactive in
  the logs. The diagnostics are still in place to keep tracing that separate spin.


# Original Spec/Prompt





Debug a Linux Mint suspend/resume CPU spin in Stillpoint.

Context:
Stillpoint is a Python app using PySide6/Qt and a uv/uvloop-style event loop integration. After waking from suspend, the main Stillpoint process pegs one CPU core at ~100%. Memory is not growing: RSS is only ~282 MB, VSZ ~2.3 GB, 20 threads, 35 file descriptors. This looks like an event-loop livelock, not a memory leak.

Evidence from strace:
- Main thread is hot.
- Repeated pattern:
  poll([{fd=11, events=POLLIN}, ...], 4, 0) = 1
  read(11, ..., 8) = 8
  write(11, "\1\0\0\0\0\0\0\0", 8) = 8
  write(11, "\1\0\0\0\0\0\0\0", 8) = 8
  poll(..., timeout=0) ...
- syscall summary showed ~84k poll calls, ~190k writes, ~169k recvmsg errors, ~84k reads in about 9 seconds.
- This strongly suggests fd 11 is probably an eventfd or wakeup fd that is re-signaling itself forever.
- There is also epoll_wait(..., timeout=0), suggesting the uv/Qt loop is busy polling instead of sleeping.

Please inspect the app for:
- QSocketNotifier usage
- eventfd/wakeup fd handling
- uv/uvloop/qasync/Qt event loop bridge
- loop.call_soon / call_soon_threadsafe feedback loops
- QTimer.start(0)
- QTimer.singleShot(0, ...)
- processEvents loops
- QMetaObject.invokeMethod / signal loops
- any resume/suspend handlers

Goal:
Find where the main Qt/Python event loop could re-post or re-signal work continuously after resume.

Add diagnostic logging around:
1. QSocketNotifier activation callbacks
2. eventfd/wakeup fd reads/writes
3. uv loop run/stop/iteration calls
4. QTimer intervals, especially 0ms timers
5. call_soon/call_soon_threadsafe callers if practical
6. suspend/resume detection

Add safeguards:
- Ensure wakeup/eventfd callbacks fully drain the fd before returning.
- Temporarily disable QSocketNotifier while handling its activation, then re-enable after draining.
- Prevent recursive self-wakeup from inside the wakeup callback.
- Avoid poll/epoll timeout=0 forever; add backoff or allow the loop to block when no real work exists.
- Add a resume handler that resets/recreates affected timers/notifiers/network watchers if needed.
- Add logging that reports if the same notifier fires more than N times per second.

Please propose a minimal patch first that adds targeted diagnostics and a safe guard against repeated self-wakeup, without changing normal app behavior.

Add a debug command or log output that prints what fd 11 is. On Linux this can be checked via:

readlink /proc/<pid>/fd/11

If it is anon_inode:[eventfd], focus on the wakeup/eventfd bridge. If it is a socket, identify the peer/socket purpose.


