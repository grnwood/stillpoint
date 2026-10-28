# Encrypted Git Vaults

Status: Proposed  
Audience: StillPoint maintainers and implementation agents  
Target: A release after the initial plaintext Git integration  
Primary constraint: This mode must never commit plaintext vault content to its Git repository.

## 1. Objective

Add a fourth, mutually exclusive vault storage mode named **Git with Encryption**.

The supported modes become:

1. Local only
2. Homebase
3. Git
4. Git with Encryption

Git with Encryption uses Git for transport, ancestry, tags, and remote storage while encrypting all vault contents on the client before they enter the Git repository. StillPoint continues to edit, index, search, and render a normal plaintext working vault on the local device.

The encrypted Git repository is an opaque mirror of the working vault. It is not the working vault itself.

## 2. Product rationale

Ordinary Git over SSH or HTTPS already encrypts traffic. Server-side disk encryption protects offline physical media. Client-side encryption adds a zero-knowledge property: the Git service, its administrators, backups, integrations, and anyone obtaining Git-only credentials cannot read vault contents without the vault passphrase.

This mode primarily protects against:

- Compromise of the running Git service or hosting provider.
- Malicious or overprivileged server administrators.
- Stolen Git credentials that permit repository download.
- Accidental repository exposure or permission errors.
- Compromised repository mirrors, backup systems, or integrations.
- Provider or legal access to otherwise readable repository contents.

This mode does not protect against:

- Compromise of a device while the plaintext vault is available.
- Theft of both the encrypted repository and its passphrase/key.
- Destructive deletion of the remote repository.
- Traffic or repository metadata analysis, including size and commit timing.
- Equality leakage caused by deterministic encrypted-object reuse.

The Preferences UI must state clearly:

> Git encryption protects published repository contents. Your open vault remains stored as normal files on this device.

## 3. Non-goals

The first release does not:

- Encrypt the local working vault at rest.
- Mount a virtual encrypted filesystem.
- Use Git clean/smudge filters, `git-crypt`, or custom merge drivers.
- Allow Git itself to merge encrypted file content.
- Automatically synchronize in the background.
- Transparently convert an existing plaintext Git repository in place.
- Promise efficient storage for frequently modified large binary files.
- Expose the encrypted mirror repository as a developer-oriented interface in the main StillPoint UI.

## 4. Core architectural decision

Use two separate local trees:

```text
Plaintext working vault
        │
        │ snapshot / restore / compare
        ▼
Encrypted Git mirror repository
        │
        │ fetch / push
        ▼
Git remote
```

The StillPoint API, editor, indexer, search, task system, link system, and attachment system continue using the plaintext working vault. Encryption and Git operations belong in a synchronization adapter below those features.

Do not make the ordinary StillPoint API read and write encrypted blobs during editing. Do not place the encrypted repository inside the plaintext vault.

Recommended mirror location:

```text
~/.stillpoint/encrypted-git/<vault-id>/repo/
```

Device-local staging and recovery data should live beside it:

```text
~/.stillpoint/encrypted-git/<vault-id>/state/
~/.stillpoint/encrypted-git/<vault-id>/staging/
~/.stillpoint/encrypted-git/<vault-id>/recovery/
```

## 5. Relationship to Homebase

The encrypted Git backend should reuse concepts and carefully extracted components from Homebase:

- Passphrase-based key derivation.
- Authenticated encryption.
- Deterministic encrypted object identities.
- Vault scanning and exclusions.
- Change planning before mutation.
- Three-way plaintext comparison and conflict handling.
- Write-ahead recovery records.
- Atomic file application.
- Missing/corrupt object validation.

The desired mental model is:

> Homebase's encrypted snapshot and conflict engine with Git replacing the Homebase HTTP object service.

Do not couple the implementation directly to `HomebaseClient`. Extract or introduce backend-neutral snapshot, manifest, comparison, and recovery services where practical.

## 6. Cryptography

### 6.1 Initial algorithm

The initial implementation may reuse StillPoint's existing Homebase primitives:

- Argon2id for passphrase-based key derivation.
- XChaCha20-Poly1305 authenticated encryption.
- Keyed deterministic nonces for content objects.
- SHA-256 identifiers of complete ciphertext envelopes.

Create a new encrypted-Git envelope/version identifier rather than silently treating Homebase envelopes as the same repository format. Format evolution must be explicit.

### 6.2 Key derivation

The repository contains a plaintext vault identifier and KDF parameters. The passphrase is never stored in the repository.

Conceptually:

