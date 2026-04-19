# Home Base GC Plan

## Current State

Phase 1 of Home Base garbage collection is implemented.

### What Exists

- Server-side janitor module: `sp/server/homebase_gc.py`
- Launcher script: `packaging/server/run-homebase-gc.sh`
- Systemd unit files:
  - `packaging/server/homebase-gc.service`
  - `packaging/server/homebase-gc.timer`
- Example env configuration:
  - `.env.example`
- README coverage for setup and usage
- Focused test coverage in `tests/test_homebase_gc.py`

### Current GC Behavior

The current janitor is a retention-based, reachability-driven cleanup pass.

It does all of the following:

- Reads retention policy from env
- Logs the effective retention policy at startup
- Scans Home Base vault directories under `STILLPOINT_VAULTS_ROOT/homebase`
- Computes per-vault size before cleanup
- Selects retained checkpoints using these policy controls:
  - `SP_HOMEBASE_GC_KEEP_LATEST`
  - `SP_HOMEBASE_GC_KEEP_ALL_DAYS`
  - `SP_HOMEBASE_GC_KEEP_DAILY_DAYS`
  - `SP_HOMEBASE_GC_KEEP_WEEKLY_DAYS`
  - `SP_HOMEBASE_GC_MIN_CHECKPOINTS`
- Deletes non-retained manifests
- Deletes non-retained checkpoint metadata files
- Marks objects reachable from retained manifests
- Deletes unreachable object blobs
- Logs savings by category:
  - manifests
  - checkpoints
  - objects
- Writes a state file under `.gc/state.json`
- Supports:
  - `--dry-run`
  - `--force`

### What Is Tested

The current test coverage verifies:

- Dry-run preserves files and logs `would_delete`
- Real pruning deletes non-retained manifests, checkpoints, and unreachable objects
- Retention minimum-count safeguard works
- CLI entrypoint works
- State file is written
- Interval gate skips runs until forced

### Current Constraints

The current object model does not provide true binary dedupe.

Right now:

- Objects are identified by ciphertext hash
- Encryption uses a random nonce
- Identical plaintext can still produce different stored objects

That means Phase 1 can reclaim old unreachable duplicate blobs after pruning, but it cannot prevent new duplicate blobs from being created across devices or after cache loss.

## Next Step State

The next meaningful state is not “more GC rules.”

The next state is a storage model that separates:

- history retention
- object reachability
- stable content identity

### Target Outcome

Move from:

- ciphertext-hash object identity

to:

- stable vault-scoped content identity plus encrypted storage blobs

### Why

This is required to make these statements true:

- the same binary does not get stored multiple times unnecessarily
- the same attachment uploaded from multiple devices dedupes cleanly
- a cache reset does not force silent storage growth for unchanged content

### Proposed Next-State Design

#### 1. Add Stable Content Identity

Introduce a vault-scoped `content_id` derived from plaintext using a keyed hash.

Properties:

- stable for identical plaintext inside the same vault
- different across different vaults
- separate from randomized ciphertext storage

This keeps encrypted-at-rest behavior while enabling dedupe.

#### 2. Keep Encrypted Blob Storage

Preserve randomized encryption for stored blobs.

The important change is:

- manifests reference `content_id`
- server stores a mapping from `content_id` to encrypted blob storage

#### 3. Preserve Reachability GC

The existing GC model remains useful.

In the next state it would:

- prune old manifests/checkpoints by retention policy
- mark reachable `content_id` entries from retained manifests
- delete unreferenced content mappings and blob files

#### 4. Add Device Progress Tracking

Before aggressive multi-device pruning, track device acknowledgement.

Suggested fields:

- `device_id`
- `last_pulled_checkpoint_id`
- `last_seen_at`

This makes pruning safer when 3 to 4 devices are active.

## Recommended Implementation Order

### Completed

1. Phase 1 retention janitor
2. Logging and accounting
3. Systemd packaging
4. Focused GC tests

### Next

1. Design vault-scoped `content_id`
2. Define manifest schema changes
3. Define server blob index / lookup model
4. Add device acknowledgement model
5. Add migration strategy for existing vaults
6. Add end-to-end tests for same-binary dedupe behavior

## Non-Goals For The Current GC Phase

These are intentionally not solved by the current GC pass:

- preventing duplicate object creation at write time
- cross-device dedupe of identical binaries
- deterministic encryption
- rollback UX / historical restore UX
- multi-device acknowledgement-aware pruning

## Short Summary

Current state:

- retention-based pruning works
- unreachable object cleanup works
- logging and operational packaging exist
- tests cover the current GC flow

Next state:

- add stable content identity
- make binary dedupe real
- keep GC as the cleanup layer on top of the improved object model