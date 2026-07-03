StillPoint Hotkeys (Desktop)
======================

Global / Navigation
-------------------
- `Ctrl+O`: Open vault in this window.
- `Ctrl+Shift+O`: Open vault in a new window.
- `Alt+Left` / `Alt+Right`: Back / forward in page history.
- `Alt+Up` / `Alt+Down`: Move to parent / first child page.
- `Alt+Home`: Go to vault root.
- `Ctrl+Shift+B`: Toggle left navigation panel.
- `Ctrl+Shift+N`: Toggle right panel.
- `Ctrl+R`: Reload current page from disk.
- `Ctrl+Shift+Space`: Cycle focus between tree, editor, right panel.
- `Alt+PgUp` / `Alt+PgDown`: Move selection in the tree (selection-only; does not auto-open page).
- `Enter` (tree focused): Open selected page and focus editor.
- `Ctrl+Enter` (tree focused): Open selected page and keep focus in tree.
- `\` (tree focused): Collapse the entire tree.

Editor Basics
-------------
- `Ctrl+S`: Save.
- `Ctrl++` / `Ctrl+-`: Zoom text in/out.
- `Ctrl+N`: New page (inline create).
- `Ctrl+D`: Insert date.
- `Ctrl+J`: Jump to page.
- `Ctrl+Alt+D`: Jump to journal date (calendar popup).
- `Ctrl+Alt+J`: Jump to bookmarked pages (bookmark-filtered picker).
- `Ctrl+L`: Insert link.
- `Ctrl+Shift+L`: Copy current page link.
- `Ctrl+Shift+B`: Toggle bookmarks bar.
- `Ctrl+Tab`: Recent pages popup (hold Ctrl, tap Tab to cycle; release to open).
- `Ctrl+Shift+Tab`: Headings popup for current page (hold Ctrl+Shift, tap Tab to cycle; release to jump and highlight).
- `Ctrl+Alt+T`: Open heading picker for the current page (non-vi equivalent of `t`).

Tasks / Vi Mode
---------------
- `F12`: Toggle task checkbox at cursor.
- `Ctrl+\` or `Ctrl+Backslash`: Focus Tasks search.
- Vi Mode: enable globally from **Preferences → Editing → Enable Vi Mode** (default: off).
  - Editors open in vi navigation mode with an `INS` badge in the status bar; the badge turns yellow only while insert mode is active.
  - Navigation keys: `h` `j` `k` `l`, `0` or `q` (line start), `;` or `$` (line end), `^` (first nonblank), `g`/`G` (file top/bottom), `w`/`b` (next/previous word), `f` (bookmark picker).
  - In the tree (when focused): `j`/`k` move selection down/up, `h`/`l` collapse/expand.
  - Selection helpers map to Shift+Arrow behavior: `Shift+N` selects down, `Shift+U` selects up, `Shift+;` selects to end-of-line.
  - Insert commands: `i` (before cursor), `a` (after cursor), `o`/`O` (new line below/above). `Esc` returns to navigation mode and clears insert highlighting.
  - Editing clipboard: `c` copies the current selection (or whole line) into the vi buffer, `x` cuts the selection/character into that buffer, `p` pastes from it. `d` deletes the current line, `r` replaces the character under the cursor once, `u` undoes, and `.` repeats the last edit.
  - `-` on an empty line (cursor at position 0) inserts a horizontal rule (`---`).
  - Standard `Ctrl+` shortcuts (links, jump, formatting, etc.) still work regardless of vi mode.

ToC / Headings
--------------
- Click headings in the floating ToC to smooth-scroll and briefly highlight.
- ToC hides when not needed (single heading or no scroll) and fades in on hover.

Right Panel
-----------
- Tabs: Tasks, Calendar, Attachments, Link Navigator, AI Chat (if enabled).
- Calendar keyboard flow: arrow keys move day by day, `Enter` opens the selected day and focuses the editor, and `Ctrl+Enter` opens the day while keeping focus in Calendar. In vi mode, `h/j/k/l` move by day or week, `Shift` with arrows or vi keys extends a multi-day selection, `t` jumps to today, `/` moves into headings/subpages, and `Esc` returns from those lists to the calendar. From the main editor, `Ctrl+Alt+D` opens a compact date-jump popup so you can pick a journal day without leaving the page.
- Map keyboard flow: `Ctrl+Enter` opens the selected node and focuses the editor, `Shift+Enter` opens it while keeping focus in the Map, `Alt+Enter` starts inline rename, `Ctrl+Space` toggles the selected node note popup, and `Space` folds/unfolds the selected node.
- Context menus and Vault menu let you open Tasks/Links/AI in separate windows.

Bookmarks
---------
- Add/remove via toolbar buttons; Ctrl+Shift+B toggles the bookmarks bar visibility.

Other Notes
-----------
- Open Vault dialog has an “Open in New Window” button.
- “Open File Location” and “View Vault on Disk” open system file managers.
