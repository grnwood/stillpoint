# Folder Navigator

**Status:** Draft for product decisions  
**Created:** September 25, 2026  
**Last revised:** September 25, 2026

## Summary

Folder Navigator is a lightweight, standalone companion to Stillpoint for browsing and editing an ordinary local folder. It should feel familiar to a Stillpoint user without treating the folder as a vault.

The app reads the filesystem directly. It does not index the folder, start or depend on the Stillpoint API, interpret vault conventions, scan tags or tasks, or write Stillpoint metadata into the selected folder.

The primary use case is reviewing a codebase or documentation tree containing Markdown, images, PDFs, and other text files.

## Product principles

- **Filesystem truth:** show the selected folder as it exists on disk, using real names and hierarchy.
- **Familiar, not identical:** reuse Stillpoint's visual language, Markdown experience, command bar, and navigation conventions only where they make sense for ordinary files.
- **Safe editing:** make unsaved work, read-only files, external changes, and save failures visible. Never silently discard or overwrite changes.
- **Independent lifecycle:** once launched, Folder Navigator must keep running if the Stillpoint window or process closes.
- **Progressive capability:** unsupported files still have a useful details view and an option to open in the system's default application.

## Goals

1. Open any user-selected local folder as a navigable tree.
2. Preview Markdown, supported text files, images, and PDFs in tabs.
3. Edit and save supported text files without Stillpoint-specific behavior.
4. Search filenames and supported text-file contents without building a persistent index.
5. Preserve the high-value navigation patterns of Stillpoint: keyboard-first tree navigation, commands for moving focus, bookmarks, and a temporary subtree filter.
6. Provide a fast, VS Code-like Quick Open flow for opening a file by name or path.
7. Work consistently on Windows, macOS, and Linux.

## Non-goals for v1

- Treating the folder as a Stillpoint vault.
- Tags, backlinks, tasks, calendar, journal, graph, AI actions, or remote/Homebase features.
- Persistent content indexing or writing cache/metadata files inside the selected folder.
- Rich editing of images or PDFs.
- IDE features such as language servers, debugging, Git integration, multi-cursor editing, or an integrated shell.
- A right-side information panel. The initial layout has only the left rail and the content area.
- Multiple folder roots in one window.
- Creating, renaming, moving, duplicating, or deleting files and folders. These are candidates for v2.

## Terminology

- **Root folder:** the folder opened in one Folder Navigator window.
- **Folder tree:** the filesystem hierarchy in the left rail.
- **Preview tab:** the single reusable, non-pinned tab used by ordinary file selection.
- **Pinned tab:** a persistent tab that is not replaced by subsequent file selection.
- **Primary modifier:** `Ctrl` on Windows/Linux and `Cmd` on macOS, except for `Ctrl+Tab`, which remains `Ctrl+Tab` on all platforms to match Stillpoint.
- **Subtree filter:** a temporary view in which the chosen folder becomes the visible root of the folder tree and search scope.
- **Folder picker:** the hierarchical popup opened from an editor with `v` in vi navigation mode.
- **Quick Open:** the flat, fuzzy file finder opened with the primary-modifier + `P`.

## Entry points and process lifecycle

### Launch from Stillpoint

Add **File > Open Folder Navigator…** to the main Stillpoint window and expose the same action in the command bar. The action opens a native folder chooser, then launches Folder Navigator as a separate OS process with the selected path.

- Closing or restarting Stillpoint must not close an already launched Folder Navigator.
- Folder Navigator must not require a running Stillpoint API/server.
- A launch failure must leave Stillpoint running and show an actionable error.
- Launching another root creates another Folder Navigator window/process. v1 does not reuse an existing window.

### Direct launch

Folder Navigator should also be directly launchable with an optional root path. With no valid path, it presents a folder chooser before showing the main window. Canceling that chooser exits without showing an empty window.

### Changing roots

**File > Open Folder…** opens the selected folder in a new Folder Navigator process. This avoids replacing a root while tabs contain unsaved changes. **File > Close Window** closes only the current Folder Navigator window, subject to the unsaved-change rules below.

## Window structure

The initial layout contains:

1. The normal application menu and a compact bookmark strip.
2. A resizable left rail with **Folder** and **Search** tabs.
3. A tabbed content area.
4. A status area for the root path, active filter, background search progress, and transient errors.

