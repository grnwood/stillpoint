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

Editor Basics
-------------
- `Ctrl+S`: Save.
- `Ctrl++` / `Ctrl+-`: Zoom text in/out.
- `Ctrl+N`: New page (inline create).
- `Ctrl+D`: Insert date.
- `Ctrl+J`: Jump to page.
- `Ctrl+L`: Insert link.
- `Ctrl+Shift+L`: Copy current page link.
- `Ctrl+Shift+B`: Toggle bookmarks bar.
- `Ctrl+Tab`: Recent pages popup (hold Ctrl, tap Tab to cycle; release to open).
- `Ctrl+Shift+Tab`: Headings popup for current page (hold Ctrl+Shift, tap Tab to cycle; release to jump and highlight).

Tasks / Vi Mode
---------------
- `F12`: Toggle task checkbox at cursor.
- `Ctrl+\` or `Ctrl+Backslash`: Focus Tasks search.
- Vi Mode: enable globally from **Preferences → Editing → Enable Vi Mode** (default: off).
  - Editors open in vi navigation mode with an `INS` badge in the status bar; the badge turns yellow only while insert mode is active.
  - Navigation keys: `h` `j` `k` `l`, `0` or `q` (line start), `;` or `$` (line end), `^` (first nonblank), `g`/`G` (file top/bottom), `w`/`b` (next/previous word).
  - Selection helpers map to Shift+Arrow behavior: `Shift+N` selects down, `Shift+U` selects up, `Shift+;` selects to end-of-line.
  - Insert commands: `i` (before cursor), `a` (after cursor), `o`/`O` (new line below/above). `Esc` returns to navigation mode and clears insert highlighting.
  - Editing clipboard: `c` copies the current selection (or whole line) into the vi buffer, `x` cuts the selection/character into that buffer, `p` pastes from it. `d` deletes the current line, `r` replaces the character under the cursor once, `u` undoes, and `.` repeats the last edit.
  - Standard `Ctrl+` shortcuts (links, jump, formatting, etc.) still work regardless of vi mode.

ToC / Headings
--------------
- Click headings in the floating ToC to smooth-scroll and briefly highlight.
- ToC hides when not needed (single heading or no scroll) and fades in on hover.

Right Panel
-----------
- Tabs: Tasks, Calendar, Attachments, Link Navigator, AI Chat (if enabled).
- Context menus and Vault menu let you open Tasks/Links/AI in separate windows.

Bookmarks
---------
- Add/remove via toolbar buttons; Ctrl+Shift+B toggles the bookmarks bar visibility.

Other Notes
-----------
- Open Vault dialog has an “Open in New Window” button.
- “Open File Location” and “View Vault on Disk” open system file managers.
