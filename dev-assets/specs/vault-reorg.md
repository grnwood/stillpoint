# Vault Reorganization Workspace

## Status

Implementation-ready specification for the first release.

## Summary

Add a dedicated **Vault Reorganization** workspace for staging and committing page moves at larger scale. The workspace combines page-title/path discovery, an unfiltered vault hierarchy, drag-and-drop destination selection, optional renaming, validation, and a reviewable operation queue.

Reorganization changes where pages live without erasing their Journal history. When a staged page is removed from beneath a Journal day and the day page does not already contain a link into that moved subtree, StillPoint appends a link to the final page location under an H1 heading named `# Moved Pages`. If a suitable link already exists, the normal backlink rewrite updates it and no additional link is inserted.

Canonical Journal day pages remain immutable historical anchors. When discovery matches content in `/Journal/YYYY/MM/DD/DD.md`, staging it creates a reference from the selected topic page back to that Journal entry; it never moves or copies the Journal day folder. True subpages beneath the day remain eligible for normal rehoming.

All moves remain staged and have no filesystem effect until the user explicitly applies the plan.

## Goals

1. Make it practical to find and rehome related pages across a large vault.
2. Let users arrange and revise a multi-page move plan before changing files.
3. Support changing a page's parent and leaf name in the same staged operation.
4. Preserve Journal-day evidence when a descendant is rehomed.
5. Reuse the existing move, H1 synchronization, path-map, backlink rewrite, dirty-editor, tree-index, and search-index behavior.
6. Preflight the complete plan and prevent partial or ambiguous operations.
7. Work for local and Homebase vaults through server APIs without direct UI filesystem access.

## Product decision: dedicated workspace, not the existing Map

Do not turn the current page Map panel into a filesystem organizer. `MapPanel` represents headings within one Markdown page, while reorganization operates on page containment and cross-page links. The Link Navigator graph represents link relationships but does not provide a clear bulk destination hierarchy.

The first release uses a purpose-built tree/list workspace. Existing tree, drag-and-drop, search-result, and move-dialog patterns may be reused. A future graph preview may display containment and Markdown links as distinct edge types, but graph editing is outside this release.

## Entry points and window behavior

Add a **Vault > Reorganize Vault…** action. Because menu actions populate the command palette, it must also appear as **Vault / Reorganize Vault…** there.

Open a modeless, single-instance window owned by the current main window. Triggering the action while it is open raises and activates the existing window. Closing the main window closes the workspace. Switching vaults with a non-empty staged plan requires the same discard confirmation described below.

Disable the action when no vault is open. In read-only mode, the workspace may open for discovery and inspection, but staging and applying changes are disabled with the normal read-only explanation.

The workspace title is `Reorganize Vault — <vault name>`.

## Workspace layout

Use three primary regions and a bottom action row.

### Candidate pane

The left pane contains:

- a search field with placeholder `Find pages by title or path…`;
- a **Content matches** checkbox, off by default;
- a **Journal pages only** checkbox, off by default;
- a result count;
- a multi-select candidate list.

Each result displays its title, current vault-relative path, and a Journal/date indicator when applicable. Content matches additionally display the existing search snippet. Results are draggable as a group.

Canonical Journal day results are labeled **Journal entry — add reference** so they cannot be mistaken for movable pages.

Candidate rows use the active theme's alternating base colors so adjacent multi-line results remain visually distinct in both light and dark themes.

An empty query shows no candidates rather than the whole vault. Search is debounced and stale responses must not replace results for a newer query.

### Destination pane

The center pane shows the complete indexed vault hierarchy. It is independent of the main file navigator's filter and **Toggle Journal** setting: Journal remains available here as both a source and destination.

A type-ahead field filters destinations by case-insensitive name or path while retaining matching ancestors. A **Show staged paths only** toggle limits the hierarchy to staged sources, destination parents, and the ancestors needed to understand their location. Staging or editing a row must preserve the hierarchy's selected destination and expanded/collapsed state so repeated moves can target the same area without reopening it.

