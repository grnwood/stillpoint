# Optional Git Backup for Cloud Folder Vaults

Status: Proposed; optional future feature, not current behavior.

## Objective

Let a Cloud Folder vault use ordinary cloud synchronization for everyday edits and an optional Git repository for history, named versions, and occasional pushes.

Keep the existing top-level storage choices. This is an option under Cloud Folder, not another storage mode. Existing Local only, Homebase, and Git behavior remains unchanged. The current preferences also expose Local only alongside the three synchronization choices; this proposal does not remove or reorganize those choices.

## User model

The cloud folder holds the live notes and attachments. A separate, ordinary Git repository holds backup copies and their history. StillPoint copies vault content into that repository when saving a backup.

```text
OneDrive/MyVault/                   Live vault; cloud client syncs this
  Notes/
  attachments/

~/.stillpoint/cloud-git/<vault-id>/repo/
  Notes/                           Backup working copy
  attachments/
  .git/                            Ordinary Git history and configuration
```

The example location must be outside all cloud-synchronized folders on the device. It is a proposed default, not proof that a particular home directory is unsynchronized.

Git commands work normally inside the backup repository. Do not use a separate Git directory, a synced `.git` pointer file, or provider-specific folder exclusions. StillPoint continues to keep Cloud Folder databases, indexes, locks, and undo state device-local.

Cloud sync moves current files. Git commits record snapshots. Git push publishes committed history. Git fetch and pull do not directly update the live vault.

## Preferences and setup

Under Cloud Folder, add:

```text
[ ] Back up this vault to Git
    Backup repository: [local folder] [Browse…]
    Remote:            [SSH or HTTPS URL]
    [Initialize Repository] [Save Remote] [Test Connection]

    [ ] Publish after Save Version
    [ ] Back up changes automatically
        Frequency: [existing Git backup intervals]
        [ ] Push after automatic backup
```

The feature is disabled by default. Reuse existing Git authentication, remote configuration, backup intervals, and named-version conventions. Clearly explain that the backup repository and remote receive plaintext notes and attachments; this feature does not add encryption.

Validate the repository location before enabling backup:

- It must be a dedicated repository whose root is the selected backup folder.
- The vault and repository must not contain one another. Resolve symlinks and reject path aliases that make them overlap.
- Reject known cloud-synchronized locations. Where detection is unavailable, require the user to identify a location outside cloud sync and explain the requirement during setup. Do not claim universal provider detection.
- Never silently adopt an unrelated or populated repository. Attaching an existing backup requires checking its vault identity and reviewing the association.
- Require Git to be available and the repository to be writable before enabling save operations.

Store the enable flag, repository path, remote association, schedule, and last-operation state in device-local vault settings. Each device configures its own backup repository; do not synchronize absolute paths or credentials through the cloud folder. Recommend one designated backup computer for the initial release.

## Git UI behavior

Enable the Git tab and applicable Git actions when a Cloud Folder vault has Git backup configured. Label the tab or its header **Git Backup** and show the live vault path, backup repository path, and last successful backup/push.

| Action | Cloud Folder backup behavior |
| --- | --- |
| Status / diff | Compare eligible live vault content against the last backup commit; show changes awaiting backup even before files are copied. Also identify unexpected edits in the backup working copy. |
| Save Changes | Flush pending editor saves, capture vault content, update the backup working copy, and commit changes. |
| Save Version | Perform the same backup and create a named version using existing Git conventions. Publish only if configured. |
| History / version comparison | Read commits and tags from the backup repository. |
| Publish / push | Push existing commits and tags from the backup repository. Do not imply that uncommitted live edits have been backed up. |
| Get Updates | Fetch remote history into the backup repository. For the first release, offer fast-forward updates only when its working copy is clean; report divergence without merging automatically. Never copy fetched content into the live vault. |
| Restore | Use the explicit Restore to Cloud Vault workflow below. |

Do not enable existing actions merely by bypassing the current mode check. Route them through a backup-aware operation layer so every Git mutation targets the backup repository. Branch switching, checkout, reset, discard, and similar existing actions must either operate explicitly on the backup copy or remain unavailable until a safe, clearly labeled workflow exists.

