# Folder Navigator implementation status

This records the remaining differences from the canonical `folder-navigator.md` specification. The companion runs with `python -m sp.app.folder_navigator [root]`, or through the StillPoint File menu and command bar. It does not start the StillPoint API. Structural file operations remain deferred to v2.

## Implemented

- Detached process launch, direct folder chooser, per-root geometry/pinned tabs/bookmarks, filesystem-backed lazy tree, hidden toggle, subtree filter, shared-model folder picker, preview/pinned tabs, dirty review, save conflict review and atomic replacement.
- Conservative UTF-8/UTF-16 text handling with newline and encoding preservation; binary/uncertain content falls back to details.
- Asynchronous bounded image decoding, PDF view where Qt PDF is installed, cancellable on-demand text search, in-memory asynchronous Quick Open catalog, and safe details fallback.
- Root boundary validation before internal file opening, no API/client calls, no folder-local metadata or content index.

## Remaining acceptance gaps

1. **Markdown reuse:** The existing `sp.app.ui.markdown_editor.MarkdownEditor` is deeply coupled to vault contexts and API calls (`set_context`, `_http_client` requests, page-name transformations, attachment handlers). Reusing it without an isolation refactor would violate the explicit no-vault/no-API requirement. The current Markdown tab uses a plain text editor with find/replace; it lacks the existing rich Markdown rendering, floating TOC, heading navigation, link/image handling, and complete vi editing behavior. This is the main architectural blocker to full MVP acceptance.
2. **Tree presentation:** Qt's filesystem model loads on demand, but inline loading/empty/permission/volume states, strict locale-aware case-insensitive folder-first ordering, and an explicit root row are not fully implemented. Outside-root directory links are not traversed; their inline appearance varies with Qt's platform model. Expansion restoration depends on Qt having loaded an index.
3. **Content handlers:** PDF loading currently runs on the UI thread; document search has input and previous/next controls but lacks password entry. Image zoom and fit exist, but large image scaling runs on the UI thread after bounded background decoding. A corrupt image receives a notice rather than the full details fallback.
4. **Search and Quick Open:** Search results are grouped with file headers in a list rather than a hierarchical results view. The filename catalog invalidates missing paths and receives loaded tree rows, but nested changes outside watched/expanded directories may appear only after reopening Quick Open. Quick Open batches `.gitignore` checks while cataloging, but content search invokes Git per path and needs batching for large repositories. Quick Open's modal UI does not yet show a stable live progress indicator.
5. **Navigation and state:** MRU switching changes tabs and shows status text, not the specified transient tab switcher. The editor `v` picker uses the shared model but appears as a centered dialog rather than a cursor-anchored popup. The vi setting is read from StillPoint's global configuration, but its plain editor has no full vi editing mode. The command bar is a simplified dedicated dialog, and command enablement is incomplete.
6. **Safety and accessibility:** Read-only detection uses OS access checks; directory/file permission states are not all represented inline. Larger editable files are guarded by size before reading but text and PDF reads still happen on the UI thread. Controls have partial accessible labels; keyboard access and platform behavior need a full audit. Atomic replace preserves permission bits, but not all other filesystem metadata.

These are implementation gaps, not changes to the product specification. The canonical spec remains the target for completion.