Dropping one or more candidate pages on a page/folder stages that node as their destination parent; it does not move files. The vault root is a valid destination parent. Invalid drops, including a source onto itself or its descendants, are rejected immediately.

The tree provides a visual preview:

- staged source nodes are dimmed and marked as moving;
- proposed destination nodes appear as italic/ghost children under their new parent;
- selecting a staged source or ghost selects the corresponding plan row;
- removing or editing a plan row updates the preview immediately.

Dragging a hierarchy node directly to another destination is also allowed and stages the same operation as dragging a search result.

### Staged-changes pane

The right pane contains one row per top-level staged operation with these columns:

| Column | Behavior |
| --- | --- |
| Operation | `Move` or `Add reference` |
| Source | Read-only current path |
| Destination | Read-only staged parent for moves or target topic page for references |
| New name / reference note | Editable move name, or the search term/short context stored beside a Journal reference |
| Journal history | `Existing link will update`, `Will add to # Moved Pages`, or blank |
| Status | Valid, warning, or blocking validation message |

Changing **New name** updates the ghost preview and destination path. It uses the same cross-platform name rules and backend validation as manual and AI rename. Moving to another parent while retaining the name and moving while changing the name are both supported.

Provide **Remove**, **Clear Plan**, and **Validate** controls. Multi-row removal is supported.

**Clear Plan** also reloads the destination hierarchy and reruns the active candidate query against the current index. A successful commit performs the same refresh automatically, so moved pages disappear from old-path results without requiring the workspace to be reopened.

### Bottom action row

Show a concise summary such as `4 pages staged; 2 Journal references will be added`, followed by **Apply Reorganization** and **Close**.

**Apply Reorganization** is disabled when the plan is empty, validation is pending, any row has a blocking error, the vault is read-only, or another structural operation is active.

Closing with staged changes prompts:

```text
Discard the staged reorganization plan?
```

The options are **Keep Working** and **Discard Plan**. Plans are session-only in this release.

## Candidate discovery

### Default title/path search

Default search must not depend on the optional full-text search index. Query indexed page metadata and match case-insensitively against:

- the page leaf name;
- the display title, using the first nonblank ATX H1 when available and otherwise the leaf;
- the full vault-relative path.

All whitespace-separated query terms must match at least one of those fields. Rank exact title/leaf matches first, title/leaf prefixes second, title/leaf substrings third, and path-only matches last. Use deterministic path ordering to break ties.

Return pages, not standalone filesystem directories. A page result represents its complete page folder/subtree when moved.

### Optional content matches

When **Content matches** is enabled, merge results from the existing full-text search API/index. Deduplicate by canonical page path. Title/path results retain priority; content-only results follow in full-text rank order and display their snippet.

If the search index is unavailable or empty, retain title/path results and show a non-blocking message explaining that content matches require a search index. Do not automatically start a rebuild from this workspace.

### Journal filtering

When **Journal pages only** is checked, restrict results to `/Journal` descendants. This filter applies to both metadata and content matches but does not hide Journal from the destination tree.

Exclude the vault root, missing index entries, filter banners, virtual pages, attachment files, and `.stillpoint` metadata.

## Staged operation model

Represent each operation with at least:

```text
source_path
destination_parent
new_name
destination_path
source_page_path
source_tree_version
journal_day_path (optional)
journal_reference_action: none | rewrite_existing | append
validation_status
validation_message
```

Paths are canonical vault-relative folder paths. The destination path is derived from `destination_parent` plus `new_name`; clients must not construct absolute filesystem paths.

Selecting a page that contains descendants moves the complete subtree. Do not add separate rows for descendants implicitly included by an already staged ancestor. If an ancestor is staged after one of its descendants, prompt to remove the redundant descendant row or reject the ancestor staging action.

The plan may use a destination that is itself being moved, provided the final hierarchy is acyclic and the server can calculate a dependency-safe order. Reject:

- duplicate source rows;
- duplicate final destinations;
- moving the vault root;
- moving a page into its own final subtree;
- source/destination cycles;
- unresolved or missing destination parents;
- overwriting or merging with an existing unstaged path;
- no-op rows whose parent and name are unchanged;
- invalid or reserved names;
- sources that no longer exist.

Moving an existing destination out of the way earlier in the same acyclic plan may make that destination available. Direct swaps that require temporary hidden names are outside this release and must be rejected with an actionable error.

## Journal-history preservation

### Protected Journal anchors and reference operations

The Journal root and canonical year, month, and day containers cannot be moved by reorganization. This prevents a content match in a day page from relocating the historical entry or its complete subtree.

Staging a canonical day page creates an `add_reference` operation targeting an existing selected topic page. Commit appends one entry under an H1 named `# Journal References` in that topic page:

```markdown
# Journal References

- [Journal:2026:08:01|Saturday 01 August 2026] — MSC
```

The link label comes from the Journal page H1. For the optional note, candidate discovery first finds the best matching ATX heading (`#` through `######`) containing every search term and uses that heading's exact text. Exact and prefix heading matches rank ahead of other heading matches. Only when no heading matches does the note fall back to the active candidate query. Existing identical entries are not duplicated. The source Journal page and folder remain untouched. Selecting the vault root is invalid because this release requires an existing target page; automatic topic-page creation may be added separately.

### Eligible moves

For each top-level staged source, detect whether its source path is beneath a canonical Journal day folder of the form:

```text
/Journal/YYYY/MM/DD
```

The day page is the canonical page file for that folder, normally:

```text
/Journal/YYYY/MM/DD/DD.md
```

Journal-history preservation applies when the final destination is no longer inside the original day folder. A rename or rearrangement that remains beneath the same day does not add a moved-page entry.

### Existing-link detection

Before moving files or rewriting links, inspect links originating in the day page. Treat the Journal history as already preserved when the day page contains a resolvable Markdown link to the staged source page or any descendant in its moved subtree. This must use StillPoint's normal link resolution rather than raw substring matching and must recognize all supported internal-link forms.

When such a link exists, set the plan row to `rewrite_existing`. The normal combined backlink rewrite updates its target after commit. Preserve its existing label.

### Appending a missing reference

When no qualifying link exists, set the row to `append`. After the filesystem moves succeed, add a bullet linking to the final root page of the moved subtree.

Use StillPoint's normal internal wiki-link syntax and a readable label based on the final page name, for example:

```markdown
# Moved Pages

- [Topics:Topic_A|Topic A]
```

If an exact `# Moved Pages` H1 already exists, append the bullet within that section before the next H1. Otherwise, append a blank-line-separated `# Moved Pages` section at the end of the day page. Reuse the section across multiple operations from the same day and insert each final target at most once.

Preserve the day page's encoding, newline style, and final-newline convention where practical. Do not alter other headings or content. The generated reference is mandatory for eligible moves; if the day page is missing, cannot be resolved, is read-only, or cannot be safely written, preflight fails rather than silently losing Journal history.

If multiple selected source rows overlap semantically, validation must ensure the day page receives only the minimal top-level final links represented by the staged rows.

## Validation and preview

Add a server-side preflight API for the complete plan. The client performs inexpensive immediate checks for feedback, but server validation is authoritative.

Suggested API:

```text
POST /api/vault/reorganize/preflight
```

The request contains the ordered user plan and the tree version captured when staging began. The response contains:

- normalized operations and final paths;
- dependency-safe execution order;
- per-row errors and warnings;
- the composed final `page_map` preview;
- Journal day paths and `rewrite_existing`/`append` decisions;
- destination collisions and structural conflicts;
- backlink counts when cheaply available from the main index;
- current tree version and whether the submitted version changed.

Validation must be rerun whenever a source, destination, or name changes. Debounce repeated validation while typing. A successful validation produces an opaque plan token or normalized-plan digest so commit can detect a stale or altered request.