## Snapshot and commit workflow

1. Check vault write permissions and flush editor saves. An unresolved cloud text conflict blocks backup until resolved.
2. Acquire a per-repository operation lock. Manual backups, scheduled backups, fetches, pushes, and restores must not overlap.
3. Capture eligible vault files into device-local staging with a manifest of relative paths and content hashes. Cloud Folder files must be available offline. An unreadable or unavailable file aborts the backup; it must never be interpreted as a deletion.
4. Check for files changing during capture, including additions and removals. Retry a bounded number of times, then report that the vault is changing and leave the previous committed backup intact. This detects common races but does not promise an atomic snapshot across a third-party cloud client.
5. Compare the staged manifest with the backup tree. Apply additions, modifications, and deletions only within managed content paths. Protect `.git` and backup-owned metadata.
6. Commit the resulting change set. If content is unchanged, report “No changes to back up” without creating an empty commit. Save Version may still name the existing commit according to current Git behavior.
7. Push only when explicitly requested or configured. A failed push leaves the local commit available for retry and is reported separately from backup success.

Define one shared inclusion policy for status, capture, commit, diff, and restore. Include pages, attachments, and supported portable vault content. Exclude `.git`, device-local databases, indexes, locks, undo data, caches, temporary files, credentials, and machine-specific configuration. Preserve the live cloud identity marker during restore rather than replacing it from Git. Maintain an explicit distinction between managed vault content and backup-owned files such as `.gitignore`.

Never overwrite unexpected manual edits in the backup working copy. Stop and explain how the user can commit, preserve, or otherwise resolve them. Record incomplete operations so a crash or failed copy cannot cause a later push to be presented as a successful fresh backup.

Automatic backups use the same capture and commit path while StillPoint is open. They never pull, merge, or restore in the background. Surface failures without interrupting editing repeatedly.

## Restore to Cloud Vault

Restoration is a deliberate operation because changing the live vault also changes what the cloud provider distributes to other devices.

1. Let the user select a commit/version and either specific files or all managed content.
2. Preview added, changed, and deleted files. State: “These changes will update your live cloud vault and sync to your other devices.”
3. Flush editor saves and capture a local recovery snapshot of affected live files, including files about to be overwritten or deleted. Abort if recovery capture fails.
4. Stage and validate the selected historical content before writing it. Reject paths that escape the vault or target protected metadata, including through symlinks.
5. Check that affected live files have not changed since the preview. If they have, stop and refresh the preview. Apply the confirmed restore with recoverable per-file writes, normal StillPoint change notifications, editor reloads, and index updates. Report partial failures and retain recovery data.
6. Preserve Cloud Folder identity and device-local runtime state. Offer recovery from the pre-restore snapshot if needed.

Restoring older content does not reset Git history. The restored live files become changes that the user can save as a new backup commit. Do not automatically commit or push a restore unless a separate explicit option requests it.

### Review and resolve text differences

Reuse the existing `MergeConflictDialog` in `sp/app/ui/merge_conflict_dialog.py` for text review. It already provides side-by-side comparison, Previous/Next difference navigation, Accept Change, Reject Change, Accept Both, and Accept Merge. This is a two-way comparison; do not describe it as automatic three-way Git conflict resolution.

- Label the sides **Current Cloud Vault File** and **Selected Git Backup Version**, including the selected commit or version. The current side is the live file on disk, not an independently fetched cloud-server revision.
- Offer this review for any changed text file before applying a restore, even when there is no formal Git merge conflict. Accept Change selects the backup text for that difference; Reject Change preserves the live text; Accept Both includes both segments for user review.
- Provide whole-file **Keep Current** and **Use Backup Version** choices using the dialog's existing configurable document actions. Skipping a file preserves it and allows review of other selected files.
- Accepted results remain staged until the user confirms the restore. Canceling the overall review writes nothing to the live vault. Before applying, recheck the live content against the reviewed content; if it changed, reopen review against the new live version instead of overwriting it.
- Adjust any remote/local-specific tooltips and final action wording for the restore context. The final action must clearly apply the reviewed result to the live cloud vault.
- Binary attachments cannot use the text merge dialog. Offer keep current, replace with backup, or skip, with previews where supported. Additions, deletions, and file-type conflicts require explicit file-level decisions; do not pass them through a text-only merge as if they were ordinary edits.

