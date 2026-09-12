# Homebase Three-Way Comparison

Status: Proposed

## Summary

Homebase synchronization uses a per-path three-way comparison between the last
checkpoint successfully reconciled by the client, the current local filesystem,
and the latest server checkpoint. Content-addressed object IDs determine whether
a file changed. Filesystem modification times are advisory metadata only and
must not determine whether a conflict exists.

The sync engine pulls remote-only changes, publishes local-only changes, and
creates a conflict only when local and remote changed differently from the same
base. An unresolved disagreement is represented once, remains stable across
equivalent checkpoints, and does not prevent unrelated paths from syncing.

Publishing the latest checkpoint uses optimistic concurrency so two clients
cannot silently replace checkpoints that they did not compare against.

## Goals

- Transfer only objects whose content is not already known locally or remotely.
- Distinguish one-sided edits from genuine simultaneous edits.
- Preserve unsynced local work without treating every differing mtime as a
  conflict.
- Prevent repeated conflict files, rows, and popups for the same disagreement.
- Permit unrelated files to finish syncing while conflicts or path errors remain.
- Prevent two clients from blindly overwriting the server's latest pointer.
- Detect paths that cannot be represented consistently across supported
  filesystems.
- Migrate existing Homebase clients without deleting `.stillpoint` or reseeding
  the complete vault.

## Non-Goals

- Homebase does not automatically merge arbitrary Markdown or binary content.
- Modification times are not used as a distributed clock.
- The server does not decrypt vault objects or inspect plaintext.
- This work does not add forgotten-password or encryption-passphrase recovery.
- This work does not silently rename, truncate, or delete conflicting paths.

## Terms

- **Base**: the object ID, or absence, for a path at the last checkpoint that the
  client successfully reconciled.
- **Local**: the current object ID, or absence, computed from the local
  filesystem.
- **Remote**: the object ID, or absence, in the latest server checkpoint.
- **Absent**: an explicit path state used for additions and deletions. Absence is
  part of comparison and is not represented by an empty file.
- **Checkpoint**: an immutable canonical mapping from paths to object IDs.
- **Publication**: the event that conditionally advances the server's latest
  pointer to a checkpoint.
- **Conflict**: local and remote states that both differ from base and from each
  other.

## Path Identity

Manifest paths use `/` separators, contain no empty, `.` or `..` segments, and
are normalized to Unicode NFC. Display casing is preserved.

Every path also has a collision key produced by Unicode-normalizing and
case-folding each segment. Two distinct display paths with the same collision
key are not allowed in one vault checkpoint. This prevents Linux clients from
publishing paths that collapse into one file on Windows or macOS.

Before network transfer, the client scans for collision keys and reports all
colliding paths as a blocking path-identity error. It does not choose a winner.
The server independently rejects a manifest containing collision keys with more
than one display path.

The canonical root page is `<vault-name>/<vault-name>.md`, using the configured
vault name's exact casing. Legacy root shorthand and case variants are detected
during migration and presented for explicit consolidation; they are not silently
mapped when more than one physical file exists.

## Local Reconciliation State

The client stores synchronization state under `.stillpoint/sync/` using a new
schema version. At minimum, the state contains:

- The latest server checkpoint ID that was fetched.
- The latest checkpoint ID successfully reconciled and used as the base.
- A per-path base map containing an object ID or explicit absence.
- The last observed local content hash and filesystem metadata used to avoid
  unnecessary rehashing.
- Active conflicts keyed by canonical collision key.
- Pending local deletions and path-local transfer errors.

The base map is not replaced wholesale merely because a remote manifest was
downloaded. Each path advances independently only after its pull, push, equality
check, deletion, or user-selected resolution succeeds.

The existing object cache remains a transfer optimization. It must not be the
only record of reconciliation ancestry, and a partial pull must not erase the
base for paths that conflicted or failed.

State and conflict writes are atomic. Concurrent UI, scan, token-refresh, and
transfer activity must not use a shared fixed temporary filename.

## Three-Way Decision Table

For each path in the union of base, local, and remote paths, the engine applies
these rules in order:

| Local state | Remote state | Comparison with base | Action |
| --- | --- | --- | --- |
| `L == R` | `R == L` | Any | No content transfer; advance base to the common state. |
| `L == B` | `R != B` | Remote-only change | Pull remote, including a remote deletion. |
| `L != B` | `R == B` | Local-only change | Publish local, including a local deletion. |
| `L != B` | `R != B` and `L != R` | Both changed differently | Create or update one conflict. |

Equality is object-ID equality, including explicit absence. Mtime, size, device
ID, and checkpoint publication time do not override this table.