```text
key = Argon2id(passphrase, salt derived from vault-id and format version)
```

The implementation must support future KDF parameter upgrades without making existing repositories unreadable.

### 6.3 Deterministic object encryption

Unchanged plaintext should produce the same encrypted object within one vault so unchanged files can reuse an existing object. This intentionally leaks equality within that vault.

Document the tradeoff:

- Benefit: deduplication and cheap unchanged-file comparisons.
- Cost: the remote can observe that two snapshots reuse identical content objects.

Never reuse deterministic object identities across vault keys.

### 6.4 Manifest encryption

Manifests must be encrypted. A plaintext manifest would reveal page names, directory structure, filenames, sizes, and object relationships.

The only required plaintext repository metadata is the minimal format header.

### 6.5 Integrity and rollback

Authenticated encryption detects object and manifest modification. It does not by itself detect a valid older repository state being replayed.

StillPoint must store the last accepted remote commit locally and warn before accepting:

- A non-fast-forward remote history rewrite.
- A remote commit older than the last accepted commit.
- A repository whose vault identifier changes.
- An unsupported or downgraded repository format.

Commit signing may be supported later but is not a substitute for AEAD verification or rollback detection.

## 7. Encrypted repository format

### 7.1 Top-level layout

Conceptual layout:

```text
stillpoint-encrypted-vault.json
root
objects/
  1a/
    1a92...ciphertext
  7f/
    7f31...ciphertext
manifests/
  ab/
    ab44...encrypted-manifest-chunk
```

`stillpoint-encrypted-vault.json` is plaintext and contains only:

```json
{
  "type": "stillpoint-encrypted-git-vault",
  "format_version": 1,
  "vault_id": "stable-random-id",
  "crypto": {
    "algorithm": "xchacha20poly1305",
    "kdf": "argon2id",
    "kdf_version": 1
  }
}
```

Do not include vault paths, filenames, page titles, usernames, credentials, or passphrase verification material beyond what is cryptographically necessary.

### 7.2 Root pointer

`root` identifies the encrypted manifest root for the snapshot represented by the commit. It must not contain plaintext paths.

The root pointer can contain an encrypted-manifest object ID and format metadata. It must be authenticated, either by being encrypted or by being cryptographically bound to the encrypted manifest.

### 7.3 Content objects

Each regular vault file becomes an encrypted content-addressed object. Object paths are derived only from ciphertext hashes.

An object must not reveal:

- Original filename.
- Vault-relative path.
- MIME type.
- Page title.

The manifest provides the encrypted mapping from vault paths to object IDs and required metadata.

### 7.4 Chunked manifest / Merkle structure

Do not encrypt and commit one monolithic full-vault manifest for every snapshot. Ciphertext cannot be delta-compressed effectively, so a changing 2 MB encrypted manifest saved daily would add roughly 730 MB per year even if one small page changed.

Use content-addressed encrypted manifest chunks, preferably a Merkle tree organized by vault hierarchy or bounded entry count. Updating one page should create only:

- A new encrypted content object for that page.
- A new leaf manifest chunk.
- New parent chunks along the path to the root.

Unrelated manifest chunks must remain reusable.

A manifest entry needs, at minimum:

- Encrypted/canonical vault-relative name or a name inside the encrypted chunk.
- Entry type: file or directory.
- Child manifest object ID for directories.
- Content object ID for files.
- Size and modification metadata only if required by synchronization behavior.
- Content hash or equivalent comparison metadata only inside encrypted content.

Paths should be visible only after decrypting the relevant manifest chunks.

## 8. Vault scanning rules

The plaintext snapshot scanner must:

- Exclude `.stillpoint/` and all device-local application state.
- Respect nested-vault boundaries.
- Reject paths that escape the vault through symlinks or traversal.
- Handle Markdown, text, images, and arbitrary binary attachments.
- Use stable canonical path normalization across macOS, Windows, and Linux.
- Detect case-collision hazards before publishing or applying a pull.
- Avoid following symlinks outside the vault.

The encrypted mirror must never ingest:

- `settings.db`.
- Homebase tokens or profiles.
- Git credentials.
- Recovery records.
- AI chat databases unless explicitly made a user-visible vault feature in a separate decision.
- Local caches, indexes, locks, or temporary files.

## 9. Local state

Per-vault settings remain in the plaintext vault configuration database. Store at least:

- Mode: `encrypted_git` or equivalent.
- Mirror repository path or mirror identifier.
- Remote name and URL.
- Current branch.
- Publish-after-version preference.
- Whether a passphrase may be loaded from secure device storage.
- Last accepted remote commit.
- Last published commit.
- Last applied encrypted manifest root.
- Last successfully generated local manifest root.
- Incomplete-operation/recovery marker.

Do not store the passphrase or derived key in `settings.db` as plaintext. Use platform secure storage if the user explicitly chooses to remember it. Otherwise retain it only for the active session and clear it on vault close.

## 10. Repository recognition and setup

Preferences must recognize three repository states:

1. Empty remote/repository suitable for encrypted initialization.
2. Valid StillPoint encrypted Git repository with a supported format.
3. Existing plaintext or unknown repository.

Behavior:

- Offer initialization for state 1.
- Prefill the detected remote, branch, vault ID, and format for state 2.
- Refuse state 3. Do not add encrypted commits to an existing plaintext history.

The setup UI must explain why a plaintext repository cannot be converted safely:

> Deleting plaintext in a later commit does not remove it from Git history. Use a new empty repository for Git with Encryption.

### 10.1 Switching from ordinary Git

Switching from Git to Git with Encryption requires:

- A new empty remote or a recognized encrypted StillPoint remote.
- A new local encrypted mirror.
- An explicit warning that the old plaintext Git repository and remote retain readable history.

StillPoint must not claim to scrub or secure the old repository.

### 10.2 Connecting another device

The Open Vault flow should eventually include **Open Encrypted Git Vault…**:

1. Select or enter the remote.
2. Clone/fetch the encrypted mirror into StillPoint's private storage.
3. Validate the format header.
4. Prompt for the passphrase.
5. Authenticate and decrypt the manifest.
6. Choose/create an empty plaintext working-vault directory.
7. Materialize the snapshot atomically.
8. Initialize the local vault index and configuration.

For an MVP limited to an already-existing local vault, document cross-device connection as a required follow-up rather than pretending the workflow is complete.

## 11. Manual synchronization model

Synchronization is manual in the first release. No filesystem watcher automatically pushes encrypted snapshots.

Main actions:

- **Get Updates**: Git fetch plus StillPoint comparison/application.
- **Save Changes…**: Create a normal encrypted snapshot commit.
- **Publish**: Push encrypted commits and version tags.
- **Save Version…**: Create an encrypted snapshot and named version tag, then optionally publish.

The main UI should use user-oriented language. Raw Git commands and encrypted object filenames belong only in optional technical details.

## 12. Save Changes workflow

1. Ensure the vault is writable.
2. Flush the active editor and all pending StillPoint writes.
3. Require the encrypted-vault key.
4. Scan the plaintext vault into a candidate snapshot.
5. Reuse unchanged encrypted content and manifest chunks.
6. Encrypt new content and manifest chunks into a temporary staging area.
7. Validate every staged object's ID and decryptability.
8. Atomically place staged objects into the mirror repository.
9. Update the encrypted root pointer.
10. Create a Git commit with the user's change name.
11. Update local synchronization state only after the commit succeeds.

If there are no content or manifest changes, do not create an empty ordinary commit.

Interrupted encryption must leave the last committed encrypted snapshot valid. Unreferenced staged objects may be cleaned later.

## 13. Save Version workflow

1. Run the Save Changes snapshot process.
2. Permit version creation even if no vault content changed, provided a valid commit exists.
3. Create an annotated tag in the reserved namespace:

```text
stillpoint/version/<timestamp>-<slug>
```

4. Store the exact human version name and optional notes in the tag annotation.
5. Optionally publish the branch and encrypted-version tags.

The setting **Publish after Save Version** remains per vault.

If publishing fails, report:

> Version saved locally; publishing failed.

Do not delete the local tag or commit.

## 14. Get Updates workflow

Git must not merge ciphertext.

1. Flush or reject dirty plaintext editor state.
2. Require the encrypted-vault key.
3. Fetch the configured remote without changing the mirror working state.
4. Validate repository identity and format.
5. Check for rollback or rewritten ancestry.
6. Identify the local base, local encrypted head, and fetched remote head.
7. Decrypt only the manifest chunks and content objects required for comparison.
8. Build a complete mutation plan before changing plaintext files.
9. Create write-ahead recovery records.
10. Present destructive changes or conflicts for review when required.
11. Apply accepted changes atomically to the plaintext vault.
12. Refresh the StillPoint tree/index.
13. Update last-accepted commit and manifest state only after successful application.

Deterministic encrypted IDs should allow unchanged objects to be skipped without decryption.

## 15. Divergence and merging