Cloud clients can still write files during restore; the application cannot guarantee a distributed transaction. Advise users to finish cloud synchronization and avoid opening the vault on another computer during backup or restore, consistent with existing Cloud Folder guidance.

## Lifecycle and failure behavior

- Turning backup off hides its actions and stops its schedule. Keep the repository, commits, and remote data; do not delete backups.
- Leaving Cloud Folder mode disables this integration and retains its repository. Do not silently replace it with an in-vault Git repository or reinterpret its settings as ordinary Git mode.
- An existing `.git` directory in the live cloud vault is not used by this feature. Explain that creating a separate backup does not remove that directory or stop its cloud synchronization. Do not move or delete it automatically.
- A missing backup disk, invalid path, Git failure, or unavailable network must not prevent ordinary Cloud Folder editing. Show a clear backup status and provide retry/reconfiguration.
- Push rejection must not trigger force-push or automatic merge. Explain divergence and require deliberate resolution in the backup repository.
- Multiple devices may back up locally, but pushing independent histories to the same branch can diverge. The initial release does not coordinate writers; recommend a designated backup device and make existing-history attachment explicit.
- Fetching or checking out history in the backup repository never restores the live vault implicitly. A later backup always captures the live cloud vault, so a backup repository update is not a substitute for Restore to Cloud Vault.

## Implementation direction

Represent cloud storage and optional Git backup as separate capabilities. Retain `cloud` as the active vault storage mode and introduce device-local Git-backup settings. Existing ordinary Git vaults keep their current mode and repository behavior.

Introduce an explicit Git operation context containing the live vault root, repository root, and operation kind. Use that context for main-window actions, the Git tab, automatic backups, and any folder-navigator Git controls. Avoid passing the live vault root to existing mutating Git helpers in backup mode.

Reuse Git command execution, authentication, commits, tags, history, and push support where applicable. Add a snapshot adapter for content comparison, staged copies, managed deletions, recovery, and restore. Keep cloud synchronization owned by the external provider.

## Acceptance criteria

- A Cloud Folder vault opens and edits normally with backup disabled; the existing top-level choices are unchanged.
- Enabling backup creates or explicitly attaches a normal repository outside the cloud folder; `git status` works from that repository without special flags.
- Editing a live page appears as a pending backup change. Save Changes captures it without creating or modifying `.git` in the live vault.
- New files, renames, attachment changes, and deletions are represented correctly. Missing offline files and capture races fail visibly without committing accidental deletions.
- Device-local state and credentials never enter backup commits. Restore preserves cloud identity and protected metadata.
- Manual and automatic saves, named versions, history, and pushes target the backup repository. Git failures leave cloud editing usable.
- Fetch, fast-forward, checkout, and other backup-side operations do not alter live files.
- Restore previews its scope and cloud impact, captures recovery data, detects intervening edits, and makes only confirmed live changes.
- Text restore review reuses the existing diff/merge dialog with per-change accept/reject/both decisions and clear current/backup labels. Canceling review leaves live files untouched; binary and deletion decisions are handled at file level.
- Unexpected backup working-copy edits, interrupted operations, remote divergence, disabled backup, and unavailable backup storage are handled without silent overwrite or force-push.
- Ordinary Git and Homebase vault behavior remains unchanged.

## Out of scope for the initial release

- Provider-specific `.git` exclusions or automatic cloud-client configuration.
- Git data inside the synced live vault, detached Git-directory layouts, or synchronized machine-specific pointers.
- Automatic bidirectional synchronization between Git and the cloud vault.
- Automatic multi-device Git history reconciliation, merges, or force-pushes.
- Client-side encryption of Git backup content.

This proposal should be reviewed as an optional feature before implementation; it does not authorize changes to current storage modes or Git behavior on its own.
