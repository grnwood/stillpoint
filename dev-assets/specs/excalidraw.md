# StillPoint Excalidraw Integration Spec

## Decisions Locked

- Excalidraw drawings are normal page attachments, like `.puml`, `.mmd`, and `.mermaid`.
- First implementation is local-vault only.
- Creating a drawing does not automatically insert markdown into the current page.
- Drawings open in a separate native PySide6 window.
- The drawing UI is Excalidraw running in `QWebEngineView`, served by StillPoint's local FastAPI app.
- The durable source of truth is the `.excalidraw` JSON file.
- Markdown preview uses a PNG sidecar for v1. SVG is preferred long term, but Qt inline SVG has known Linux risk in StillPoint.
- If `QWebEngineView` is unavailable or unstable, provide an external browser fallback.

## Goal


The feature should feel like the existing PlantUML and Mermaid attachment editors:

1. User opens a page.
2. User opens the Attachments panel.
3. User chooses `Add new Excalidraw...`.
4. StillPoint creates a `.excalidraw` file beside the current page file.
5. StillPoint opens a separate native drawing window.
6. The window loads Excalidraw from the local StillPoint FastAPI server.
7. Excalidraw autosaves JSON back to the `.excalidraw` attachment.
8. Excalidraw exports a PNG preview sidecar for markdown/image rendering.

## Existing Architecture To Match

Use StillPoint's current layout and integration points:

```text
sp/app/ui/attachments_panel.py
  Existing attachment UI and creation/opening flow.
  Add New Excalidraw action and .excalidraw double-click routing here.

sp/app/ui/main_window.py
  Existing editor-window launch coordination.
  Mirror _open_plantuml_editor and _open_mermaid_editor for Excalidraw.

sp/app/ui/excalidraw_window.py
  New native non-modal QMainWindow for Excalidraw.

sp/server/api.py
  Existing FastAPI app.
  Add local-only Excalidraw app/static/load/save/preview endpoints here.

web-client/excalidraw/ or sp/excalidraw_web/
  New Vite React Excalidraw app source.
  Built assets must be served by sp/server/api.py and packaged with StillPoint.
```

Do not introduce generic folders like `backend/`, `desktop/`, or `web/` unless the repo is intentionally reorganized first.

## User Experience

### Create From Attachments Panel

In `AttachmentsPanel._on_attachments_context_menu`, add:

```text
Add new Excalidraw...
```

Behavior:

1. Prompt for a file name, defaulting to `drawing`.
2. Ensure suffix `.excalidraw`.
3. Create the file in the current page folder.
4. Seed it with a valid empty Excalidraw scene.
5. Refresh the attachments list.
6. Emit a new signal to open the Excalidraw editor.
7. Do not modify the markdown page content.

Example local page folder:

```text
Vault/
  Notes/
    Design/
      Design.md
      drawing.excalidraw
      drawing.excalidraw.png
```

### Open Existing Drawing

Double-clicking a `.excalidraw` attachment opens the native Excalidraw window.

Markdown attachment links should also open the drawing window:

```markdown
[Open drawing](./drawing.excalidraw)
```

For v1, no special `drawing:abc123` ID syntax is required.

### Markdown Preview

If the user wants inline preview, they can reference the PNG sidecar:

```markdown
![Drawing](./drawing.excalidraw.png)
```

StillPoint should not auto-insert this during drawing creation.

Future enhancement: StillPoint may detect image references to `.excalidraw` and substitute the PNG sidecar automatically, but that is out of v1 scope.

## File Format

The `.excalidraw` file stores the full Excalidraw scene JSON.

Use a StillPoint wrapper that preserves Excalidraw data clearly:

```json
{
  "type": "excalidraw",
  "version": 2,
  "source": "stillpoint",
  "elements": [],
  "appState": {},
  "files": {}
}
```

Rules:

- Preserve all Excalidraw element data.
- Preserve `files` for embedded images.
- Do not flatten the drawing into only PNG/SVG.
- Keep text elements, arrows, groups, and custom element data inspectable for future AI features.
- Do not store absolute local paths inside the scene.

## Sidecar Preview

For v1, the preview sidecar is:

```text
<drawing-name>.excalidraw.png
```

Example:

```text
drawing.excalidraw
drawing.excalidraw.png
```

PNG is the primary preview because StillPoint currently disables inline SVG by default on Linux due to Qt SVG crash risk.

SVG remains a desired future option:

```text
drawing.excalidraw.svg
```

The implementation may also save SVG if it is cheap, but acceptance for v1 should require PNG only.

## Runtime Architecture

```text
PySide6 MainWindow
  └── AttachmentsPanel
        ├── creates/opens .excalidraw attachment
        └── emits excalidrawEditorRequested

PySide6 ExcalidrawWindow
  └── QWebEngineView
        └── loads http://127.0.0.1:<port>/excalidraw/edit?path=<vault-relative-path>
              └── FastAPI serves built Excalidraw app
                    └── React app calls local StillPoint API
                          ├── load .excalidraw JSON
                          ├── save .excalidraw JSON
                          └── save .excalidraw.png preview
```