When local and remote encrypted branches diverge, StillPoint performs an application-level three-way merge:

```text
base plaintext snapshot
local plaintext snapshot
remote decrypted snapshot
```

Rules:

- Reuse Homebase's material-text-difference behavior where appropriate.
- Automatically merge only cases already considered safe by StillPoint's conflict engine.
- Preserve both sides for unresolved binary or structural conflicts.
- Never ask Git to line-merge encrypted blobs or manifests.
- Never resolve divergence by force-pushing without explicit destructive confirmation.

After a successful application-level merge:

1. Materialize the resolved plaintext working vault.
2. Generate a new encrypted snapshot.
3. Create a Git merge commit with both the local and remote encrypted commits as parents.
4. Update synchronization state.
5. Let the user publish manually.

Using `git commit-tree` or equivalent plumbing may be safer than asking Git to merge the encrypted working tree. The implementation must ensure the merge commit's tree contains exactly the newly generated encrypted snapshot.

## 16. Conflict and recovery behavior

Required failure cases:

- Wrong passphrase.
- Corrupt encrypted object.
- Missing object referenced by a manifest.
- Invalid or corrupt manifest chunk.
- Remote history rewrite or rollback.
- Local plaintext changed after preview but before apply.
- Interrupted fetch, encryption, commit, pull apply, or merge.
- Insufficient disk space.
- Git authentication failure.
- Case-sensitive/case-insensitive path collision.
- Unsupported repository format.

Requirements:

- Validate before mutation whenever possible.
- Record the expected old plaintext hash before overwriting or deleting a file.
- Recheck the expected hash immediately before applying each mutation.
- Preserve recoverable local content before destructive replacement.
- Keep the encrypted mirror and plaintext vault in their last known valid states after failure.
- Make incomplete operations visible on the next vault open.
- Never treat a partial pull as a successful accepted remote commit.

## 17. Version and file history UI

When Git with Encryption is enabled, the existing vault Git UI should retain the same user-facing concepts as plaintext Git:

- Changes
- Get updates
- Save changes
- Publish
- Save Version…
- Versions…

The implementation differs internally:

- Change lists come from plaintext snapshot comparison, not `git status` against the vault.
- Diffs decrypt historical content on demand.
- Git's raw ciphertext diff is never the primary UI.
- Version history reads StillPoint version tags from the encrypted mirror.

For page/folder **Versions…**:

- **Named versions only** is checked by default.
- Named versions are vault-wide snapshots and must appear for a page if that page existed at the version, even when it did not change in that commit.
- Turning off the checkbox shows ordinary encrypted snapshot commits relevant to the item.
- Selecting an entry decrypts only the necessary historical objects and displays a plaintext diff.
- **Restore This Version** writes historical plaintext into the current working vault as an unsaved change.
- **Restore Latest Saved** restores the current encrypted head's plaintext.
- Folder restoration never automatically deletes files created later.

## 18. Mode exclusivity and warnings

Only one mode can be active. Enforce exclusivity in configuration and every entry point, not only in Preferences.

### Homebase to Git with Encryption

- Stop Homebase synchronization only after current activity/conflicts are resolved.
- Do not delete Homebase data or profiles.
- Explain that the new Git remote is independent of Homebase.

### Plaintext Git to Git with Encryption

- Report uncommitted and unpublished plaintext Git work.
- Require a separate empty or recognized encrypted remote.
- Warn that existing Git history remains plaintext.
- Do not delete the old `.git` repository automatically.

### Git with Encryption to Homebase or Local only

- Report locally saved but unpublished encrypted commits and tags.
- Offer Publish First, Switch Anyway, and Cancel where appropriate.
- Do not delete the encrypted mirror or remote configuration automatically.

## 19. Repository growth

Encrypted ciphertext is effectively random to Git. Git cannot meaningfully delta-compress one encrypted revision against another.

Approximate growth:

```text
growth ≈ sum(size of every changed file at every saved snapshot)
       + changed encrypted manifest chunks
       + Git object/commit overhead
```

Unchanged deterministic content objects are reused and should not add another full copy.

Examples:

- A 100 KB page saved daily: about 36.5 MB/year before overhead.
- A 2 MB monolithic document saved daily: about 730 MB/year.
- A 25 MB PDF replaced monthly: about 300 MB/year.
- A 100 MB presentation changed ten times: about 1 GB.
- An immutable 20 MB attachment added once: about 20 MB total, regardless of later snapshots.

Git garbage collection cannot remove objects reachable from commits or tags. Repacking provides little delta benefit for ciphertext.

