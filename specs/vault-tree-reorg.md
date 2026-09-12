# Vault Tree Reorganization

Status: Proposed

## Summary

Extend the existing **Reorganize Vault** workspace with a contextual Tree mode.
The workspace has three clearly separated modes—**Search**, **Tree**, and
**Review**—that all edit one shared, session-only reorganization plan.

Search remains the efficient way to discover scattered pages by title, path, or
indexed content. Tree presents physical vault containment as a map-style canvas
where a page subtree can be moved, renamed, or reordered while its surrounding
structure remains visible. Review validates the complete combined plan and shows
every affected path, Markdown link rewrite, Journal preservation edit, and index
change before Commit becomes available.

Tree mode may be opened at the vault root or scoped to any node selected in the
file navigator. A scoped canvas is a visual focus, not a move boundary: pages may
be moved elsewhere in the vault through an explicit outside-scope destination
drawer. The UI must make every move crossing the scope boundary unmistakable.

Nothing changes on disk while the user edits the canvas. Commit uses an
authoritative server-side preflight, a structural lock, content fingerprints, a
durable recovery journal, mandatory Markdown link rewriting, Journal-history
preservation, and index reconciliation. A successful operation must never depend
on the desktop client starting a later background link-rewrite job.

## Relationship to Existing Features

This feature extends rather than replaces the implementation described by the
existing Vault Reorganization specification and implemented in:

- `sp/app/ui/vault_reorg_window.py`;
- `sp/server/vault_reorg.py`;
- the `/api/vault/reorganize/*` APIs;
- `sp/app/ui/map_panel.py`, as an interaction and visual-design reference; and
- the existing tree-version, path-map, link-index, search-index, dirty-editor,
  Homebase suspension, and recovery mechanisms.

The current reorganization transaction and staged operation model remain the
single backend for Search and Tree modes. Tree mode must not introduce a second
move implementation.

`MapPanel` itself must not be subclassed as the vault organizer. Its nodes are
Markdown headings with source line numbers and its commit output is one rebuilt
Markdown document. Extract or reproduce reusable canvas behavior—layout, node
selection, pan/zoom, collapse/expand, drag targeting, ghost presentation, and
keyboard movement—in a vault-specific component whose model uses canonical
vault paths and stable staged-operation identities.

The Link Navigator remains a link-relationship viewer. Tree mode may use link
data as selected-node context, but containment is the only editable edge type.

## Product Decisions

1. Search, Tree, and Review are modes in one single-instance Reorganize Vault
   window.
2. A subtree scope controls what is initially visualized, but is not a sandbox.
   A page may be deliberately moved outside that scope.
3. Tree mode supports both parent changes and sibling display ordering.
4. Link rewriting is mandatory for a reorganization commit, regardless of the
   user's ordinary `rewrite_backlinks_on_move` preference.
5. The Journal root, calendar containers, and canonical day pages remain fixed.
   True descendants of a day page may move with existing history-preservation
   behavior.

## Goals

- Make the vault's physical hierarchy understandable while it is being edited.
- Rehome a page and its complete descendant tree with direct manipulation.
- Start with local context without preventing a deliberate vault-wide move.
- Combine Search-created and Tree-created changes in one plan and one commit.
- Provide full, bounded undo and redo for the uncommitted staging plan,
  independently of editor undo history.
- Support reparenting, simultaneous rename, and sibling display ordering.
- Preview the final structure without mutating files.
- Make stale state, outside-scope movement, large impact, and Journal effects
  visible before commit.
- Rewrite affected Markdown links and update backlink/index data as part of the
  recoverable server operation.
- Keep opening, navigating, and editing responsive in large vaults.
- Recover deterministically from a process exit or failure during every commit
  phase.
- Preserve behavior for local and Homebase vaults through server APIs.

## Non-Goals

- Editing link edges or treating backlinks as parent-child relationships.
- Automatically inventing an ideal taxonomy or using AI to rearrange the vault.
- Copying, merging, deleting, or deduplicating pages.
- Moving the vault root or protected Journal anchors.
- Supporting cyclic moves, directory swaps requiring arbitrary temporary names,
  or merging into an existing destination.
- Persisting an uncommitted plan across application restarts in the first
  release.
- Providing long-lived undo after a successful, verified commit. The recovery
  journal is for incomplete commits, not normal history.
- Rendering the entire link graph over the entire containment tree.