Use the existing embedded local FastAPI server already started by `sp/app/main.py`.

## Routes

Add these to `sp/server/api.py` or a small module imported by it.

All v1 routes are local-only. They must require the same local UI authorization behavior as other write-capable local UI requests.

### Serve App

```http
GET /excalidraw/edit
```

Query:

```text
path=/Notes/Design/drawing.excalidraw
```

Returns the Excalidraw React app `index.html`.

The app reads `path` from `window.location.search`.

### Static Assets

```http
GET /static/excalidraw/*
```

Serves Vite-built JS/CSS/assets.

### Load Drawing

```http
GET /api/excalidraw
```

Query:

```text
path=/Notes/Design/drawing.excalidraw
```

Response:

```json
{
  "path": "/Notes/Design/drawing.excalidraw",
  "title": "drawing.excalidraw",
  "scene": {
    "type": "excalidraw",
    "version": 2,
    "source": "stillpoint",
    "elements": [],
    "appState": {},
    "files": {}
  },
  "updated_at": "2026-07-03T12:00:00Z"
}
```

Behavior:

- Resolve `path` as a vault-relative path.
- Reject paths outside the active vault.
- Require suffix `.excalidraw`.
- If the file is missing, return 404.
- If the file is empty or invalid JSON, return a useful error instead of silently overwriting.

### Save Drawing

```http
PUT /api/excalidraw
```

Request:

```json
{
  "path": "/Notes/Design/drawing.excalidraw",
  "scene": {
    "type": "excalidraw",
    "version": 2,
    "source": "stillpoint",
    "elements": [],
    "appState": {},
    "files": {}
  }
}
```

Behavior:

- Validate payload is JSON.
- Require suffix `.excalidraw`.
- Resolve only inside the active vault.
- Enforce a reasonable max JSON size.
- Persist formatted JSON with UTF-8 encoding.
- Update attachment metadata if StillPoint tracks the file.
- Return `{ "ok": true, "path": "...", "updated_at": "..." }`.

### Save PNG Preview

```http
PUT /api/excalidraw/preview
```

Request:

```json
{
  "path": "/Notes/Design/drawing.excalidraw",
  "png_base64": "..."
}
```

Behavior:

- Decode PNG.
- Persist to `/Notes/Design/drawing.excalidraw.png`.
- Reject non-PNG data.
- Enforce a reasonable max preview size.
- Update attachment metadata for the PNG sidecar if needed.
- Return `{ "ok": true, "preview_path": "/Notes/Design/drawing.excalidraw.png" }`.

Future optional route:

```http
PUT /api/excalidraw/preview-svg
```

Store `/Notes/Design/drawing.excalidraw.svg` after SVG sanitization. This is not required for v1 acceptance.

## PySide6 Window

Create:

```text
sp/app/ui/excalidraw_window.py
```

Class:

```python
class ExcalidrawWindow(QMainWindow):
    def __init__(
        self,
        file_path: str,
        *,
        base_url: str,
        local_auth_token: str | None = None,
        parent=None,
    ) -> None:
        ...
```

Behavior:

- Use StillPoint app icon.
- Use `QWebEngineView` if available.
- Load `f"{base_url}/excalidraw/edit?path={quote(vault_relative_path)}"`.
- Keep the window non-modal.
- Keep a strong reference in `MainWindow`, matching `_plantuml_windows` and `_mermaid_windows`.
- Title: `Excalidraw - <filename>`.
- Remember geometry with existing config helpers if convenient; otherwise defer.

### WebEngine Loading

Follow the cautious Mermaid pattern:

- Import Qt WebEngine lazily.
- Configure Linux WebEngine environment before importing.
- Do not crash the app if `QWebEngineView` cannot import.
- If WebEngine is unavailable, show a prompt offering to open the same URL in the external browser.

External browser fallback:

```python
QDesktopServices.openUrl(QUrl(url))
```

If auth headers are needed, prefer embedding a short-lived local UI token in the query string for local-only routes rather than requiring custom browser headers.

## Attachments Panel Changes

In `sp/app/ui/attachments_panel.py`:

- Add signal:

```python
excalidrawEditorRequested = Signal(object)
```

- Add context action:

```text
Add new Excalidraw...
```

- Add `.excalidraw` handling in `_open_attachment`.
- Add local-only `_create_new_excalidraw`.
- Remote behavior is explicitly deferred for v1. If in remote mode, disable the action or show a clear local-only message.

Initial scene template:

```json
{
  "type": "excalidraw",
  "version": 2,
  "source": "stillpoint",
  "elements": [],
  "appState": {},
  "files": {}
}
```

## Main Window Changes