### Additions and Deletions

- Base absent, local present, remote absent is a local addition.
- Base absent, local absent, remote present is a remote addition.
- Base present, local absent, remote equals base is a local deletion.
- Base present, local equals base, remote absent is a remote deletion.
- Base present, one side deleted and the other side changed is a conflict.
- Base absent and both sides independently created different content at the same
  path is a conflict.

## Cold Start and Missing State

A client with no trustworthy base first fetches the latest manifest and compares
content IDs against its local files.

- Matching paths are adopted without downloading the object.
- Remote-only paths are pulled.
- Local-only paths are staged for publication.
- A path present on both sides with different content becomes an initial-pairing
  conflict because the client cannot prove which side changed.
- An empty local vault is seeded from the latest checkpoint.
- An empty remote vault is seeded from the local vault.

Deleting `.stillpoint` is never presented as authentication recovery or a normal
sync repair. Missing metadata initiates this cold-start comparison; it must not
force a complete download when local object IDs already match remote objects.

## Pull Behavior

The client fetches the latest manifest before transferring objects. It compares
manifest object IDs with the base and local object IDs, then downloads only
remote objects needed for remote-only changes or conflict review. Downloads may
use up to the configured transfer-worker limit.

Remote-only changes are written atomically. A successful write advances that
path's base. A path-specific download or filesystem error is recorded and the
remaining paths continue. The failed path does not advance its base and is
retried on a later cycle.

Authentication failures, invalid manifests, checkpoint integrity failures, and
an encryption-key mismatch fail the entire attempt because the client cannot
trust the checkpoint comparison.

## Push and Publication Behavior

After reconciliation, the client constructs a candidate checkpoint from:

- Successfully reconciled local states.
- Local-only additions, edits, and deletions selected for publication.
- The current remote state for unresolved conflict paths.
- The current remote state for paths whose local application failed.

An unresolved local version is not placed into the shared checkpoint merely
because the pull half created a conflict copy. Unrelated local changes may still
be published.

Objects are prepared and uploaded in parallel up to the configured worker limit.
Already-present content-addressed objects are reused. A checkpoint whose canonical
path/object map equals the remote checkpoint is a no-op and must not create a new
publication.

## Stable Checkpoint Identity

The checkpoint ID is the SHA-256 hash of a canonical serialization containing
only synchronization-significant fields: schema version, vault ID, and the
sorted path-to-object mapping with file kind. Volatile values such as creation
time, publisher device, request time, and per-client scan metadata are not part
of checkpoint identity.

Publication time, username, device ID, and the prior checkpoint ID are stored as
server-side publication metadata. Repeating an equivalent candidate therefore
produces the same checkpoint ID and is idempotent.

## Conditional Latest Update

The client publishes with both:

- `checkpoint_id`: the desired immutable checkpoint.
- `expected_checkpoint_id`: the remote head used for three-way comparison, or
  explicit absence when creating the first checkpoint.

Under a per-vault lock, the server compares the expected value with the current
latest pointer and updates it atomically.

- If expected equals current, the server advances latest and returns success.
- If desired already equals current, the request succeeds idempotently.
- Otherwise, the server returns `409 Conflict` with the actual current checkpoint
  and does not alter latest.

On `409`, the client fetches the new head, reruns three-way comparison, and
rebuilds the candidate. It must not retry the stale latest update blindly.

## Conflict Lifecycle

There is at most one visible active conflict per canonical path. The conflict
record contains:

- Canonical and display path.
- Base, local, and remote object IDs or explicit absence.
- Remote checkpoint and publisher device metadata.
- First-detected and last-seen timestamps.
- The local and remote mtimes as advisory display values.
- Resolution state and the object IDs to which that resolution applied.

Conflict identity is based on path plus base/local/remote content states, not the
checkpoint ID. When a new checkpoint contains the same remote object, the engine
updates `last_seen` without creating another row, file, or popup.

Remote candidate bytes are kept in the excluded `.stillpoint/sync/` area by
object ID. They are not written as timestamped siblings in the visible vault.
This avoids orphan conflict files, accidental indexing, sync recursion, and path
length failures. Existing sibling `.sync-conflict-*` files remain readable for
migration and are removed only after successful migration or resolution.

If remote content changes again while a conflict is unresolved, the active row
is updated to state that the server changed again. Prior candidate object IDs are
retained in conflict history so content is not lost, but the UI still presents
one path-level workflow.

### Resolution

- **Keep My Version** conditionally publishes the selected local object against
  the current remote head.
