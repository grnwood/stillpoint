# Folder Navigator

Folder Navigator is StillPoint's detached, keyboard-first browser and editor for an ordinary folder. It is **Ranger-inspired**, meaning it is designed for fast filesystem movement and a keyboard-driven flow; it is not a clone of Ranger's column layout.

## Where It Fits

Your StillPoint vault is the home base for durable notes, decisions, journal entries, tasks, and links. A code repository or client folder often has a structure of its own that should not be rearranged into StillPoint pages.

Folder Navigator bridges those worlds:

1. Keep the thinking and project record in your vault.
2. Open the external working folder in Folder Navigator.
3. Browse, search, preview, and edit its files with familiar StillPoint behavior.
4. Move back to your vault for notes and context without importing or converting the external folder.

Each root opens in a separate process and has a folder-badged StillPoint application icon. Closing the main StillPoint window does not close an already running Folder Navigator.

## Opening and Pinning a Folder

From StillPoint:

- **File -> Open Folder Navigator…** chooses a root and launches it.
- **File -> Bookmark Folder Navigator…** chooses a root, adds a folder chip to the current vault's bookmark bar, and launches it.
- The arrow beside the main bookmark button also exposes **Bookmark Folder Navigator…**.
- Click a folder chip to launch that root again. Right-click it to reveal the folder in the system file manager or remove the shortcut.

Folder shortcuts in StillPoint are scoped to the current vault. They store an absolute path, not a copy of the external files. A missing path remains visible with a warning so you can remove it deliberately.

From a source checkout, launch the companion directly with:

```bash
python -m sp.app.folder_navigator /path/to/folder
```

## The Folder Tree

- Select a file to open it in the reusable preview tab.
- Press `Enter` or double-click to keep it open as a pinned tab.
- Press `Shift+Enter` to open it and move focus into the editor.
- Use the context menu to create a new file, bookmark a target, filter from a folder, reveal a target, copy its path, open a terminal, or hand a file to its default system application.
- **View -> Show Hidden Files** controls dotfiles and other platform-hidden items.
- A folder filter temporarily makes a subtree the visible navigation and search root. Press `Escape` in the tree to clear the filter; without a filter, `Escape` collapses the tree.

When vi mode is enabled, use `h`, `j`, `k`, and `l` in the tree. In an editor, the first `Escape` leaves insert mode; from vi navigation mode, `Escape` returns focus to the tree and reselects the active file.

## Quick Open, Search, and the Folder Picker

- `Ctrl+P` (`Cmd+P` on macOS) opens **Quick Open**, a fuzzy filename and relative-path picker.
- `Ctrl+Alt+V` opens the hierarchical folder picker.
- `Ctrl+Shift+P` opens the Folder Navigator command bar.
- `Alt+G` is an alternate command-bar shortcut.
- The Search rail performs an on-demand search across filenames and safely readable text content. It supports case-sensitive, whole-word, regular-expression, and include-ignored options.

Quick Open maintains a filename-only SQLite catalog at `.sp_folder/catalog.sqlite3` under the selected root. It does not store file contents. Content search is performed only when requested and is not persisted as a content index. Hidden and Git-ignored paths are excluded by default from Quick Open and search, with controls to include them for the current operation.

## Editing and Tabs

Folder Navigator reuses StillPoint's Markdown editor for Markdown files and a syntax-highlighted source editor for other safely decoded text files. It can also preview common images and PDFs when the installed Qt build supports them. Unknown, binary, oversized, or unsafe-to-decode files open in a details view with system-open and reveal actions.

- Use the standard Save shortcut for the active file; **File -> Save All** writes all dirty tabs.
- Saving is explicit. Folder Navigator does not autosave external files.
- A dot on a tab marks unsaved work.
- If a file changes on disk while your buffer also has edits, Folder Navigator asks before overwriting or reloading it.
- Closing dirty tabs or the window offers Save, Discard, and Cancel choices.
- `Ctrl+Tab` and `Ctrl+Shift+Tab` cycle tabs using StillPoint's familiar switcher.
- In a Markdown editor, `t` in vi navigation mode or `Ctrl+Alt+T` opens the heading picker, fixed in the editor viewport.

Folder Navigator follows the global StillPoint vi-mode and vi-cursor preferences. The source editor supports navigation keys, selection, find, copy/cut/paste operations, opening a line, and `d` to delete a selection or the current line.

## Two Kinds of Bookmarks

The word “bookmark” appears in two related scopes:

- A **StillPoint folder shortcut** lives on the current vault's bookmark bar and launches an entire Folder Navigator root.
- A **Folder Navigator bookmark** lives inside one root and points to a file or subfolder there. File bookmarks open pinned tabs; folder bookmarks reveal the folder and can apply a subtree filter.

Keeping these scopes separate prevents an external file from being mistaken for a StillPoint page.

## Boundaries and Local State

Opening a folder does not make it a vault. Folder Navigator does not create StillPoint page folders, resolve vault page links, add files to the vault's search/RAG index, or start the embedded StillPoint API.

Window geometry, open pinned tabs, internal bookmarks, recent files, and related per-root state are stored in `~/.stillpoint_folder_navigator.json`. The filename catalog lives in the root's `.sp_folder` directory. Text edits and explicit new-file creation affect the selected folder; structural rename, move, and delete operations are intentionally not provided in the current version.