The product should eventually provide:

- Estimated encrypted repository size.
- Changed-large-file warnings.
- Counts and sizes of unpublished objects.
- Optional retention/compaction workflows.
- Documentation that compaction or history rewriting is destructive.
- Possible encrypted Git LFS support for large objects, noting that LFS still stores every encrypted revision and consumes quota.

The first implementation must at least avoid the monolithic-manifest amplification described above.

## 20. Git operations and authentication

- Use the installed Git executable and existing SSH agent or credential manager.
- Never store Git passwords or access tokens in vault configuration.
- Prefer the existing `origin` remote; otherwise recognize and reuse the first configured remote.
- Disable indefinite terminal credential prompts from a headless/background process; surface an actionable authentication error.
- Run long fetch, push, encryption, decryption, and comparison operations off the Qt UI thread.
- Show cancellable progress where cancellation can leave a valid state.
- Cancellation must never interrupt the middle of an atomic plaintext replacement.

## 21. Suggested implementation structure

Names are illustrative; preserve existing project conventions.

```text
sp/sync/encrypted_git/
  backend.py          # Git fetch/push/ancestry and repository recognition
  format.py           # Header, root pointer, format versions
  objects.py          # Encrypted content object read/write/validation
  manifests.py        # Chunked encrypted manifest/Merkle implementation
  snapshots.py        # Plaintext scan to encrypted snapshot
  reconcile.py        # Base/local/remote comparison and merge planning
  state.py            # Device-local accepted/published/incomplete state
  recovery.py         # Write-ahead recovery integration
```

Prefer extracting shared Homebase logic instead of copying large sections of `sp/sync/engine.py`. Keep backend-specific transport operations behind a small interface.

Possible transport interface:

```python
class EncryptedSnapshotStore(Protocol):
    def fetch(self) -> None: ...
    def local_head(self) -> str | None: ...
    def remote_head(self) -> str | None: ...
    def read_object(self, object_id: str) -> bytes: ...
    def write_object(self, object_id: str, data: bytes) -> None: ...
    def commit_snapshot(self, root_id: str, message: str, parents: list[str]) -> str: ...
    def push(self, include_version_tags: bool) -> None: ...
```

Do not force Homebase's HTTP object calls through this exact interface if doing so harms Homebase behavior. Share the snapshot/reconciliation layer first.

## 22. Implementation phases

### Phase 1: Format and local codec

- Define header and versioned envelope formats.
- Implement content objects.
- Implement chunked encrypted manifests.
- Round-trip a vault snapshot entirely locally.
- Verify unchanged-object/chunk reuse.
- Measure growth for representative vaults.

Exit criterion: A vault can be snapshotted and reconstructed byte-for-byte without Git or UI integration.

### Phase 2: Single-device Git transport

- Initialize private mirror repository.
- Recognize empty, encrypted, and invalid/plaintext repositories.
- Save encrypted commits.
- Fetch and fast-forward encrypted commits.
- Push manually.
- Persist local accepted/published state.

Exit criterion: One device can publish and restore encrypted commits through a bare test remote.

### Phase 3: Pull planning and recovery

- Decrypt remote changes into staging.
- Preview additions, modifications, and deletions.
- Add write-ahead recovery.
- Apply atomically.
- Handle wrong passphrases, missing objects, corruption, and interrupted operations.

Exit criterion: Pull failures cannot leave either tree in an unrecognized partial state.

### Phase 4: Divergence and conflicts

- Implement base/local/remote reconciliation.
- Reuse/extract Homebase merge behavior.
- Produce encrypted Git merge commits with two parents.
- Add conflict review UI.

Exit criterion: Two devices can edit the same vault, diverge, reconcile, and publish without Git merging ciphertext.

### Phase 5: Product UI

- Add the fourth mode in Vault Preferences.
- Add setup, warnings, passphrase flow, progress, rail state, and commands.
- Add named versions, history, plaintext diff, and non-destructive restore.
- Add repository-size reporting and documentation.

### Phase 6: Cross-device open flow

- Open/clone an encrypted Git vault into a new plaintext working directory.
- Validate identity and passphrase before materializing.
- Add recovery for interrupted initial materialization.

## 23. Required tests

### Cryptography and format

- Correct passphrase round-trip.
- Wrong passphrase rejection.
- Ciphertext and manifest tamper rejection.
- Deterministic equality within one vault.
- Different ciphertext/object IDs across different vault keys.
- Format-version validation.
- KDF-version validation.