- **Keep Server Version** atomically applies the selected remote object locally.
- **Merge** writes the merged content locally and conditionally publishes its
  object.

A resolution is complete only after its local write and/or conditional server
publication succeeds. The base advances to the resolved object, the active
conflict is marked resolved, and stored candidates no longer referenced by
history may be cleaned up.

If the server head changes during resolution, the engine fetches it and verifies
that the path's remote object still matches the reviewed candidate. A different
remote object returns the conflict to review instead of applying a stale choice.

The UI shows one row per path, identifies whether local, remote, or both changed
from base, and labels device ID as the checkpoint publisher rather than the file
author. Reopening the application does not re-popup a conflict whose content
triple has not changed.

## Filesystem Errors

Unrepresentable names, permission failures, missing objects, and individual
write failures are path-local errors when checkpoint integrity remains valid.
The engine records the path, phase, object ID, reason, and attempt count, then
continues with other paths.

Names are never automatically truncated because that changes path identity and
can collide with another remote file. The user may rename the path from a capable
client or explicitly publish its deletion. Failed paths retain their base and
remote state until repaired.

## Migration and Compatibility

On first use of this specification's state schema:

1. Preserve existing scan, object-cache, error, deletion, and conflict files.
2. Fetch the current remote checkpoint.
3. Adopt an existing cached object as base only when it equals the object in the
   fetched remote checkpoint for that path.
4. Adopt matching local and remote objects directly.
5. Treat remaining differing paths as cold-start comparisons; do not infer
   ancestry from mtime.
6. Group unresolved legacy conflict records by collision key and content where
   possible. Preserve ambiguous candidates for review.
7. Detect case-fold collisions before allowing publication.

The server rollout has two stages:

1. Deploy support for stable checkpoint validation and conditional latest
   updates while temporarily accepting legacy publications.
2. After writable clients support conditional publication, require
   `expected_checkpoint_id` for writes and return `428 Precondition Required` to
   legacy clients. Read-only legacy clients remain supported.

Allowing old clients to publish latest unconditionally after the new behavior is
enabled would reintroduce checkpoint races and is not a supported steady state.

## Observability

Client logs include one concise reconciliation decision per changed path with
base, local, and remote object-ID prefixes. Aggregate logs report counts for
local-only, remote-only, equal, conflicted, skipped, failed, downloaded,
uploaded, and reused objects.

Server logs distinguish successful conditional publications, idempotent
publications, and rejected stale-head attempts. Normal concurrent activity is a
`409` reconciliation event, not a server error.

Status UI separates active conflicts, retryable path errors, authentication
failure, and offline backoff. A completed cycle with unresolved conflicts may be
reported as synchronized with review required; it is not reported as interrupted
unless a whole-attempt failure occurred.

## Acceptance Criteria

- A local edit against an unchanged remote base uploads without a conflict,
  regardless of mtime ordering.
- A remote edit against an unchanged local base downloads without a conflict.
- Different local and remote edits from the same base create exactly one active
  conflict and one popup.
- Repeated checkpoints containing the same disagreement do not create additional
  conflict rows or visible conflict files.
- Resolving a conflict remains resolved when an equivalent stale publication is
  observed.
- Two clients publishing concurrently cannot silently overwrite an unseen head;
  one succeeds and the other receives `409`, rebases, and retries.
- A no-op sync does not publish a new checkpoint.
- A copied vault without `.stillpoint` reuses matching local content and does not
  download the entire vault.
- Local-only and remote-only additions and deletions follow the decision table.
- A delete-versus-edit race creates a conflict without losing the edited bytes.
- One path-length or permission failure does not prevent other paths from
  finishing, and the failed path remains retryable.
- Pull and push object transfers use up to the configured worker limit.
- Case-only or Unicode-normalization path collisions are detected before
  publication and are never silently renamed.
- Existing state migrates without requiring deletion of `.stillpoint`, a full
  reseed, or re-upload of unchanged objects.

## Required Tests

- Unit tests for every row of the three-way table, including explicit absence.
- Cold-start tests for matching, local-only, remote-only, and differing paths.
- Two-client integration tests for simultaneous edits and conditional latest
  publication.
- Regression tests for repeated equivalent checkpoints and conflict-popup
  deduplication.
- Resolution tests for keep-local, keep-remote, merge, and head changes during
  resolution.
- Case-fold and Unicode-normalization collision tests on manifests and local
  scans.
- Migration tests from the existing local state, object cache, and conflict log.
- Worker-limit tests for parallel pull and push transfers.
- Partial-error tests proving unrelated files complete and failed paths retry.
- Clock-skew tests proving mtime cannot create or suppress a conflict.
