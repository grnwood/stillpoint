# Homebase Local Recovery and Pinned Checkpoints

## Status

Proposed feature specification.

This document extends the Homebase behavior described in
`dev-assets/specs/homebase-sync-behavior.md`. It adds device-local recovery
for destructive pull operations, a suspicious-change safety fuse, and an
optional later phase for named server checkpoint pins.

## Problem

Homebase is local-first, but a valid remote checkpoint can still contain an
unwanted change. Examples include a malfunctioning client, an accidental mass
edit, an unintended deletion, a bad reorganization, or a server-authoritative
reset initiated against the wrong remote state.

The current client protects some concurrent local edits by creating conflict
copies. It does not preserve the previous local bytes when a newer remote file
wins last-writer-wins, when a known remote deletion is applied, or when the
server-authoritative reset rewrites the local vault.

Homebase retains historical server checkpoints, but server retention is not a
substitute for an independent local recovery layer:

- checkpoints are eventually garbage-collected;
- several bad checkpoints can be published in succession;
- recovery currently requires operational tooling rather than a normal client
  workflow;
- a server, account, authentication, or encryption problem can make remote
  history temporarily unavailable.

## Goals

1. Preserve recoverable pre-change content before Homebase overwrites or
   deletes an existing local file.
2. Keep recovery data on the current device and outside the Homebase sync set.
3. Allow recovery of one file, selected files, or the complete local state that
   existed before a pull.
4. Make recovery storage bounded, deduplicated, inspectable, and configurable.
5. Detect unusually destructive checkpoints and pause before applying them.
6. Cover normal pulls and explicit server-authoritative resets.
7. Treat restored content as a new local change that can be published normally.
8. Preserve the existing local-first, encrypted-remote Homebase model.

## Non-goals

- Continuous version history for every local edit.
- Replacing system backups, filesystem snapshots, or offline backups.
- Automatically merging arbitrary versions of a file.
- Synchronizing device-local recovery objects between clients.
- Retaining every server checkpoint forever.
- Encrypting the entire local vault at rest as part of this feature.

## Terminology

- **Preimage**: the local bytes and metadata immediately before a destructive
  sync operation.
- **Recovery event**: a manifest describing one remote checkpoint application
  and the preimages captured for it.
- **Recovery object**: preimage bytes stored by SHA-256 digest.
- **Recovery point**: a recovery event retained for restoration.
- **Pinned recovery point**: a device-local recovery event exempt from normal
  retention pruning and optionally given a user-visible name.
- **Server checkpoint pin**: a later, separate feature that prevents a named
  Homebase checkpoint and its reachable objects from server garbage collection.
- **Destructive operation**: overwriting or deleting a local file. Creating a
  previously absent local file is recorded but has no preimage object.

## Default Behavior

Local recovery is enabled by default for Homebase vaults.

Before applying a remote checkpoint, the client MUST:

1. Build the complete local apply plan.
2. Classify paths as create, overwrite, delete, unchanged, keep-local, or
   conflict-copy.
3. Capture a preimage for every planned overwrite and delete.
4. Durably write the recovery objects and an initial recovery-event manifest.
5. Evaluate the suspicious-change safety fuse.
6. Apply the accepted plan.
7. Finalize the recovery event with the actual outcome of each path.

If a required preimage cannot be stored, the client MUST NOT perform that
overwrite or deletion. It records a path-local sync error and retries later.
Failure to protect one path does not authorize unprotected mutation of that
path.

New remote files do not require a preimage, but the recovery event records
their paths so a complete rollback can remove them.

Byte-identical and non-material text differences do not create recovery
objects because no local bytes are changed.

Conflict-copy behavior remains unchanged. Writing a remote conflict copy does
not overwrite the original local file, so it is recorded in the event but does
not require an original-file preimage.

## Storage Location

Recovery data MUST live outside the vault:

```text
~/.stillpoint/homebase-recovery/
  <vault-id>/
    <device-id>/
      recovery.sqlite
      objects/
        ab/
          <sha256>
      events/
        <event-id>.json
```