## Terms and Hierarchy Model

- **Hierarchy node**: a vault-relative folder node, usually backed by its
  canonical page file `<folder>/<leaf>.md`, and containing any child pages and
  attachments.
- **Page subtree**: the selected hierarchy node and everything physically below
  its folder. Moving the node moves this complete subtree.
- **Scope root**: the node chosen as the initial Tree canvas root. `/` means the
  entire vault.
- **Containment edge**: the editable physical parent-child relationship between
  two hierarchy nodes.
- **Link edge**: a Markdown relationship obtained from the link index. It is
  contextual and read-only in this workspace.
- **Original path**: the canonical folder path identifying a node when it is
  first added to the plan.
- **Final path**: the server-resolved path after every dependent staged move and
  rename has been applied.
- **Sibling order**: the display order stored for the direct children of one
  final parent. It does not reorder Markdown sections or modify page content.
- **Outside-scope move**: a move whose final parent is not the scope root or one
  of its descendants.

All client/server paths are normalized, vault-relative folder paths. Clients do
not construct or send absolute filesystem paths.

## Entry Points and Window Lifetime

### Vault-wide entry

**Vault > Reorganize Vault…** opens the single-instance workspace at `/`. It
opens Search mode by default unless the already-open workspace is being raised,
in which case its current mode, scope, and plan are preserved.

### Contextual entry

The file navigator's **Organize** section adds:

```text
Reorganize this subtree…
```

The action is available for page-backed nodes and structural folder nodes. It
opens Tree mode with that node as the scope root. On a protected Journal node,
the action is still useful: the selected Journal node is a locked scope root,
while eligible descendants remain movable.

If the workspace is already open with a staged plan, invoking this action raises
the same window, changes only the visible Tree scope, and retains the plan. The
header says `Plan retained: N staged changes` so the scope change cannot be
mistaken for clearing the plan.

The action is disabled when no vault is open. In read-only mode, the workspace
may open for inspection, but all staging, validation, and commit controls are
disabled with the standard read-only explanation.

Switching vaults or closing the workspace with a non-empty plan uses the existing
discard confirmation. Closing without a plan has no extra prompt.

## Workspace Structure

The header contains:

- the vault name;
- a segmented **Search | Tree | Review** mode control;
- `Scope: <breadcrumb path>` when Tree mode is active;
- a persistent `N staged changes` button that opens Review;
- current state: `Editing plan`, `Validation required`, `Ready to commit`,
  `Stale—validate again`, or `Recovery required`; and
- Help.

Only one primary mode is displayed at a time. The existing three-pane interface
must not simply add a fourth tree/map pane. This separation is necessary to keep
the single window understandable.

A shared footer contains **Clear Plan**, **Validate**, **Commit Reorganization**,
and **Close**. The header action row contains always-visible **Undo** and **Redo**
controls with tooltips that name the next plan action, such as `Undo: Move Idea
under Projects`. Validate and Commit are enabled only in Review mode; attempting
to commit from another mode first opens Review. Any plan change, undo, or redo
invalidates the previous preflight and plan token immediately.

### Search mode

Search retains existing candidate discovery, content-match, Journal-only,
multi-select, and destination-selection behavior. Its staged operation table
moves to Review. A compact side summary shows the most recent staged changes and
offers **Open Review**.

Search and Tree edit the exact same plan. A move staged in Search appears as a
ghost in Tree when its original or final location is visible. A move staged in
Tree appears in Search's plan summary and may be edited there.

### Tree mode layout

Tree mode contains:

- a breadcrumb and scope toolbar;
- the containment canvas;
- a collapsible node inspector;
- a global destination drawer; and
- a small legend distinguishing current containment, staged containment,
  selected-node links, protected nodes, and outside-scope portals.

The toolbar provides **Up one level**, **Use vault root**, **Fit**, **Expand one
level**, **Collapse**, **Find in scope**, and **Choose scope…**. Changing scope
does not change or clear the plan.

The canvas uses the current Map's general visual language: a root with child
branches, pan, zoom, fit, collapsible nodes, clear selection, strong valid and
invalid drop targets, and ghost structure. It uses a deterministic tree layout,
not a force-directed graph, so stable hierarchy remains readable and nodes do
not jump unpredictably.

Each node displays:

- display title or leaf name;
- a compact descendant count when known;
- protected, staged, warning, or outside-scope indicators; and
- a disclosure affordance when children are available but not loaded.