Do not include the Stillpoint right panel. Persist window geometry, splitter position, tree expansion, active left-rail tab, open pinned tabs, and the active tab per root folder. Restore only paths that still exist; report and omit stale entries without blocking startup.

When no file is open, the content area shows the root folder name and concise keyboard/opening guidance rather than a blank editor.

## Folder tree

### Contents and presentation

- The root row uses the selected folder's basename and exposes its full canonical path in a tooltip or accessible description.
- Show real nested folders and files. Sort folders first, then files, using locale-aware, case-insensitive display ordering while preserving actual names.
- Use native file/folder icons where available, with themed Stillpoint fallbacks. Qt's `QFileIconProvider` is the intended cross-platform source; exact icons may differ by OS.
- Load folders lazily so large trees do not block window startup.
- Show loading, empty, permission-denied, missing, and disconnected-volume states inline at the affected node.
- Refresh when files change on disk. Preserve selection and expansion wherever possible.
- Hidden files are hidden by default and can be toggled with **View > Show Hidden Files**.
- Do not apply `.gitignore` rules in v1; this is a filesystem browser, not a Git view.
- Do not follow directory symlinks/junctions that resolve outside the root in v1. Show them as links with an explanatory tooltip. File symlinks may be opened only when their resolved target remains inside the root.

### Selection and opening

- Single-clicking or keyboard-selecting a file opens it in the preview tab.
- Selecting a folder selects it; activation toggles expanded/collapsed state.
- Double-clicking a file, pressing `Enter` on it, or choosing **Open in New Tab** opens it in a pinned tab.
- Primary-modifier-click opens a file in a pinned tab and makes it active.
- If the path is already open, any open action activates its existing tab instead of creating a duplicate.
- A preview tab becomes pinned when the user edits it, double-clicks its tab, or invokes **Keep Open**.

This preview-tab model resolves the ambiguity between “replace the active tab” and persistent tabs: ordinary browsing replaces one preview tab, never an arbitrary pinned or dirty tab.

### Keyboard behavior

When the folder tree has focus:

- Up/Down: move to the previous/next visible row.
- Left: collapse an expanded folder; otherwise move to its parent.
- Right: expand a collapsed folder; otherwise move to its first child.
- `Enter`: activate the selected item and pin files as described above.
- `Escape`: clear the active subtree filter first; with no filter, collapse the tree.
- When vi navigation is enabled, exactly match Stillpoint's current `h/j/k/l` tree bindings: `j`/`k` move down/up and `h`/`l` collapse/expand or move to the parent/first child.

## Tabs and content area

- Show the filename in each tab and the parent path in its tooltip.
- Mark modified tabs with a visible dirty indicator that does not rely on color alone.
- Provide close buttons and context actions for **Close**, **Close Others**, **Close Tabs to the Right**, **Close Saved Tabs**, **Keep Open**, and **Reveal in Folder Tree**.
- `Ctrl+Tab` and `Ctrl+Shift+Tab` cycle open tabs in most-recently-used order and show a transient tab switcher. This replaces Stillpoint's recent-page switcher in this app.
- The platform-standard close-tab shortcut closes the active tab.
- Closing a dirty tab, closing the window, or quitting offers **Save**, **Discard**, and **Cancel**. For multiple dirty tabs, provide a single review dialog rather than a sequence of modal prompts.
- If a file changes externally while its tab is clean, reload it and show a non-modal notice while preserving the viewport when practical. If it is renamed or removed, keep the tab open in a clearly marked missing/detached state. Always retain a dirty buffer until the user resolves the conflict.

## Editor folder picker (`v`)

Preserve Stillpoint's editor `v` interaction in both the Markdown and plain-text editors when vi navigation mode is active. Rename it from the vault picker to the folder picker in this app.

- `v` opens a transient hierarchical view of the current root folder near the editor cursor.
- Preserve Stillpoint's non-vi alternative, `Ctrl+Alt+V` (using the platform's existing Stillpoint shortcut mapping).
- Initially select and reveal the active file. If there is no active file, select the current subtree-filter root or the root folder.
- The popup respects the active subtree filter.
- Use the same `h/j/k/l`, arrow, `Enter`, and `Escape` behavior as the main folder tree.
- Activating a file opens it using normal preview-tab rules, or activates its existing tab, closes the popup, and returns focus to the editor.
- Activating a folder expands or collapses it; it does not open a content tab.
- The popup reflects filesystem changes and does not maintain an independent stale snapshot.