The platform-specific StillPoint application-data directory may replace
`~/.stillpoint`, but it MUST remain device-local and outside every vault root.

This is stronger than placing backups under the vault's `.stillpoint`
directory. Homebase already excludes `.stillpoint`, but external storage also
keeps recovery data out of vault searches, filesystem watchers, exports,
copies, and other sync providers that operate on the vault directory.

The storage identity uses the stable Homebase `vault_id`, not the vault's
current filesystem path. Moving the local vault therefore does not orphan its
recovery history. `device_id` separates independent local histories on a
shared operating-system account.

All recovery directories and files SHOULD use owner-only permissions where the
platform supports them. Recovery data has the same confidentiality sensitivity
as the plaintext local vault.

## Content-addressed Object Store

Preimage bytes are stored once using:

```text
object_id = sha256(file_bytes)
objects/<first-two-hex-characters>/<object_id>
```

Object creation MUST be atomic:

1. Write to a temporary file in the destination directory.
2. Flush file contents.
3. Atomically replace or link into the final content-addressed path.
4. Treat an existing object with the same verified digest as success.

Objects are plaintext because the working vault is plaintext. A future local
encryption feature may encrypt recovery storage, but it is not required for
the first implementation.

## Recovery Event Manifest

Each attempted pull that can change local files receives a random event ID.
The JSON manifest is human-inspectable; SQLite provides indexes, retention
accounting, and object reference counts.

Example:

```json
{
  "schema_version": 1,
  "event_id": "01K6...",
  "vault_id": "vault-uuid",
  "device_id": "device-uuid",
  "created_at": "2026-09-29T19:30:12Z",
  "completed_at": "2026-09-29T19:30:15Z",
  "operation": "normal-pull",
  "source_checkpoint_id": "previous-checkpoint-id",
  "target_checkpoint_id": "incoming-checkpoint-id",
  "remote_device_id": "other-device-id",
  "state": "complete",
  "pinned": false,
  "label": null,
  "paths": [
    {
      "path": "Notes/example.md",
      "planned_action": "overwrite",
      "result": "applied",
      "old_object_id": "sha256-of-local-bytes",
      "new_object_id": "homebase-object-id-or-plaintext-hash",
      "old_size": 1234,
      "new_size": 987,
      "old_mtime_ns": 1780171000000000000,
      "old_mode": 420,
      "error": null
    }
  ]
}
```

Required event states are:

- `preparing`: preimages are being captured;
- `protected`: all required preimages are durable;
- `applying`: remote mutations are in progress;
- `complete`: all planned mutations reached a terminal result;
- `partial`: some paths applied and others failed or were skipped;
- `cancelled`: the user rejected a suspicious plan before mutation;
- `failed`: no destructive mutation began and preparation failed.

The event MUST distinguish the normal pull path from
`server-authoritative-reset`.

An event with only new-file creations still needs a manifest if applying the
whole event must be reversible. It does not need recovery objects.

## Apply Transaction

The filesystem cannot provide one atomic transaction across an entire vault.
The recovery event therefore acts as a durable write-ahead recovery record.

### Planning

The engine downloads and validates the target manifest and required objects,
then computes a plan without changing the vault. Planning records:

- local and remote existence;
- local and remote content identity;
- the expected action under existing conflict rules;
- local and remote sizes and modification times;
- aggregate safety-fuse statistics.

The planner MUST revalidate a local file immediately before capturing its
preimage. If it changed since planning, the action is recalculated rather than
backing up stale bytes and overwriting newer work.

### Protection

For every overwrite and deletion:

1. Open and read the local file.
2. Calculate its SHA-256 digest.
3. Write or reuse the recovery object.
4. Record the path metadata and object reference.

After all required objects and the manifest are durable, transition the event
to `protected`. No destructive writes may occur before this point.

### Application

Existing atomic file replacement remains the write mechanism. Each completed
path updates the event result. A crash may therefore leave an `applying` event,
which the next startup surfaces as an interrupted sync event with the exact
set of protected and applied paths.

Remote deletion MUST use the same protection path before `unlink()`.