The complete path is available in the inspector and tooltip. Long labels are
elided on the canvas rather than expanding the complete layout.

### Node inspector

Selecting a node shows:

- current path and proposed final path;
- parent, child count, and total loaded/known descendant count;
- whether moving it will leave the current scope;
- whether it is a protected Journal anchor;
- proposed new name;
- current and proposed sibling position;
- validation status;
- Journal-history action, when applicable;
- indexed inbound and outbound link counts; and
- a bounded list of inbound/outbound pages with **Open page** actions.

Link information loads on selection and never blocks canvas rendering. An
optional **Show selected links** toggle draws faint, non-editable edges only for
the selected node and only to currently visible nodes. Links outside the canvas
are summarized as counts in the inspector.

### Review mode

Review is the only place where the complete plan is validated and committed. It
contains:

- the staged operation table;
- a final-structure outline for affected branches;
- blocking errors and non-blocking warnings;
- an impact summary; and
- the confirmation and progress states.

The operation table includes Action, Source, Final parent, Final name, Placement,
Scope, Journal, Link rewrites, and Status. Full values appear in escaped,
bounded tooltips. Individually valid rows say `Valid—plan blocked elsewhere`
when a plan-wide rule prevents commit.

The final-structure outline includes only affected nodes, their old and new
ancestors, and enough unchanged siblings to make placement understandable. It
must not require rendering the full vault.

## Tree Editing Behavior

### Reparenting

Dragging a node onto another eligible node stages the dragged node as the last
child of the target. The dragged node represents its complete subtree; its
descendants do not receive redundant operation rows.

The target is highlighted before drop, and a persistent description follows the
pointer:

```text
Move /Journal/2026/09/12/Idea and 8 descendants under /Projects/StillPoint
```

Invalid targets use the forbidden cursor and state the reason. Invalid drops do
not alter the plan.

Dropping a staged node again edits its existing operation rather than adding a
duplicate. Dragging an ancestor after one of its descendants has been staged
must offer to remove the now-redundant descendant operation; it must never leave
an ambiguous double move in the plan.

### Sibling ordering

Dropping above or below a sibling stages display ordering under their final
parent. A visible insertion line distinguishes ordering from dropping onto the
node. Reordering does not change paths or rewrite links by itself.

A node moved to a different parent may also carry an explicit insertion
position. If it has no explicit position, it is appended after the parent's
existing final children. Multiple staged inserts at the same position retain
their plan order deterministically.

Changing order inside a filtered or partially loaded view must never omit hidden
siblings. The server merges the requested relative placement into the complete,
authoritative sibling order during preflight.

### Rename

Rename is available from the inspector, node context menu, and Review row. It
uses the existing cross-platform leaf-name rules. A rename may be combined with
a parent change in one operation. The ghost node and every dependent final path
update immediately.

### Non-drag alternative

Drag and drop is not the only way to edit structure. **Move to…** opens the same
global destination drawer for the selected node. **Move before** and **Move
after** actions allow precise sibling placement. These controls are required for
keyboard access and for very large canvases.

### Removing staged changes

**Revert staged change** on a source or ghost removes that node's move, rename,
and explicit placement. Removing a parent operation recalculates dependent final
paths and invalidates validation. Clear Plan removes all Search- and Tree-created
operations after confirmation. Removal and Clear Plan are themselves staging
commands and may be undone while the workspace remains open.

## Staging Undo and Redo

The shared plan model owns an undo and redo history that is completely separate
from Markdown editor undo stacks, page-version history, commit recovery, and any
future filesystem undo feature. It operates only while changes are staged and
uncommitted.

Every user-level mutation of the plan creates one reversible command:

- staging one node or a multi-selected Search batch;
- moving a staged node to a different parent;
- changing its outside-scope destination;
- changing its name;
- changing sibling placement or order;
- accepting the removal of redundant descendant operations when staging an
  ancestor;
- removing one row or a multi-row selection; and
- clearing the complete plan.

A single gesture produces a single history entry even when it changes several
rows. For example, staging ten selected Search results is undone in one step,
and accepting an ancestor move that replaces three descendant operations is one
atomic compound command. Undo must never expose a half-applied compound edit.

Text typed into an inline name field is coalesced and recorded when the edit is
accepted or loses focus; individual keystrokes do not create plan-history
entries. Canceling an inline edit creates no entry. Repeated drag-hover previews
also create no entries; only the completed drop is recorded.