A stale version supplied to preflight is not by itself a blocking error: preflight validates against the current vault, refreshes the hierarchy, and returns a token bound to that current version. Missing sources, collisions, cycles, and other conflicts discovered in the current structure remain blocking. This prevents repeated “validate again” loops while retaining commit-time stale-plan protection.

Before commit, show a confirmation dialog containing:

- the number of top-level moves and total affected page files;
- every source-to-final-destination mapping;
- the number and paths of Journal pages that will receive new references;
- warnings that do not block commit;
- a reminder that existing links will be rewritten.

## Commit and recovery

Add an authenticated write API such as:

```text
POST /api/vault/reorganize/commit
```

Commit accepts the normalized plan plus its preflight token/digest. The server reruns authoritative validation immediately before mutation. Reject stale plans if relevant paths or the structural tree version changed; return row-specific errors so the workspace can be refreshed without losing the plan.

Before calling commit, the UI saves every dirty main or detached editor whose page may be moved, rewritten, or used as a Journal day page. If any save fails or remains dirty, abort without sending commit. The UI performs one final preflight after those saves so a watcher or just-finished sync cannot invalidate the confirmation-time token before the commit request is sent.

The server commit coordinator must:

1. acquire one vault-scoped structural-operation lock;
2. rerun preflight;
3. persist a recovery manifest containing normalized operations, original/final paths, affected Journal page backups or revisions, and progress state;
4. execute moves in dependency-safe order using the existing shared move primitive with per-operation link rewriting disabled;
5. compose every returned page map from original paths to final paths;
6. append required Journal references using the final targets;
7. update indexed link paths once with the composed map;
8. bump/invalidate tree state as one logical reorganization result;
9. mark the recovery manifest complete;
10. return the composed `page_map`, display orders, touched Journal paths, final tree version, and operation summary.

After a successful response, the UI applies the composed path map to current pages, history, bookmarks, and open editor windows; refreshes the vault tree and staged workspace; and queues one existing background on-disk backlink rewrite using the composed map. Clean open pages reported as touched are reloaded without adding history entries.

Do not overwrite existing destinations, auto-suffix names, merge directories, or continue after an operation failure.

If a move or Journal write fails after mutation begins, automatically apply inverse moves in reverse order and restore Journal page backups. Retain and report the recovery manifest if rollback is incomplete. On next vault open, detect an incomplete manifest and present a repair/recovery prompt before allowing another structural operation. This recovery mechanism is not a general user-facing undo history.

Only one move, rename, delete, or reorganization commit may hold the vault structural-operation lock at a time. Background backlink rewriting begins after the structural lock is released.

## Progress, cancellation, and errors

Commit displays determinate progress by completed operation plus final Journal/index phases. Cancellation is allowed only before the server begins filesystem mutation. Once mutation begins, the dialog may be hidden but the operation cannot be canceled.

Errors must identify the affected source and leave the staged plan open. Collision and stale-plan errors should select the corresponding row. Authentication, permission, and network errors use existing presentation patterns.

Closing the workspace during an active commit does not stop the server job. The main window continues polling and reports completion or recovery failure.

## Suggested implementation areas

- `sp/app/ui/vault_reorg_window.py` — workspace, candidate list, destination tree, staged-plan model, validation, review, and progress UI;
- `sp/app/ui/main_window.py` — Vault menu action, single-instance lifecycle, dirty-editor coordination, path-map reconciliation, and background backlink update;
- `sp/server/api.py` — candidate, preflight, commit, status, and recovery endpoints/models;
- `sp/server/file_ops.py` — batch coordination, composed path maps, inverse operations, Journal reference edits, and structural locking;
- `sp/server/search_index.py` or indexed metadata helpers — title/path candidate query and optional content-result merge;
- `sp/app/config.py` — only if lightweight window geometry is persisted; staged plans are not preferences.

Prefer extracting reusable move/path-map helpers over driving the existing single-page move dialog programmatically. The server remains the authority for paths, conflicts, permissions, and commit ordering.