The server-authoritative reset MUST use this workflow for all overwrites and
for any local paths it deletes. If reset semantics do not delete paths absent
from the server today, this feature does not implicitly change those semantics.

### Completion

The event becomes `complete` only after all path results are written. Partial
application becomes `partial`; it remains restorable and is visible in the UI.
The sync engine may then update its normal checkpoint and object-cache state.

## Retention Policy

A simple “last three copies per file” policy is insufficient because three
rapid bad pulls could remove the last healthy version. The default retention
policy is hybrid:

- retain the latest 3 distinct preimages per path;
- retain at least one recovery event per UTC day for 7 days;
- never automatically delete pinned events;
- enforce a configurable total storage quota;
- prune oldest unpinned, unprotected events first when above quota;
- never prune an object still referenced by a retained event.

Recommended initial quota: 2 GiB per device across all Homebase vaults.

Settings SHOULD expose:

- Enable local Homebase recovery: default on
- Versions per file: default 3, minimum 1
- Daily recovery retention: default 7 days
- Storage quota: default 2 GiB
- Current usage and oldest recovery date
- Open Recovery Manager
- Prune unpinned recovery data now

Retention runs after successful sync and on application startup, never between
capturing a preimage and finalizing its event. If quota enforcement cannot make
enough room because pinned events consume the quota, destructive sync pauses
with a clear storage error.

The pruning implementation SHOULD use database reference counts or a mark and
sweep from retained event manifests. It MUST tolerate a crash between manifest
and object cleanup without deleting reachable objects.

## Suspicious-change Safety Fuse

The client SHOULD pause before applying a checkpoint whose planned effect is
unusual for that vault. Recovery is created before confirmation so continuing
remains safe.

Signals include:

- deletion count and percentage of previously tracked files;
- overwrite count and percentage;
- total bytes removed;
- many files shrinking substantially;
- many non-empty text files becoming empty or nearly empty;
- an incoming checkpoint affecting far more paths than recent checkpoints;
- a server-authoritative reset affecting an existing non-empty vault;
- a previously unseen remote device producing a large change.

Initial conservative defaults:

- always warn for server-authoritative reset of a non-empty vault;
- warn when at least 25 paths and at least 10 percent of tracked files would be
  overwritten or deleted;
- warn when at least 10 paths and at least 5 percent of tracked files would be
  deleted;
- warn when at least 10 non-empty text files would become empty or lose at
  least 90 percent of their bytes.

Thresholds are product defaults, not correctness boundaries. They SHOULD be
adjustable later based on observed vault behavior.

The warning displays the source device, target checkpoint, counts, byte
impact, and representative paths. Actions are:

- Review Changes
- Apply Now
- Cancel This Pull

Cancellation MUST leave the remote head unseen so a later sync can reconsider
it. The client must not silently mark a rejected checkpoint as successfully
pulled.

## Recovery Manager

Add a Homebase Local Recovery view accessible from the Homebase sync summary
and application menu.

The event list shows:

- date and time;
- normal pull, reset, or restore;
- remote device;
- target checkpoint;
- counts of creates, overwrites, and deletes;
- complete, partial, interrupted, or cancelled state;
- pinned status and optional label;
- stored size attributable to the event.

Selecting an event shows its changed paths. The UI supports:

- text diff for UTF-8 Markdown and text files;
- image preview where the application already supports the format;
- metadata-only comparison for other binary files;
- restore one file;
- restore selected files;
- restore the complete pre-event state;
- export selected preimages to a new directory;
- pin/unpin and name the local recovery point;
- delete an unpinned event with confirmation.

Diffs are computed on demand. The event list must not load every stored object
into memory.

## Restore Semantics

A restore is a deliberate new local edit, not a rewind of Homebase's remote
head.

Before restoration:

1. Suspend the Homebase sync engine and wait for an active cycle to finish.
2. Build the restore plan.
3. Create a new recovery event protecting the files currently on disk. This
   makes the restore itself reversible.
4. Apply restored bytes atomically.
5. Delete paths that were created by the original event only when performing a
   complete event restore and after explicit confirmation.