Undo restores the plan state immediately before the most recent command. Redo
reapplies the most recently undone command. A new plan mutation after Undo clears
the redo stack using conventional branching-history behavior. Undo and Redo:

- recompute every dependent final path and complete sibling order;
- refresh Search summaries, Tree ghosts/portals, and Review rows together;
- preserve stable operation IDs for operations that are restored;
- update selection to the most relevant restored or changed node when possible;
- mark the plan `Validation required` and discard any plan token; and
- show a concise status such as `Undid: Move Idea outside /Journal/2026/09/12`.

Validation, switching modes, changing Tree scope, expanding/collapsing nodes,
selecting nodes, searching, and opening a page are view actions and do not enter
plan history.

The history retains the newest 100 complete plan commands while keeping their
serialized command data at or below 5 MiB. When a limit is exceeded, the oldest
complete command is evicted; an atomic compound command is never split. The
newest command is always retained even if that command alone exceeds the memory
target, ensuring the user's most recent action remains undoable. These limits
are implementation constants for the first release and may later become
preferences. Plan commands store semantic deltas or compact before/after values,
not preflight impact lists, page content, canvas state, or editor text.

Undo and Redo are disabled with explanatory tooltips when their corresponding
stack is empty. Discarding the plan while closing the workspace, switching
vaults, or completing a successful commit clears both stacks. A recoverable
failed staging edit does not enter history. A failed validation leaves history
intact. A failed commit or clean rollback leaves the staged plan and its history
available unless the server reports that recovery is required.

Undo never reverses an already committed filesystem operation. After successful
commit the plan and staging history are empty; recovery and any future committed
operation history remain separate concepts.

### Shortcut routing

- **Undo** and **Redo** buttons always target the staging plan.
- With focus on the canvas, destination tree, candidate list, plan table, or
  other non-text workspace control, `Ctrl+Z` invokes plan Undo;
  `Ctrl+Shift+Z` and `Ctrl+Y` invoke plan Redo.
- While a search field or unaccepted inline name editor has text focus, standard
  text-field undo/redo remains local to that field. Accepting the name edit then
  creates one plan command.
- Shortcut routing must never reach the main Markdown editor's undo stack merely
  because the reorganization window is modeless.

## Moving Outside the Scope

A scoped tree must never imply that the user is constrained to the visible
subtree.

**Move to…** and the canvas edge drop target open a destination drawer that
searches the complete vault hierarchy. Results show full paths and an
`Outside current scope` badge when applicable. Journal visibility in this drawer
is independent of the main navigator's Toggle Journal setting.

Before staging an outside-scope move, the drawer displays:

```text
This moves the selected subtree outside /Journal/2026/09/12.
New parent: /Projects/StillPoint
```

The user confirms with **Stage outside-scope move**. This is staging
confirmation, not filesystem confirmation.

After staging:

- the source remains dimmed at its original position;
- an outbound portal card reads `Moves outside scope → <final parent>`;
- the inspector shows an amber `Leaves current scope` status;
- Review's Scope column says `Outside /<scope>`; and
- final commit confirmation reports the number of scope-crossing moves.

An outside-scope warning is informational unless another rule makes the target
invalid.

## Journal Rules

The existing Journal behavior is retained across Search and Tree modes.

The following are protected and cannot be moved or renamed:

- `/Journal`;
- canonical year and month containers;
- canonical day containers/pages of the form `/Journal/YYYY/MM/DD`; and
- the vault root.

Protected nodes have a lock indicator and cannot begin a move drag. They may be
used as a scope root or, where existing rules permit, as a destination.

A true descendant below a canonical day container may be moved normally. When
its final path leaves that day container, preflight uses normal link resolution
to determine whether the day page already links to the source or any descendant
in the moved subtree:

- an existing link is rewritten while preserving its label and anchor;
- otherwise, a link to the final page is appended beneath `# Moved Pages` in the
  day page; and
- a move that remains within the same day container adds no history entry.

Search mode retains the existing `add_reference` behavior for canonical Journal
day candidates. Tree mode does not turn a drag of a protected day page into a
reference operation; the node simply cannot be dragged.

## Staged Plan Model

The server remains authoritative for final paths and ordering. Extend the staged
operation representation to include at least:

```text
operation_id
operation_type: move | add_reference
source_path
destination_parent
new_name
placement_mode: first | last | before | after
placement_sibling_path (optional)
created_from: search | tree
scope_root
source_tree_version
validation_status
validation_message
```

`operation_id` is a session-stable client identifier used to keep selection and
ghosts stable while paths are recomputed. It is not a filesystem identity.

The pure shared plan model also exposes reversible command application,
`can_undo`, `can_redo`, next-action labels, history-size accounting, and one
change notification used by all three modes. UI widgets must not maintain
separate Search or Tree undo stacks.

Preflight returns a normalized form containing at least:

```text
destination_path
final_destination_parent
final_placement
execution_order
journal_day_path (optional)
journal_reference_action: none | rewrite_existing | append | add_reference
subtree_page_count
subtree_file_count
link_rewrite_sources
link_rewrite_count
content_fingerprints
final_sibling_orders
warnings
errors
```

Sibling ordering is returned as complete final child orders per affected parent,
not as a partial filtered list. The plan token covers normalized operations,
complete final sibling orders, the tree version, and the fingerprints of every
content file preflight expects to rewrite.

## Client-Side Immediate Validation

The UI rejects obvious mistakes before contacting the server:

- moving the vault root or a protected Journal node;
- dropping a node on itself or its loaded descendant;
- duplicate staged sources;
- an unchanged parent/name/order;
- invalid names; and
- an invalid or unloaded destination placeholder.

Client validation improves feedback but never authorizes Commit. A plan is valid
only after server preflight.

## Authoritative Preflight

Preflight evaluates the complete combined Search and Tree plan against current
disk and database state. It must:

1. Refresh a stale client tree version and normalize every path.
2. Resolve dependent destinations when a destination node is also moving.
3. Calculate final parentage and complete sibling order for affected parents.
4. Enumerate every folder, page, attachment, and metadata row affected by each
   subtree move.
5. Reparse or verify indexed links so a stale link index cannot omit a Markdown
   file that must be rewritten.
6. Calculate exact Journal edits and exact link-source content rewrites.
7. Hash every existing content file that will be rewritten.
8. Verify source existence, destination-parent existence, file permissions, and
   that every resolved path remains inside the vault.
9. Check cross-platform collision keys using Unicode normalization and
   case-folding, including case-only renames.
10. Check for an existing incomplete recovery transaction.
11. Return a deterministic impact report and plan token.

Preflight rejects:

- duplicate or ancestor-redundant sources;
- duplicate final destinations;
- containment or dependency cycles;
- moving a node into its own final subtree;
- missing sources or unresolved parents;
- overwriting or merging with an unstaged path;
- direct swaps that require unsupported temporary naming;
- invalid, reserved, or cross-platform-colliding names;
- a placement sibling that will not share the final parent;
- partial sibling orders that cannot be resolved unambiguously;
- protected Journal moves;
- writes outside the vault, including symlink escapes;
- unreadable or unwritable affected files;
- an unreadable index required to calculate link impact; and
- any active recovery-required state.

A stale input tree version is not by itself a permanent error. Preflight returns
the current version and re-evaluated plan. Commit is disabled until that returned
plan is reviewed and accepted.

## Impact Report

Successful preflight displays:

- top-level move/rename count;
- total descendant pages and total filesystem entries moved;
- sibling-order-only parent count;
- exact old-to-final path mappings;
- Markdown files whose stored links will change;
- link occurrence count, separated from file count;
- Journal pages updated and whether each update rewrites or appends;
- canonical metadata/link/task/attachment rows affected;
- search-index entries moved or refreshed;
- outside-scope move count;
- warnings for unusually large subtrees or operations; and
- the validated tree version and time.

The user may expand each category. Large lists are virtualized and may be copied
as text. Commit confirmation summarizes counts and requires the explicit
**Commit Reorganization** action; Enter in a search field or canvas must never
commit.

## Mandatory Link Rewriting

For reorganization commits, rewriting stored links to moved pages is mandatory
and independent of ordinary move preferences. This special behavior does not
change the user's global preference for other rename/move commands.

The server computes one combined old-page-to-final-page path map after resolving
all staged moves. It then rewrites every supported internal-link form in every
affected Markdown source file, including links to descendants of a moved node.
The rewrite must:

- use StillPoint's canonical link parser/resolver rather than substring
  replacement;