Do not recursively scan and cache the whole folder when the window opens solely for this popup. Prefer one lazy filesystem model shared by the main tree and folder picker, with each view maintaining its own selection and expansion state. Directory entries may remain in the model's normal bounded cache after they have been loaded.

## Quick Open (`Ctrl+P` / `Cmd+P`)

The primary-modifier + `P` opens a VS Code-like file picker from anywhere in the Folder Navigator window. This shortcut is **Quick Open**, not Print, in Folder Navigator; any future print action must use a different shortcut or the File menu.

- Show a centered popup with a text field and a ranked list of matching files.
- Search relative paths as well as basenames using forgiving fuzzy matching. Rank basename matches, path-boundary matches, recently opened files, and currently open tabs ahead of weaker matches.
- Display the filename prominently and its root-relative parent path secondarily. Disambiguate duplicate basenames by path.
- Results update as the user types. Up/Down or `Ctrl+J`/`Ctrl+K` changes selection, `Enter` opens the selected file, and `Escape` closes the popup and restores prior focus.
- An ordinary `Enter` uses the preview-tab rules. Primary-modifier + `Enter` opens a pinned tab.
- Show currently open files immediately, even while the remaining candidate list is still being collected.
- By default, omit hidden files and files ignored by `.gitignore`, consistent with search defaults. Provide a visible action to include them for the current invocation.
- When a subtree filter is active, Quick Open searches that subtree by default and clearly shows the scope. A visible action can widen the current invocation to the full root.
- An empty query shows recently opened files followed by other candidates. A no-results state explains the current scope and exclusion settings.

Quick Open may use an in-memory, filename-only catalog scoped to the current root. Begin warming it asynchronously at low priority after the window becomes interactive; never delay the first paint or recursive-load the visible tree to build it. If Quick Open is invoked before warming starts or completes, prioritize the scan and stream partial results. Update or invalidate the catalog from filesystem watcher events and verify that a selected path still exists before opening it. Do not persist the catalog, read file contents, or treat it as the prohibited content index.

## File handlers

Choose a handler using detected MIME/content where practical, with the extension as a hint rather than the only test.

### Markdown

Reuse the Stillpoint Markdown editor and its floating table of contents, heading navigation, vi editing mode, find/replace, and standard text-editing behavior.

In Folder Navigator mode, disable vault-only concepts and actions, including backlinks, tags, tasks, page-name resolution, vault-relative rename/move semantics, and API calls. Standard Markdown links and images should resolve relative to the current file. A relative link to a file inside the root opens in Folder Navigator; an external URL requests the system browser; a local target outside the root requires confirmation before opening in the system application.

### Other text files

Simple text files—including `.txt`, shell scripts, batch files, source files, and configuration files—are editable in a plain-text editor when their contents can be decoded safely. Use content detection rather than treating every file with a known extension as text. Preserve the original line-ending style and encoding on save. If decoding is uncertain or fails, open a read-only explanatory view rather than displaying replacement characters and allowing a destructive save.

Syntax highlighting is out of scope for v1. There is no fixed extension allowlist: a positive text-content/encoding decision and the configured size guardrail determine editability.

The plain-text editor should not send non-Markdown files through Markdown parsing or display transformations. Prefer a dedicated `PlainTextEditor` with shared editor behavior factored out for vi mode, find/replace, dirty tracking, save coordination, and the `v` folder picker. An explicit plain-text mode inside `MarkdownEditor` is acceptable only if Markdown rendering, links, task syntax, images, floating TOC, and all vault-specific behavior are completely bypassed and the file round-trips without semantic changes.

### Images

Use a native Qt image viewer backed by `QImageReader`, which discovers built-in and installed image-format plugins at runtime. Required interactions are fit-to-window, actual size, zoom in/out, reset zoom, pan, image dimensions, and file size. Large images must be decoded/scaled off the UI thread or refused with a useful limit message.

The minimum guaranteed formats should be PNG, JPEG, GIF, BMP, and WebP when supported by the packaged Qt build. Do not promise every format solely from its extension.

### PDF

