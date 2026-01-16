# StillPoint

StillPoint is a local-first, Zim-style note system with a PySide6 desktop app and an embedded FastAPI backend. It is built around a folder-per-page vault structure, fast navigation, and Markdown-first editing.

## Highlights

- Local-first vaults on disk (folders + Markdown files).
- Fast tree navigation, history popup, and heading switcher.
- Markdown editor with formatting shortcuts, task parsing, and inline images.
- Print to browser with clean HTML output, print CSS, and image support.
- Journaling workflows with date navigation and templates.
- Optional vi-mode navigation/editing.
- Built-in help vault and keyboard shortcuts guide.
- AI chat panel, one-shot prompts, and AI actions when configured.
- Focus/Audience modes for distraction-free writing and reading.
- Link graph / navigator for contextual browsing and filtered views.
- PlantUML diagramming with AI-assisted generation and templates.

## License

StillPoint is licensed under the Apache License, Version 2.0.
See the [LICENSE](LICENSE) file for details.

## Getting Started

1. Create / activate a virtual environment (optional):
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   ```
2. Install dependencies:
   ```bash
   pip install -r sp/requirements.txt
   ```
3. Run the desktop app:
   ```bash
   python -m sp.app.main
   ```

The embedded FastAPI server boots automatically and listens on `127.0.0.1:${ZIMX_PORT:-8765}`.

## Vault Structure

A vault is a normal folder on disk. Each page is a folder containing a same-named Markdown file:

```
MyVault/
  Projects/
    Project Phoenix/
      Project Phoenix.md
      attachments/
        diagram.png
```

- Page files use `.md` (legacy `.txt` still works).
- Attachments live in `attachments/` alongside the page file.
- Journal pages are stored under `Journal/YYYY/MM/DD/DD.md`.

## Desktop App Overview

The app lives in `sp/app` and is centered around `sp/app/ui/main_window.py` and the custom editor in `sp/app/ui/markdown_editor.py`.

Key UI features:

- Vault picker, New Vault flow, and multi-window support.
- Left tree navigator with inline rename, create, and delete.
- Drag and drop to reorder pages within the same folder.
- Right-click "Move To..." to relocate pages or folders to different parents.
- History popup (Ctrl+Tab) and heading switcher (Ctrl+Shift+Tab).
- Task panel with tag filtering and search.
- Calendar panel and "Today" journal actions.
- Attachments, link navigator, and AI panels (optional).
- Focus/Audience modes for distraction-free reading.

## Keyboard Shortcuts

The built-in help vault includes a full shortcuts guide:

- Help menu: **Help → Keyboard Shortcuts**
- File in repo: `sp/help-vault/Keyboard Shortcuts/Keyboard Shortcuts.md`

The help vault is copied to `~/.stillpoint/help-vault` on first open. To refresh it from the repo, delete or rename that folder and reopen Help → Documentation.

## FastAPI Backend

The API lives in `sp/server/api.py` and is embedded in the desktop app. It handles vault file access, tree listing, tasks, search, and journal utilities. Requests expect vault-relative paths starting with `/` and are validated to stay within the selected vault root.

## Print to Browser

StillPoint renders pages (or a merged subtree) to HTML and opens the system browser for print/PDF. This avoids Qt print fidelity issues and makes it easy to produce clean, paginated PDFs.

Benefits:

- Browser-based rendering with print CSS for consistent output.
- Images render inline via the embedded server.
- Optional subtree merge: page + descendants are combined into a single, well-ordered document.
- Clean layout that prints as separate pages for a tidy bundled PDF.

Local overrides:

- If `print.html` or `print.css` exist under `~/.stillpoint/templates` or `<vault>/.stillpoint/templates`, StillPoint uses those instead of the defaults.

## Templates

Template files live in `sp/templates` and user templates are stored under `~/.stillpoint/templates`. Templates currently use `.txt` names (by design).

## Tests

Tests live in `tests/`:

```bash
pytest tests
```

## Packaging (PyInstaller)

Build scripts and spec live under `packaging/`.

```bash
pyinstaller -y packaging/sp.spec
```

Artifacts land in `dist/StillPoint/`.

## Install into OS
If you want to install fully into the OS there are some helper scripts in packaging/

### Windows
Open powershell

```bash
> .\venv\Scripts\Activate.ps1
> pyinstaller.exe -y .\packaging\sp.spec
> cd .\packaging\win32\
> .\install.ps1
```

Zimx should be installed in menus, etc.

### Linux
```bash
~/code/stillpoint$ cd packaging/linux-desktop/
~/code/stillpoint/packaging/linux-desktop$ sudo ./install-app.sh 
📦 Installing StillPoint...
➡️  Creating install dir: /opt/stillpoint
➡️  Copying files...
➡️  Creating symlink: /usr/local/bin/stillpoint
➡️  Installing icon to /usr/share/icons/stillpoint.png
➡️  Creating desktop entry at /usr/share/applications/stillpoint.desktop

🎉 StillPoint installed successfully!
You can now launch it from: Menu → Accessories → StillPoint
Or run from terminal: stillpoint
```
## Repo Layout

- `sp/app/` - Desktop app (PySide6)
- `sp/server/` - Embedded FastAPI backend
- `sp/help-vault/` - Bundled help vault content
- `sp/templates/` - Default templates
- `tests/` - pytest suite
- `packaging/` - PyInstaller spec and assets

## Notes

StillPoint stores settings per-vault in `.stillpoint/settings.db` (SQLite). Vault contents always live where the user chooses and remain plain files on disk.