- preserve labels, anchors, surrounding Markdown, and newline style;
- avoid rewriting external URLs or coincidental text;
- handle a link source that is itself moved by writing to its final location;
- compose multiple path changes once so intermediate paths never leak into
  content;
- avoid duplicate Journal entries; and
- return the touched source-page paths in final-path form.

The `links` table is updated/rebuilt from final file content before success is
reported. The desktop must not call `_queue_background_link_update()` for a
successfully committed reorganization path map.

## Commit Protocol

### Before commit

The desktop saves every dirty main-editor and detached page-editor buffer that
could conflict with the operation. If any save fails, commit stops. Homebase sync
is suspended using the existing reorganization hooks. Because these saves may
change versions or content hashes, the client performs one final preflight after
the saves and before beginning mutation.

### Server transaction phases

Under the per-vault structural lock, the server performs these phases:

1. **Revalidate** — verify the plan token, current tree version, content
   fingerprints, recovery state, and all sources/destinations.
2. **Prepare recovery** — create a unique transaction directory beneath
   `.stillpoint/reorganization/`, atomically write and flush a manifest, and
   store byte-for-byte backups of every content file that may be rewritten.
   Files moved without content edits are represented by reversible move records
   rather than duplicated wholesale.
3. **Move structure** — execute dependency-ordered folder moves using batch
   primitives that do not independently finalize the overall transaction or
   repeatedly bump the public tree version.
4. **Write content** — apply canonical H1 renames, combined Markdown link
   rewrites, Journal-history additions, and Journal reference operations using
   temporary files and atomic replacement.
5. **Update metadata** — apply final page paths, display orders, link rows, tags,
   tasks, attachments, bookmarks, cursor positions, and populated search-index
   paths/content inside coordinated SQLite transactions.
6. **Verify** — confirm final paths, required page files, rewritten content
   hashes, link destinations, complete sibling orders, and primary index rows.
7. **Publish** — bump the tree version once, mark the manifest complete, flush
   it, and then remove the recovery transaction.

The public result contains the final page map, complete affected display orders,
touched content paths, Journal paths, link rewrite counts, final tree version,
and index status.

Once phase 3 begins, UI cancellation is disabled. Closing the window or losing
the client connection must not interrupt server recovery bookkeeping.

### Index failure policy

Filesystem structure, page content, and the canonical metadata/link index are
required for ordinary success. A failure before Publish triggers rollback.

The full-text search index is derived and may be disabled or intentionally
absent. If it was populated before the operation, its affected rows must be
updated synchronously. If post-write verification finds that only this derived
index cannot be reconciled safely, the server records
`committed_needs_search_reindex`, returns that explicit status, and schedules or
offers the normal rebuild. It must not silently ignore the failure or claim that
all indexes are current.

### Rollback and restart recovery

On failure, the server restores content backups and reverses completed moves in
reverse dependency order, then restores or rebuilds affected metadata from the
authoritative files. A clean rollback removes the transaction directory and
returns the original tree version plus an error.

If rollback is incomplete, the manifest remains in `recovery_required` state.
Further structural operations are disabled. On next vault open, the existing
recovery prompt offers deterministic retry. Recovery is idempotent: already
restored content and already reversed moves are recognized rather than treated
as new errors.

Homebase sync resumes only after success or clean rollback. A successful commit
marks one local structural change and triggers sync. Recovery-required state
keeps sync suspended for affected structural data until recovery completes.

## Concurrency and Staleness

The workspace is modeless and may remain open while the vault changes.

- A changed tree version marks the plan `Stale—validate again` but does not
  discard it.
- Reloading tree branches preserves scope, expansion, selection, and the plan
  when their source nodes still exist.
- A missing or newly conflicting source receives a row-specific error.
- Content fingerprints protect Markdown files that will be rewritten even when
  the hierarchy itself did not change.
- Structural mutation is serialized with ordinary moves, renames, deletes,
  Homebase structural application, and other reorganization commits.
- App-originated writes to affected content use existing per-file content locks.

If a commit request loses its response, the client queries transaction status by
transaction ID before retrying. It must not submit a second equivalent mutation
blindly.

## Performance Requirements

Tree mode must not fetch or render the entire vault recursively on open.

- Open the scope with bounded depth and `include_journal=true` using the existing
  tree API/cache where possible.
- Fetch direct children lazily on expand. Extend the tree API with bounded depth
  or pagination/continuation metadata when a parent has too many direct children
  for one response.