Use Qt PDF's `QPdfDocument` and `QPdfView` when the packaged PySide6 build includes them. Required interactions are continuous page view, fit width/page, zoom, page number, previous/next page, and in-document search. Password-protected, malformed, or unsupported PDFs show a useful error and **Open in Default Application**.

### Unsupported and binary files

Show a read-only details view with filename, type, size, modified time, and full path, plus **Open in Default Application** and **Reveal in File Manager**. Never render arbitrary HTML or execute active content in-process as a generic preview fallback.

Implementation references:

- [Qt `QFileIconProvider`](https://doc.qt.io/qt-6/qfileiconprovider.html)
- [PySide6 `QImageReader`](https://doc.qt.io/qtforpython-6/PySide6/QtGui/QImageReader.html)
- [PySide6 `QPdfView`](https://doc.qt.io/qtforpython-6/PySide6/QtPdfWidgets/QPdfView.html)

## Editing and save safety

- Do not autosave in v1.
- The standard Save shortcut writes the active editable file; Save All is available from File and the command bar.
- Save atomically where the platform/filesystem permits: write a sibling temporary file, flush it, then replace the target while preserving permissions where possible.
- Before saving, compare the current on-disk identity/modified state with the version loaded. If both the buffer and disk changed, offer **Compare/Review**, **Overwrite**, **Reload from Disk**, and **Cancel**; default focus is **Cancel**.
- Read-only files open read-only and explain why. Save As is out of scope unless explicitly added later.
- Surface permission errors, full disks, removed volumes, and failed atomic replacement without clearing the dirty state.

## Search

The **Search** left-rail tab performs an on-demand, non-indexed search within the root or active subtree filter.

- One query field searches both filenames and supported text-file contents.
- Results are grouped by file and show a path plus a short matching excerpt and line number for content matches.
- Activating a filename match opens the file; activating a content match opens it at the matching line.
- Search is case-insensitive by default, with toggles for case sensitivity, whole word, and regular expression.
- Search runs asynchronously, can be canceled, and streams results without freezing navigation.
- Skip unreadable files, binary files, directory links outside the root, and files above a configurable safety limit. Summarize skipped items.
- In a Git worktree, respect `.gitignore` during content search by default and provide an **Include Ignored Files** toggle. This policy affects search only; it does not hide non-hidden entries from the folder tree.
- Cap displayed results and explain when the cap is reached.
- Search results update only when the query is run again; v1 does not maintain a live index.

Search scope must be visible whenever a subtree filter is active.

## Bookmarks and subtree filtering

Bookmarks are scoped to a canonical root folder and persisted in the user's Stillpoint application configuration, never inside the browsed folder.

- A bookmark can target a file or folder inside the root.
- Activating a file bookmark opens the file in a pinned tab.
- Activating a folder bookmark reveals and selects the folder; it does not filter automatically.
- A bookmark context action, **Filter Folder From Here**, applies the subtree filter for a folder bookmark.
- The tree context menu exposes **Filter From Here** for folders.
- While filtered, the selected folder acts as the visible tree root and search scope. Show a persistent, keyboard-accessible indicator containing the folder name and **Clear Filter**.
- Clearing the filter restores the full tree and reveals the prior selection when it still exists.
- Missing bookmark targets remain visibly marked until the user removes or retargets them; do not silently delete them.

## Context menus and commands

### File

- Open / Open in New Tab
- Keep Open (preview tab only)
- Bookmark / Remove Bookmark
- Reveal in File Manager
- Open in Default Application
- Copy Full Path / Copy Relative Path
- Open Terminal Here (uses the containing folder)

### Folder

- Expand / Collapse
- Bookmark / Remove Bookmark
- Filter From Here / Clear Filter
- Reveal in File Manager
- Copy Full Path / Copy Relative Path
- Open Terminal Here

Creating, renaming, moving, duplicating, dragging, or deleting filesystem items is excluded from v1 and deferred to v2. Text-file content saves are the only filesystem mutation currently specified.

### Command bar and focus movement

Reuse Stillpoint's command-bar presentation and shortcut. Populate it only with actions available in Folder Navigator. Include commands for **Go: Folder**, **Go: Search**, **Go: Editor**, **Go: Quick Open**, **Go: Folder Picker**, **Go: Next Tab**, **Go: Previous Tab**, **File: Open Folder**, **File: Save**, **File: Save All**, **View: Show Hidden Files**, and filter/bookmark actions where applicable.

Commands must update their enabled state based on the active file, selection, permissions, and filter state.

## Accessibility and platform behavior

- Every action available by pointer must also be reachable by keyboard.
- Respect platform primary-modifier conventions and display native shortcut labels.
- Tree rows, tabs, bookmark controls, search results, dirty state, loading state, errors, and the active filter must expose meaningful accessible names/states.
- Maintain visible focus, sufficient contrast, and a non-color indicator for selection, modification, and errors.
- Do not assume that a file manager or terminal executable has a particular name. Use platform services where available and fail with an actionable message.
- Tooltips supplement labels; they must not be the only way to discover essential state.

## Performance and resilience

- Initial display must not recursively enumerate the entire root.
- Directory loading, content search, image decoding, PDF loading, and large-file checks must not block the UI thread.
- A failure in one folder or file must not make the rest of the root unavailable.
- Apply filesystem watcher updates in batches to avoid selection flicker during builds or branch changes.
- Define configurable guardrails for maximum editable text-file size, preview image allocation, searched file size, result count, and concurrent filesystem work before implementation.

## Privacy and security

- All browsing, preview, editing, and search are local in v1.
- Do not send file paths or contents to the Stillpoint API, AI providers, telemetry, or other network services.
- Do not execute files, scripts, embedded PDF actions, or active HTML content.
- Confirm before opening any local target that resolves outside the selected root.
- Canonicalize paths before enforcing root boundaries so `..`, symlinks, junctions, and case differences cannot bypass them.

## Error and empty states

The design must explicitly handle:

- Empty root folder.
- Root renamed, removed, unmounted, or permission-revoked while open.
- Folder or file permission denied.
- File too large for an internal handler.
- Unsupported or corrupt image/PDF.
- Undetectable text encoding.
- External modification while a tab is clean or dirty.
- Search canceled, partially completed, or limited.
- Terminal, file manager, or default application cannot be launched.

Errors should be placed near the affected content and copied to the status area when useful. Reserve modal dialogs for destructive or blocking decisions.

## MVP acceptance criteria

1. A user can launch Folder Navigator from Stillpoint, close Stillpoint, and continue using Folder Navigator.
2. A user can directly open a local root without starting the Stillpoint API or creating files in that root.
3. The folder tree lazily browses accessible descendants and clearly represents hidden, linked, missing, empty, and unreadable states according to this spec.
4. Preview selection replaces only the preview tab; pinned and dirty tabs are never silently replaced.
5. Markdown and approved text files can be edited and safely saved; dirty, read-only, failed-save, and external-conflict states are visible and non-destructive.
6. Supported images and PDFs render with the specified basic controls; unsupported files fall back to a safe details view.
7. Search finds filename and text-content matches without a persistent index, remains cancelable, and respects the active subtree filter.
8. File and folder bookmarks persist per root, and stale bookmarks remain visible and manageable.
9. Keyboard tree navigation, focus commands, and MRU `Ctrl+Tab` switching work without requiring a pointer.
10. Vault-only UI and API behavior do not appear or run in Folder Navigator.
11. No local target outside the canonical root is followed internally without confirmation.
12. The editor `v` picker shows the live folder hierarchy and works consistently in both supported editors.
13. Quick Open remains responsive while building its filename catalog, correctly scopes/excludes candidates, and opens preview and pinned tabs as specified.
14. Automated coverage includes tab replacement/pinning, save conflicts, symlink boundaries, search cancellation/limits, Quick Open ranking and invalidation, folder-picker navigation, stale persisted state, and child-process independence.

## Resolved product decisions

1. Markdown uses the Markdown editor; other safely decoded text files use a plain-text editing experience.
2. Tree and folder-picker vi navigation exactly match Stillpoint's current `h/j/k/l` bindings.
3. Structural filesystem operations are deferred to v2.
4. Single-click browsing uses one replaceable preview tab; deliberate opening or editing pins the tab.
5. Hidden files are off by default. Search and Quick Open respect `.gitignore` by default, while the folder tree still shows non-hidden ignored files.
6. Directory links outside the root are not traversed internally in v1; they may be opened through the system only after confirmation.
7. Layout and pinned tabs are restored per canonical root. Preview tabs and unsaved buffers are not restored.