In `sp/app/ui/main_window.py`:

- Connect `attachments_panel.excalidrawEditorRequested` to `_open_excalidraw_editor`.
- Implement `_open_excalidraw_editor(file_path)`.
- Add `.excalidraw` handling in `_open_local_attachment_link`.
- Remote handling is deferred for v1.

The launch pattern should mirror `_open_plantuml_editor` and `_open_mermaid_editor`.

## React App

Create the Excalidraw app with Vite + React.

Candidate source location:

```text
web-client/excalidraw/
  package.json
  vite.config.js
  index.html
  src/
    App.jsx
    api.js
```

Candidate built asset location:

```text
sp/server/static/excalidraw/
  index.html
  assets/
```

The exact asset path can change if packaging prefers `importlib.resources`, but the build output must be packageable with StillPoint.

Responsibilities:

- Read `path` from URL query string.
- Fetch drawing JSON from `GET /api/excalidraw?path=...`.
- Render Excalidraw with `initialData`.
- Debounce autosave to 1-2 seconds.
- Save only after meaningful scene changes.
- Save `elements`, `appState`, and `files`.
- Generate PNG preview after successful saves.
- Surface save status in the native/web UI: saving, saved, failed.

Autosave pseudo-flow:

```text
onChange(elements, appState, files)
  mark dirty
  debounce 1500ms
  PUT /api/excalidraw { path, scene }
  if save ok:
    export PNG
    PUT /api/excalidraw/preview { path, png_base64 }
```

Use relative API URLs so the app works when served from StillPoint:

```text
/api/excalidraw
/api/excalidraw/preview
```

## Auth And Local-Only Scope

V1 is local-only, but still must not expose arbitrary file writes.

Requirements:

- Bind desktop FastAPI server to localhost as StillPoint already does by default.
- Require local UI authorization for write routes.
- Validate every path as vault-relative.
- Reject path traversal.
- Reject non-`.excalidraw` load/save paths.
- Reject preview writes that do not correspond to a `.excalidraw` source path.
- Enforce max JSON and PNG sizes.

Remote vault support is deferred. The v1 UI should either hide Excalidraw creation/opening in remote mode or show a clear message:

```text
Excalidraw editing is local-vault only for now.
```

## AI-Ready Metadata

Do not implement AI actions in v1, but preserve data so future AI features can inspect drawings.

Future AI should be able to read:

- text elements
- arrows
- groups
- element positions and rough layout
- custom metadata

Future shape-to-note metadata can use Excalidraw `customData`:

```json
{
  "id": "element123",
  "type": "rectangle",
  "customData": {
    "stillpointLinks": [":Projects:Launch:Risk Register"]
  }
}
```

## Out Of Scope For V1

- Remote vault editing.
- Global drawing IDs.
- Automatic markdown insertion.
- Automatic `.excalidraw` markdown embed rendering.
- Shape-level note linking.
- Collaborative Excalidraw rooms.
- Native Qt drawing implementation.
- SQLite/Postgres drawing records.
- Required SVG preview rendering.

## Acceptance Criteria

The first implementation is complete when:

1. Attachments panel offers `Add new Excalidraw...` for local vault pages.
2. Creating a drawing creates a valid `.excalidraw` file beside the current page file.
3. Creating a drawing refreshes the attachment list and opens the drawing window.
4. Double-clicking an existing `.excalidraw` attachment opens the drawing window.
5. Markdown links to `.excalidraw` open the drawing window.
6. The native window uses `QWebEngineView` when available.
7. If `QWebEngineView` is unavailable, the user can open the drawing in an external browser.
8. The WebEngine/browser page loads the local Excalidraw React app from StillPoint's FastAPI server.
9. Existing `.excalidraw` JSON loads into Excalidraw.
10. Changes autosave back to the same `.excalidraw` attachment.
11. Reloading the drawing window preserves the drawing.
12. Successful saves generate or update `<name>.excalidraw.png`.
13. PNG sidecar previews can be referenced as normal markdown images.
14. All file reads/writes are constrained to the active local vault.
15. Remote vault mode does not pretend to support editing; it gives a clear local-only path.
16. Focused tests cover path validation, file creation, load/save API behavior, and attachment launch routing.

## Recommended First Commit Scope

Build the smallest stable vertical slice:

1. Add `.excalidraw` creation/open routing in `AttachmentsPanel`.
2. Add `_open_excalidraw_editor` in `MainWindow`.
3. Add `ExcalidrawWindow` with lazy WebEngine import and external browser fallback.
4. Add FastAPI routes for serving the built app, loading JSON, saving JSON, and saving PNG preview.
5. Add a minimal Vite React Excalidraw app with load and debounced autosave.
6. Add tests for local attachment creation/opening and API path safety.

After that is stable, add polish:

- window geometry persistence
- save status details
- better empty/error scenes
- optional SVG sidecar
- markdown-side preview helpers
