## Overview

Vi mode is now a user-level preference (Preferences → Editing → Enable Vi Mode). The toggle is stored in `~/.stillpoint_config.json`, defaults to **off**, and applies to every editor instance (main window plus pop-outs). All runtime toggles (`Alt+Space`, etc.) have been removed.

## When vi mode is disabled

- The status bar shows only the dirty badge; the `INS` label is hidden.
- All keys behave like a standard QTextEdit. Existing shortcuts such as recent-pages (`Ctrl+Tab`), heading navigator (`Ctrl+Shift+Tab`), `Ctrl+J`/`Ctrl+L`, task helpers, etc., continue unchanged.

## When vi mode is enabled

- Editors load directly into **navigation mode**. A status-bar `INS` badge appears with a clear background until insert mode is entered.
- The block cursor overlay (when enabled via the separate preference) activates only after the widget paints once to avoid startup crashes on Windows.
- `Esc` always returns to navigation mode and clears insert highlighting. All `Ctrl+` and `Alt+` shortcuts remain active while vi mode handles bare keys only.

### Navigation keys

- `h` `j` `k` `l`: left/down/up/right.
- `0` or `q`: jump to start of line.
- `;` or `$`: jump to end of line (Shift+`;` selects to end).
- `^`: first nonblank character.
- `g` / `G`: start/end of file.
- `w` / `b`: next / previous word start.
- `Shift+N`: extend selection down (same as Shift+Down).
- `Shift+U`: extend selection up (same as Shift+Up).
- `Shift+;`: extend selection to line end (Shift+End).

### Insert commands

- `i`: insert before cursor.
- `a`: insert after cursor.
- `o` / `O`: open a new line below / above and enter insert mode.
- Insert mode highlights the `INS` badge; pressing `Esc` restores navigation mode without toggling focus.

### Clipboard / edit commands

- `c`: copy the current selection (or the whole line when nothing is selected) into the vi clipboard without modifying text.
- `x`: cut the current selection or character into the vi clipboard (removing it from the buffer).
- `p`: paste the vi clipboard at the caret location (repeatable via `.`).
- `d`: delete the current line.
- `r`: replace the character under the cursor with the next typed character (single-use insert).
- `u`: undo last change.
- `.`: repeat the most recent vi edit (cut, paste, delete, replace, etc.).

### Additional guarantees

- Tab/backtab behavior is unchanged; multi-line indents continue to work.
- Recent-pages (`Ctrl+Tab`) and heading navigator (`Ctrl+Shift+Tab`) dialogues still suspend typing but do not disturb vi state once dismissed.
- Vi activation is deferred until after the first paint so startup with vi mode enabled no longer hangs or crashes on Windows.