- Preserve and reuse branch data while the tree version is unchanged.
- Load link details and page excerpts only for the selected node.
- Perform layout outside the GUI thread and discard results from superseded
  generations.
- Incrementally relayout affected branches after a staged edit where practical.
- Cull off-screen nodes and edges from painting and hit testing.
- Virtualize long Search, Review, destination, and impact lists.
- Debounce text search and never allow an older response to replace a newer one.
- Show progressive descendant counts (`23+` or `calculating…`) rather than
  recursively scanning on node hover.

Acceptance testing uses synthetic vaults with at least 10,000 pages, deep
nesting, and high-fanout parents. Opening a bounded scope, panning/zooming, basic
selection, and staging a move must not synchronously block the GUI for more than
100 ms on the reference development machine. Full preflight may take longer but
runs with determinate phase/progress reporting and a responsive window.

## Progress and Failure Presentation

Validation progress uses named phases such as `Resolving final tree`, `Scanning
links`, and `Checking writes`. It may be canceled because no mutations have
started.

Commit progress uses `Preparing recovery`, `Moving structure`, `Rewriting
links`, `Updating indexes`, and `Verifying`. It becomes non-cancelable when
mutation starts.

Errors include:

- a short user-facing summary;
- the affected operation/path when known;
- whether no changes were made, rollback completed, or recovery is required;
- an expandable technical detail suitable for support; and
- the next safe action.

A derived search-index repair is shown as `Reorganization committed; search
index repair required`, never as an unqualified failure or success.

## Accessibility and Keyboard Behavior

- Mode controls, canvas nodes, inspector fields, destination results, plan rows,
  and footer actions participate in a predictable tab order.
- Every drag action has a keyboard/menu equivalent.
- Undo and Redo expose their next plan action in visible tooltips and accessible
  names.
- Focused nodes and valid/invalid targets are not distinguished by color alone.
- Protected nodes expose `Protected Journal anchor` to accessibility APIs.
- Outside-scope destinations expose `Outside current scope` in visible and
  accessible text.
- Arrow keys move between visible related nodes; Left/Right collapse or expand;
  Enter selects; Shift+Enter opens the page; the Context Menu key opens node
  actions.
- `Ctrl+1`, `Ctrl+2`, and `Ctrl+3` switch Search, Tree, and Review.
- `Ctrl+Z` performs plan Undo and `Ctrl+Shift+Z` or `Ctrl+Y` performs plan Redo
  when focus is not in an active text field.
- Escape cancels an active drag/menu or clears the active search. It never
  commits or silently discards the plan.
- Commit has no automatic/default-button behavior.

## API Changes

Extend the existing reorganization payload rather than creating a Tree-only
commit endpoint.

### Tree data

Use `/api/vault/tree` and `/api/vault/tree/expand-path` for initial and lazy tree
data. Add bounded-depth and continuation parameters only where needed for large
branches. Responses used by Tree mode must include or permit deriving:

- canonical folder path;
- page/open path when present;
- display title and leaf;
- direct-child availability/count;
- protected Journal status; and
- tree version.

### Preflight

`POST /api/vault/reorganize/preflight` accepts placement, scope, and stable
operation IDs in addition to existing move/reference fields. Its normalized
response adds final sibling orders, content fingerprints, exact link impact,
subtree counts, warnings, and a transaction-ready plan token.

### Commit and status

`POST /api/vault/reorganize/commit` accepts only the staged intent plus the
server-issued tree version and plan token. The server recomputes and compares
the normalized plan; it never trusts client-calculated final paths or impact.

Commit returns a transaction ID. Add a read-only transaction-status route so a
client can resolve an interrupted response without resubmitting the mutation.
Recovery status remains available through the existing recovery routes.

## Implementation Boundaries

Recommended UI separation:

- `VaultReorgWindow`: shared mode, plan, validation, and commit coordinator;
- `VaultReorgSearchPage`: existing discovery and destination workflow;
- `VaultTreeReorgPage`: scope toolbar, canvas, inspector, and destination drawer;
- `VaultReorgReviewPage`: operation table, final-tree preview, and impact report;
- `VaultTreeCanvas`: vault-specific scene/layout/interaction component; and
- a pure plan model shared by all three pages, owning a bounded command-history
  component.

Recommended server separation:

- pure normalization and final-tree planning;
- exact impact and link-rewrite planning;
- recovery-journal persistence;
- batched filesystem/content execution;
- coordinated metadata/index application; and
- idempotent verification/recovery.