## Tests

### Candidate search

- empty query returns no results;
- title and leaf matches rank ahead of path-only matches;
- default results work without a full-text index;
- content-only results appear only when **Content matches** is enabled;
- metadata and content results deduplicate by canonical path;
- Journal-only filtering applies without depending on navigator visibility;
- stale asynchronous searches cannot replace newer results.

### Staging and UI

- dragging one or multiple candidates stages operations without API mutation;
- the full destination tree includes Journal when main navigation hides it;
- editing **New name** updates final paths and ghost nodes;
- redundant ancestor/descendant rows are rejected or explicitly resolved;
- removing and clearing rows update the preview;
- closing or switching vaults with a non-empty plan requires confirmation;
- the menu action is single-instance and appears in the command palette.

### Validation

- reject root moves, cycles, descendant moves, duplicate sources/destinations, invalid names, no-ops, missing parents, and collisions;
- allow a destination vacated earlier by an acyclic staged operation;
- calculate dependency-safe ordering;
- reject direct swaps requiring temporary paths;
- stale tree/path state invalidates preflight tokens;
- read-only and insufficient-permission states cannot commit.

### Journal preservation

- moving a direct or deeper day descendant outside its original day is eligible;
- moving or renaming within the same day is not eligible;
- an existing supported link to the source or moved subtree is detected before moves and rewritten without adding a bullet;
- custom labels on existing links are preserved;
- a missing link creates `# Moved Pages` at the bottom with the final target;
- an existing exact section is reused and duplicate targets are not inserted;
- multiple moves from one day create one section with one link per staged root;
- a missing or unwritable day page blocks commit;
- Journal edits preserve unrelated content and newline conventions.

### Commit and recovery

- preflight performs no filesystem or index mutation;
- commit moves and optionally renames multiple subtrees and returns one composed original-to-final page map;
- H1 synchronization follows the existing conservative rename rule;
- dirty affected editors are saved before commit and failed saves abort;
- backlink rewriting is queued once after the batch rather than once per operation;
- current path, history, bookmarks, tree selection, and open windows reconcile through the composed map;
- failure after one or more moves applies inverse operations and restores Journal pages;
- incomplete rollback retains a detectable recovery manifest and blocks another structural operation;
- Homebase/authenticated APIs enforce write permissions.

## Acceptance criteria

1. **Vault > Reorganize Vault…** opens one modeless workspace and is available through the command palette.
2. Users can find pages by title/path without a full-text index and explicitly enable content matches.
3. The destination hierarchy always includes the complete vault, including Journal when hidden in the main navigator.
4. Users can stage multiple drag-and-drop moves, revise destination parents, and optionally rename every staged page without changing files before commit.
5. The workspace previews sources, ghost destinations, Journal-link actions, and all blocking conflicts.
6. A complete server preflight rejects unsafe, stale, cyclic, colliding, or ambiguous plans before mutation.
7. Moving a page out from beneath a Journal day preserves history: an existing day-page link is rewritten, otherwise one final-target link is placed under a single `# Moved Pages` H1.
8. Commit returns and applies one composed path map, preserves conventional H1 synchronization, and queues one durable Markdown backlink rewrite.
9. A failed batch rolls back completed moves and Journal edits; incomplete recovery is detected and reported before another structural operation.
10. Focused candidate, staging, validation, Journal preservation, commit, recovery, navigation, move, rename, and backlink tests pass.

## Non-goals

- modifying the current heading Map panel or Link Navigator graph for reorganization;
- automatic AI classification or AI-selected destinations;
- automatic reorganization without an explicit staged plan and confirmation;
- merging pages or directories;
- overwriting destinations or silently suffixing collisions;
- deleting pages;
- persistent saved reorganization plans;
- direct cyclic swaps through temporary hidden paths;
- a general user-facing undo/unrename history;
- bulk tag or page-content editing beyond required Journal references;
- rebuilding the full-text index automatically.
