# Page Search and Replace

Author: Codex  
Status: Draft  
Scope: In-editor search/replace for the Markdown editor and vi-mode shortcuts

## Goals
- Add an in-editor search/replace system that works with both standard and vi-mode workflows.
- Provide a `SearchEngine` abstraction with `find_next`, `replace_current`, and `replace_all`.
- Wire keyboard shortcuts for standard users (Ctrl/Cmd) and vi-mode users.
- Keep the UI minimal: inline find/replace bars, no modal dialogs.

## Out of Scope
- Cross-file or vault-wide search.
- Regex, case-sensitivity, or whole-word toggles (future enhancements).
- Rich history of searches or replacements.

## Functional Requirements
1) Search Engine
   - Implement `SearchEngine` (or equivalent class) with:
     - `find_next(query: str, backwards: bool = False, wrap: bool = True) -> bool`
       - Moves the cursor to the next match and selects it; returns True if found.
     - `replace_current(replacement: str) -> bool`
       - If the current selection matches the last search query, replace it; return True if replaced.
     - `replace_all(query: str, replacement: str) -> int`
       - Replace all occurrences in the document; return the count of replacements.
   - Track last query and last replacement text for quick repeat actions (n/N in vi-mode).
   - Operate on the current editor document without altering undo/redo semantics (reuse QTextDocument undo stack).

2) Standard UI/Shortcuts
   - Ctrl+F (Cmd+F on macOS): show a find bar above the editor.
     - Text input for query; Enter = find next; Shift+Enter = find previous; Escape hides the bar.
   - Ctrl+H (Cmd+H on macOS): show a replace bar (expands find bar with replacement input).
     - Enter in query = find next; Enter in replacement = replace current then find next.
     - Buttons: Find Next, Replace, Replace All, Close (minimal labels/icons OK).
   - When the bar is open, typing should stay in the query field unless the user focuses the editor.
   - Closing the bar keeps the last query/replacement cached for vi-mode repeat keys. 

3) Vi-mode Bindings (when vi-mode is enabled)
   - `/` : open find bar focused on query (forward search).
   - `?` : open find bar focused on query (backward search on first Enter).
   - `n` : find next using last query (direction of last search).
   - intentionally do NOT support 'N' because it conflicts with shift-n for selected text. (and i never use it anyway, sorry all :) ).
   - `*` : search forward for the word under cursor (whole word), select first result.
   - `#` : search backward for the word under cursor (whole word), select first result.
   - `:%s/old/new/g` : minimal substitution command
     - Scope: entire document.
     - No regex/flags beyond the trailing `g` (required).
     - Executes replace_all with `old` → `new`, reports count in a status/toast.

4) UX/Behavior
   - Selection of a match visibly highlighted using the editor’s existing selection formatting.
   - Searches wrap around the document by default.
   - If no match is found, show a non-intrusive status message/toast.
   - Replace All should be undoable in one step.
   - When the document changes, repeat-find (n/N) should still work with the last query against the updated text.

## Implementation Notes
- Place SearchEngine with clear editor/document access (likely within `markdown_editor.py` or a helper module).
- Keep the find/replace bar as a lightweight QWidget overlay/toolbar anchored above/below the editor.
- Respect existing vi-mode plumbing (reuse existing key handlers/event filters).
- Status messaging can use existing status bar/toast mechanisms already present in main window/editor.

## Acceptance Criteria
- Ctrl/Cmd+F opens find bar and finds next/prev with Enter/Shift+Enter.
- Ctrl/Cmd+H opens replace bar; Replace and Replace All work and are undoable.
- `/`, `?`, `n`, `N`, `*`, `#`, `:%s/old/new/g` function in vi-mode as specified.
- Selection moves to each match, wrapping when necessary; no crashes on empty or missing queries.
- Last query is remembered across bar close/reopen and used by vi-mode repeat keys.