6. Assign restored files a current modification time.
7. Refresh editor and filesystem state.
8. Resume sync and schedule an immediate push.

The next push publishes a new checkpoint containing the restored content. The
client MUST NOT set its local checkpoint state backward or cause the incoming
bad checkpoint to overwrite the restored bytes again before they are
published.

If an open editor contains unsaved changes for a restore target, the user must
choose whether to save, discard, skip that file, or cancel the restore.

Full-event restore rules are:

- prior overwrite: restore the preimage;
- prior deletion: recreate the preimage;
- prior creation: remove the created file only with explicit confirmation and
  only if its current bytes still match the bytes applied by that event;
- file changed since the event: require review rather than overwriting
  silently.

## Named Local Recovery Points

Naming a local recovery point is in scope for the first implementation once
basic restore works. Pinning sets `pinned=true` and optionally stores a label
such as “Before notebook reorganization.” Pinned events are excluded from
automatic retention but still count toward displayed storage usage.

Labels stay local to the device and are not Homebase-synced.

The UI SHOULD warn before unpinning the only named recovery point older than
the ordinary retention window.

## Server Checkpoint Pins (Later Phase)

Server checkpoint pins are useful and are not considered over-engineering, but
they are separate from local recovery. A local recovery point protects one
device even when Homebase is unavailable. A server pin makes a known-good
checkpoint available to every authorized device.

Suggested model:

```text
homebase/<vault-id>/refs/pins/<pin-id>.json
```

The pin contains at least:

- `pin_id`
- `checkpoint_id`
- `created_at`
- `created_by_device_id`
- label metadata, encrypted if labels must not be visible to the server

Required server behavior:

- create, list, rename, and delete pins through authenticated endpoints;
- validate that the checkpoint exists before pinning;
- treat every pinned checkpoint as a garbage-collection root;
- retain the pinned manifest, checkpoint metadata, and all reachable objects;
- prevent deletion of reachable objects until the final referring pin is
  removed.

The client can compare any two retained checkpoints by comparing manifest
paths and object IDs. This yields added, modified, and deleted paths without
downloading file contents. Content is downloaded and decrypted only for an
on-demand detailed diff or restore.

Server pins MUST NOT replace local recovery and are not required to ship its
first version.

## Observability

Homebase sync logging SHOULD include concise recovery events without logging
file contents:

```text
recovery plan event=<id> checkpoint=<id> create=4 overwrite=12 delete=2
recovery protected event=<id> objects_new=9 objects_reused=5 bytes_new=183204
recovery safety-pause event=<id> reason=mass-delete affected=31 percent=18.2
recovery apply-complete event=<id> applied=18 skipped=0 failed=0
recovery prune events=3 objects=7 bytes=98213
```

Status UI should distinguish “Preparing local recovery,” “Waiting for review,”
and “Applying pulled files.”

## Security and Integrity

- Validate every recovery object digest when writing and before restoring.
- Reject recovery paths that escape the vault root or cross into a nested
  vault.
- Never follow a recovery manifest path outside the configured vault.
- Use atomic writes for objects, manifests, database updates where possible,
  and restored files.
- Use owner-only permissions for recovery roots and temporary files.
- Do not include passphrases, authentication tokens, or decrypted remote keys
  in manifests or logs.
- Treat malformed recovery events as unavailable rather than partially
  restoring unverified data.
- Provide an integrity-check operation that reports missing, corrupt, and
  orphaned objects without mutating recovery data.

## Crash Recovery

On startup, scan for events in `preparing`, `protected`, or `applying` state.

- `preparing`: no destructive operation was authorized; mark failed after
  verifying that application never began.
- `protected`: safe to offer resume or cancel because no path should have been
  applied.
- `applying`: compare recorded old/new hashes with disk contents, mark each
  path as applied, not applied, or diverged, and present recovery options.

Normal sync MUST not discard or overwrite an interrupted event record. It may
resume only after the event is reconciled or the user explicitly continues.

## Configuration and Compatibility