### Manifest behavior

- All supported filenames and Unicode paths round-trip.
- A one-file change reuses unrelated manifest chunks.
- Directory rename behavior is deterministic and bounded.
- Empty directories follow the chosen documented policy.
- Case collisions are rejected before apply.
- Paths cannot escape the vault.

### Git repository safety

- Empty repository initialization.
- Existing encrypted repository recognition.
- Plaintext repository refusal.
- Unknown repository refusal.
- Existing non-`origin` remote reuse.
- No plaintext page bytes appear in Git blobs, trees, commits, tags, or reflogs.
- No plaintext filenames appear outside encrypted manifests, except explicitly accepted minimal header metadata.
- No passphrase or derived key appears in repository files or logs.

### Synchronization

- Fast-forward pull.
- Fast-forward push.
- Unchanged snapshot creates no ordinary empty commit.
- Named version can tag an unchanged snapshot.
- Divergent non-conflicting text edits.
- Divergent conflicting text edits.
- Binary conflicts.
- Local deletion versus remote modification.
- Remote deletion versus local modification.
- Remote rollback/non-fast-forward warning.
- Local file changed after preview is not overwritten.

### Recovery

- Interruption during object encryption.
- Interruption before Git commit.
- Interruption after commit but before state update.
- Interruption during remote fetch.
- Interruption during plaintext apply.
- Disk-full simulation.
- Missing/corrupt object recovery.

### History and restore

- Named vault version appears for unchanged files that existed at that version.
- Historical plaintext diff is correct.
- Page restore becomes an ordinary working change.
- Folder restore does not delete newer files.
- Restore latest saved works after restoring an older version.

### Performance and growth

- Snapshot does not re-encrypt unchanged objects.
- One small page change does not rewrite the entire manifest.
- Large attachment warning threshold.
- Representative one-year growth simulation for small notes and large binaries.

## 24. Security review checklist

Before release, verify:

- [ ] No plaintext is written inside the encrypted mirror, including temporary files.
- [ ] Temporary decrypted data lives only in protected device-local staging and is removed or recoverable.
- [ ] Repository logs and UI errors do not include passphrases or decrypted content.
- [ ] Manifest paths are encrypted.
- [ ] AEAD verification occurs before using decrypted bytes.
- [ ] Object IDs are verified against complete ciphertext envelopes.
- [ ] Repository identity is bound to the expected vault ID.
- [ ] Remote rollback and rewritten history are detected.
- [ ] Plaintext Git repositories are refused.
- [ ] Git credentials are delegated to Git/SSH credential infrastructure.
- [ ] All destructive plaintext changes have write-ahead recovery.
- [ ] Nested vaults and path traversal are blocked.
- [ ] Windows/macOS case and Unicode normalization behavior is tested.

## 25. Acceptance criteria

The feature is complete when:

1. A user can enable Git with Encryption only against an empty or recognized encrypted repository.
2. The user's plaintext vault remains fully usable by all normal StillPoint features.
3. No vault content or filenames are readable from the Git repository without the passphrase.
4. Manual Save Changes, Save Version, Get Updates, and Publish work without exposing raw Git complexity.
5. Two devices can safely reconcile divergent edits without Git merging ciphertext.
6. Wrong keys, corrupt objects, missing objects, interrupted operations, and remote rewrites fail safely.
7. Page/folder version history displays plaintext diffs and restores without automatic deletion of newer files.
8. Unchanged files and unrelated manifest chunks are reused across snapshots.
9. The UI accurately explains local plaintext storage, remote protection, equality leakage, and repository growth.
10. The test suite proves that plaintext content, filenames, passphrases, and keys never enter the encrypted repository.

## 26. Open decisions

Resolve these before implementation reaches Phase 2:

1. Exact encrypted-Git envelope magic and versioning scheme.
2. Manifest chunking strategy: directory-based, bounded-entry Merkle tree, or hybrid.
3. Whether file sizes and modification times are stored in encrypted manifests or reconstructed locally.
4. Secure-storage behavior and supported platform keychains.
5. Default large-file warning threshold.
6. Initial retention/compaction policy, if any.
7. Whether encrypted Git LFS is supported initially or explicitly deferred.
8. Whether cross-device Open Encrypted Git Vault ships in the same release or the immediately following milestone.
9. Whether commit signing is offered as an optional integrity enhancement.
10. Exact behavior when the encrypted mirror commit succeeds but local state persistence fails.

Until these are resolved, do not implement an irreversible repository format without a migration/versioning plan.
