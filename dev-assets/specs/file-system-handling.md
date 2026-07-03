# Local File System Handling

* [ ] StillPoint no longer depends on recursively registering every vault directory with
  `QFileSystemWatcher`. Local changes are detected through a coarse snapshot scan,
  with the expensive work pushed into a background thread and the UI updated only
  after the worker returns a compact result.

```mermaid
flowchart TD
    A[Local vault configured] --> B[_ensure_homebase_watcher]
    B --> C[_ensure_local_filesystem_monitor]
    C --> D[Take initial page snapshot<br/>path -> mtime_ns, size]
    D --> E[Start periodic scan timer<br/>default 120s, minimum 15s]

    E --> F[Timer fires]
    F --> G{Remote mode<br/>or no vault?}
    G -- yes --> Z[Skip]
    G -- no --> H[_schedule_local_filesystem_scan]
    H --> I{Too soon since<br/>last scan?}
    I -- yes --> Z
    I -- no --> J[_on_local_fs_ui_quiet_timeout]

    K[App activated] --> L[_check_current_file_for_external_change]
    L --> M[Stat only current page]
    M --> N{Current page metadata<br/>differs from snapshot?}
    N -- yes --> O[Force local filesystem scan]
    N -- no --> Z
    O --> J

    P[StillPoint saves a page] --> Q[_mark_recent_self_saved_path]
    Q --> R[Remember short self-save window]
    Q --> S[Update snapshot for that page]

    J --> T[Capture generation, reason,<br/>current path, self-save map]
    T --> U[Start worker thread]
    U --> V[_compute_local_fs_refresh_payload]
    V --> W[Walk vault once<br/>ignore .stillpoint and AGENTS.md]
    W --> X[Build new snapshot]
    X --> Y[Compare with previous snapshot]
    Y --> AA[Filter recent self-saved paths]
    AA --> AB[Index changed pages<br/>delete removed pages]
    AB --> AC[Queue reconcile payload]

    AC --> AD[UI result timer polls queue]
    AD --> AE[_drain_local_fs_refresh_results]
    AE --> AF{Worker error?}
    AF -- yes --> AG[Log and stop polling]
    AF -- no --> AH[Replace local snapshot]
    AH --> AI{Structure changed?}
    AI -- yes --> AJ[Bump tree version<br/>queue tree refresh on UI activity]
    AI -- no --> AK{Indexed or removed pages?}
    AK -- yes --> AL[Refresh task and link panels]
    AK -- no --> AM[No visible UI refresh]
    AJ --> AL
    AL --> AN{Current page changed<br/>and editor idle?}
    AM --> AN
    AN -- yes --> AO[Reload current page]
    AN -- no --> AP{Homebase enabled<br/>and local changes?}
    AO --> AP
    AP -- yes --> AQ[Mark unsynced<br/>schedule Homebase sync]
    AP -- no --> AR[Stop result timer]
    AQ --> AR

    subgraph UIThread[Qt UI thread]
        B
        C
        D
        E
        F
        H
        J
        K
        L
        M
        Q
        R
        S
        AD
        AE
        AH
        AJ
        AL
        AO
        AQ
        AR
    end

    subgraph WorkerThread[Background worker thread]
        U
        V
        W
        X
        Y
        AA
        AB
        AC
    end
```

Notes:

- The expensive periodic refresh path is non-blocking for the UI: vault walking,
  snapshot comparison, page reads, and incremental indexing happen in the worker
  thread.
- The periodic cadence is configurable through
  `local_filesystem_scan_interval_seconds`; the loader clamps it to at least 15
  seconds and defaults to 120 seconds.
- The scheduler also applies a short minimum gap between non-forced scan
  requests, so activation or repeated timer events cannot immediately pile up.
- Self-saves are recorded for a short window and the saved page's snapshot entry
  is updated immediately. That prevents StillPoint from treating its own write as
  an external edit on the next scan.
- One remaining synchronous cost is the initial snapshot in
  `_ensure_local_filesystem_monitor`. If very large vaults still lag on open,
  that initial snapshot is the next candidate to move into the worker path.