New settings require defaults so existing Homebase profiles gain protection
without migration work. Disabling recovery is allowed only through an explicit
advanced setting with a warning that remote overwrites and deletions will no
longer have device-local preimages.

The recovery database and event schemas are versioned. A newer unsupported
schema must cause recovery protection to pause rather than silently treating
the store as empty.

Vaults that are not configured for Homebase do not create recovery events.
Plain remote and ordinary local vault behavior remain unchanged.

## Implementation Boundaries

Likely code boundaries are:

- `sp/sync/recovery.py`: storage, event lifecycle, integrity, retention, and
  restoration primitives;
- `sp/sync/engine.py`: apply planning, protection gate, safety evaluation, and
  event finalization;
- `sp/sync/local_fs.py`: reusable atomic and metadata-preserving filesystem
  helpers;
- Homebase sync summary UI: Recovery Manager, plan review, and restore flow;
- preferences/config: enablement, retention, quota, and thresholds;
- `sp/server/homebase_gc.py`: later support for pinned checkpoint roots;
- `sp/sync/homebase_client.py` and server routes: later pin APIs.

Recovery storage logic should be independently testable without constructing
the Qt UI or contacting a Homebase server.

## Test Requirements

### Storage

- identical preimages deduplicate to one recovery object;
- distinct contents create distinct objects;
- object and manifest writes are atomic;
- digest corruption is detected before restore;
- recovery roots are outside the vault and absent from sync iteration;
- vault moves continue to resolve history by `vault_id`;
- traversal and nested-vault paths are rejected.

### Pull protection

- last-writer-wins overwrite captures the exact old bytes first;
- remote deletion captures the exact deleted bytes first;
- new-file creation is recorded without a preimage object;
- unchanged, cached-unchanged, and non-material text paths do not create
  unnecessary objects;
- conflict-copy behavior does not back up or overwrite the original;
- authoritative reset protects every overwritten or deleted local file;
- disk-full or permission failure prevents the associated destructive write;
- a local file changed between plan and apply is re-evaluated safely.

### Recovery

- restore one overwritten file;
- recreate a remotely deleted file;
- restore a complete event;
- reverse the restore using its own recovery event;
- refuse to delete a later-modified file that was originally created by the
  recovered event;
- restoration becomes a new local change and produces a new checkpoint;
- unsaved editor changes require an explicit choice.

### Retention

- latest distinct versions per path are retained;
- daily retention survives rapid repeated pulls;
- pinned events never prune automatically;
- quota pruning removes only unreachable objects;
- reference sharing prevents deletion of objects used by retained events;
- quota exhaustion by pins pauses destructive sync safely.

### Safety fuse

- ordinary small pulls apply without prompting;
- configured mass overwrite and deletion thresholds pause before mutation;
- cancelled checkpoints are not marked pulled;
- review shows accurate counts and representative paths;
- explicit continuation applies the already-protected plan.

### Crash behavior

- interrupted preparation performs no unprotected overwrite;
- interrupted application can distinguish applied, unapplied, and diverged
  paths;
- startup surfaces incomplete events and does not silently delete them.

### Server pins, when implemented

- pin CRUD requires vault authorization;
- unknown checkpoints cannot be pinned;
- garbage collection retains pinned manifests and reachable objects;
- removing the final pin makes old data eligible for normal retention;
- manifest comparison reports added, modified, and deleted paths correctly.

## Acceptance Criteria for the First Release

The first release is complete when:

1. Local recovery is enabled by default for Homebase vaults.
2. Normal remote overwrites and deletions are protected before mutation.
3. Server-authoritative reset uses the same protection gate.
4. Recovery storage is device-local, outside the vault, deduplicated, and
   quota-limited.
5. The user can inspect events and restore one file or an entire event.
6. Restore produces a new local change instead of moving checkpoint state
   backward.
7. Pinned local recovery points survive retention.
8. Suspicious mass changes pause for review with a usable explanation.
9. A recovery write failure prevents the corresponding destructive change.
10. Automated tests cover overwrite, delete, reset, restore, retention,
    corruption, quota failure, and interrupted application.

Server checkpoint pins may ship later and do not block the first local-recovery
release.