Pure planning code must be testable without Qt or an HTTP server. The server
must remain the only layer that resolves absolute paths and mutates vault files.

## Testing Requirements

### Plan and hierarchy tests

- Reparent a leaf, a deep subtree, and a structural folder.
- Rename while reparenting.
- Reorder first, middle, and last siblings without path changes.
- Move and reorder into a parent that is also moving.
- Preserve hidden siblings when ordering from a partial view.
- Re-edit and remove staged operations without duplicates.
- Reject ancestor redundancy, cycles, collisions, missing paths, invalid names,
  unsupported swaps, and placement anchors under the wrong final parent.
- Resolve one deterministic final tree and execution order regardless of mode.
- Undo and redo every supported plan mutation from both Search and Tree.
- Treat batch staging, multi-row removal, ancestor replacement, and Clear Plan
  as indivisible history commands.
- Coalesce an accepted inline rename and exclude canceled/hover-only edits.
- Clear redo after a new mutation following Undo.
- Evict only complete oldest commands at the 100-command and 5 MiB limits.
- Preserve operation IDs and recompute dependent paths/order through undo/redo.
- Invalidate a successful preflight after undo or redo while preserving history
  after failed validation and clean commit rollback.

### Scope and UI tests

- Open Tree mode from the vault root and from a navigator subtree.
- Retain a plan when the visible scope changes.
- Show and confirm an outside-scope destination before staging.
- Show the outside portal, inspector warning, Review status, and commit count.
- Reflect Search operations in Tree and Tree operations in Search/Review.
- Preserve selection/expansion across lazy loads and a refreshed tree version.
- Verify keyboard alternatives for move, order, rename, validate, and review.
- Verify focus-aware shortcut routing between plan history, search fields,
  inline rename fields, and the main Markdown editor.
- Verify read-only inspection and disabled mutation.

### Journal tests

- Lock Journal root, year, month, and canonical day nodes.
- Allow a protected node to be used as the contextual scope root.
- Move a true day descendant within the day without adding history.
- Move it outside the day and rewrite an existing resolved link.
- Add exactly one `# Moved Pages` entry when no suitable link exists.
- Preserve Journal link label/anchor and prevent duplicate history entries.
- Retain Search-mode `add_reference` behavior.

### Link and content tests

- Rewrite supported wiki, Markdown, and colon links to every moved descendant.
- Preserve labels, anchors, formatting, UTF-8 content, BOM, and newline style.
- Do not rewrite external URLs or plain-text false positives.
- Correctly rewrite a link source that also moves.
- Compose chained moves to final paths without intermediate targets.
- Rewrite links even when the ordinary move preference is disabled.
- Reject commit when an affected content fingerprint changed after validation.
- Confirm the desktop does not queue a second background rewrite.

### Transaction and recovery tests

- Inject failure after every commit phase and after representative individual
  moves/content writes.
- Verify clean rollback restores folders, file bytes, order, and indexes.
- Restart with each incomplete manifest state and recover idempotently.
- Reject concurrent structural operations and stale/altered plan tokens.
- Resolve a lost client response through transaction status without duplicate
  mutation.
- Suspend/resume Homebase correctly on success, clean rollback, and
  recovery-required outcomes.
- Verify one public tree-version bump on success and none after clean rollback.
- Surface canonical-index failure and derived-search-index repair distinctly.

### Performance tests

- Exercise a 10,000-page vault and high-fanout parents without a recursive
  initial fetch.
- Confirm stale asynchronous layout/search results are discarded.
- Confirm off-screen canvas nodes are culled.
- Measure initial bounded-scope display, expand, pan/zoom, selection, and staging
  against the UI responsiveness budget.
- Validate progress and bounded memory use for a large move/link-impact scan.

## Acceptance Criteria

The feature is complete when a user can right-click a subtree, understand its
physical context in a map-style Tree view, stage subtree moves and sibling
ordering—including a clearly identified move outside the scope—freely undo and
redo staging decisions, review exact effects, and commit once with no partial or
deferred backlink work.

A reported successful commit leaves final filesystem paths, canonical page
files, stored Markdown links, Journal history, display order, backlink metadata,
and required indexes mutually consistent. Any interruption either rolls back
cleanly or leaves a durable, visible, retryable recovery state that blocks
further structural mutation.
