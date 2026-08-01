# StillPoint Operations AI Model and AI Page Rename

## Status

Implementation-ready specification.

## Summary

StillPoint currently uses the same configured default AI model for interactive AI work and bounded application-managed AI operations. Add a second model preference so inexpensive models can be used for those defined operations without changing the model used for chats, agents, inline AI, or other open-ended user-directed generation.

This release must also add an explicit **Rename Auto (AI)** command that generates a page title from the page body and then renames the page through the existing rename flow.

## Goals

1. Add a separately persisted default model for StillPoint-managed AI operations.
2. Use that model for all four operation workflows in scope for this release:
   - the existing **Generate Chat Summary** title operation;
   - the existing calendar AI insight summary;
   - the existing task AI insight summary;
   - the new **Rename Auto (AI)** page operation.
3. Keep manual and AI page rename clearly distinct in the File menu.
4. Make automatic page rename safe, asynchronous, bounded in input size, and predictable on malformed model output or operational failure.
5. Load the auto-rename system prompt from a bundled text file and cache it after the first read.

## Definitions and scope

A **StillPoint operation** is a bounded AI request whose input, system prompt, and output purpose are defined by the application, rather than an open-ended conversation or user-authored content-generation request. The initial consumers are chat-summary titles, calendar insight summaries, task insight summaries, and page auto-rename titles.

The following remain on their current server/model-selection paths and are not migrated to the operations model in this change:

- normal AI chats and agents;
- inline AI and one-shot prompts;
- chat condensation that produces content for the user;
- Mermaid, PlantUML, and Excalidraw generation.

Future internal operations should use the same operations-model resolver instead of reading `default_ai_model` directly.

## 1. AI preference for StillPoint operations

### Preferences UI

In **Preferences > AI Chats and Agents**, retain the existing default server control and expose two model controls under a heading such as **Default Server and Models**:

- **Default model for chats and agents** — the existing `default_ai_model` preference, relabeled for clarity.
- **Default model for StillPoint operations** — the new preference.

Both model controls use the models available for the selected default server. Changing the server or refreshing its models must repopulate both controls.

Place a compact question-mark help control beside each model label. Use a small auto-raised `QToolButton` (or an equivalent theme-compatible control) displaying `?`; it must not change either selection. Hovering the control must show a tooltip bubble, and the control must be keyboard-focusable with an accessible name so the same explanation is available without a mouse.

Use the following tooltip content, with line wrapping as needed:

- **Chats and agents help:** `Used as the default for AI chats and agents, inline and one-shot AI, chat condensation, and diagram generation. Individual features may allow a different model selection.`
- **StillPoint operations help:** `Used for Generate Chat Summary titles, calendar AI insights, task AI insights, and Rename Auto (AI). If unavailable, StillPoint falls back to the chats-and-agents model and then the server's available defaults.`

Set descriptive accessible names such as `About the chats and agents model` and `About the StillPoint operations model`. For each control, define the explanatory text once and assign it as both the tooltip and accessible description so the hover and assistive-technology copy cannot drift apart.

When the saved operations model is available for the selected server, select it. Otherwise, select the resolved fallback described below. Saving Preferences persists both model values. Canceling Preferences must not persist either value.

Do not add a separate operations server setting in this release. StillPoint operations use the configured default AI server.

### Configuration

Add configuration helpers alongside the existing default AI server/model helpers in `sp/app/config.py`:

- `load_default_ai_operations_model() -> Optional[str]`
- `save_default_ai_operations_model(model: Optional[str]) -> None`

Persist the value in global config under:

```text
default_ai_operations_model
```

No eager config migration is required. An absent key is valid and must use the runtime fallback. This preserves existing installations without rewriting their config merely because they upgraded.

### Server and model resolution

Use one shared resolution path for StillPoint operations. Avoid duplicating subtly different fallback logic in the chat panel and main window.

Resolve the server as follows:

1. the configured `default_ai_server`, when it still identifies a configured server;
2. otherwise, the first configured server;
3. if no server exists, do not start a worker and show a concise user-visible error.

For the resolved server, choose the first non-empty model in this order:

1. `default_ai_operations_model`, if it is present in that server's available/cached model list;
2. `default_ai_model`, if it is present in that list;
3. the server's `default_model`, if present;
4. the first available model.

If no cached model list is available, treat a non-empty server `default_model` as the available fallback and allow a non-empty configured operations model to be used. This retains support for OpenAI-compatible servers that do not expose model discovery.

If no model can be resolved, do not send a request and show a concise user-visible error.

### Existing chat-summary operation

Change the existing **Generate Chat Summary** title request in `AIChatPanel` to use the shared StillPoint-operations server/model resolver. Do not change its prompt construction, output normalization, worker concurrency rules, or rename behavior as part of this work.

The interactive chat's currently selected model and its stored `last_model` must not be changed when a summary title uses the operations model.

### Existing calendar and task insight operations

Change the AI summary generation paths in `CalendarPanel` and `TaskPanel` to use the same shared StillPoint-operations server/model resolver.

Only model/server resolution changes for these features. Preserve their existing:

- prompt files and prompt construction;
- input collection and existing input-size limits;
- streaming behavior and progress UI;
- cancellation and concurrent-worker behavior;
- rendering, persistence, and failure handling.

The operations-model choice applies when generating a new calendar or task insight. Previously saved insight text is unaffected, and switching the preference must not automatically regenerate an insight.

## 2. File menu commands

Change the existing File menu action:

```text
Rename
```

to:

```text
Rename (Manual)
```

Keep its existing `F2` shortcut. Replace the File-menu/F2 inline editor with the standard name-entry dialog, prefilled with the current leaf name. Resolve the open page directly from `current_path`; do not require the page to be present or visible in the file-navigation model. When no page is open, a valid non-root tree selection may be used as a fallback. The tree context-menu **Rename** command uses the same dialog with its explicit tree path.

Both manual and AI rename must keep a conventional page-title heading synchronized using this conservative rule:

- inspect only the first nonblank content line;
- update it only when it is an ATX H1 (`# `, optionally preceded by up to three spaces) whose title text exactly and case-sensitively matches the actual old page leaf/filename without its suffix;
- replace the heading text with the actual destination leaf name, including any case, spacing, underscore, or other conversion already present in that destination (for example, `New Page` to `new_page`);
- do not independently slugify or otherwise transform heading text;
- leave H2–H6 headings, formatted headings, extended headings, case-insensitive matches, and later headings unchanged;
- preserve leading blank lines, indentation, spacing, UTF-8 BOM, line-ending style, final-newline state, and all other page content.

This synchronization belongs in the shared backend rename/move path so all page-leaf renames behave consistently. A move that retains the same leaf name does not change the heading.

After either manual or AI rename, use the returned `page_map` to queue the existing background on-disk backlink rewriter, just as page moves do. Updating only indexed link rows is insufficient because a later reindex would restore stale targets from Markdown. Save any dirty active editor before starting the rename. When the background job reports that it changed the clean, currently open page, reload that page without adding a history entry so its editor buffer cannot later overwrite the corrected links. Preserve custom link labels while updating link targets. The existing preference must be labeled **Rewrite backlinks on page move or rename** and remains enabled by default.

Immediately after it, add:

```text
Rename Auto (AI)
```

The AI action has no keyboard shortcut in this release. The navigation-tree context menu is outside the scope of this label change and keeps its existing manual **Rename** action.

The AI action uses the same target resolution as the manual File-menu rename action: prefer the open page's `current_path`, including a Journal page hidden from file navigation, and only then fall back to a valid non-root tree selection. It must not operate on the filter banner, an invalid selection, or the vault root. If no page can be resolved, show a status-bar message and do nothing.

## Journal visibility command

Add a checkable **Vault > Toggle Journal** action. It must appear as **Vault / Toggle Journal** in the command palette and call the same journal-visibility state setter as the file-navigation Journal button. Triggering either control updates the persisted preference, rebuilds the navigator as it does today, and synchronizes the checked state of both controls without recursively firing either action.

The action should remain visible for discoverability. On invocation, reject the operation with a clear status-bar message when:

- AI chats are disabled;
- the vault is read-only or the current user lacks write permission;
- another page auto-rename request is already running;
- there is no usable AI server or operations model.

## 3. Automatic page-rename workflow

### Source content

Resolve the selected page's backing file using the existing folder-to-page-file conversion. Read it through the existing `/api/file/read` path so local and remote vaults behave consistently.

If the selected page is also the open page and has unsaved edits, save those edits before reading. If the save fails or the page remains dirty, abort; never generate a title from stale content and never rename after a failed save.

Before constructing the model input, omit the first nonblank ATX H1 when it conventionally represents the current page leaf. For this input-only comparison, match case-insensitively, treat underscores and spaces as equivalent, and collapse whitespace. Do not modify the stored page during this step. Retain custom or nonmatching headings. This prevents the current title from anchoring the model to the name it is supposed to improve.

Strip leading and trailing whitespace from the resulting body. If the result is empty, do not call AI and show:

```text
Cannot auto-rename an empty page.
```

### Bounded input

Do not send an arbitrarily large page to a utility model.

- Use the entire trimmed body when it is at most 12,000 Unicode characters and at most 200 lines.
- Otherwise, use the leading content ending at whichever limit is reached first: 12,000 Unicode characters or 200 lines.
- Do not split a Unicode code point. Prefer ending at the last complete line within the character limit when doing so leaves non-empty content; otherwise use the exact character limit.
- Tell the model in the user message when the supplied page body was truncated.

The request messages must contain:

1. one system message containing the loaded auto-rename prompt;
2. one user message containing the bounded page body and whether it was truncated.

Do not include unrelated vault pages, chat history, attachments, credentials, absolute filesystem paths, or server configuration in the prompt.

### Asynchronous request and UI state

Use the existing non-streaming AI worker/request infrastructure. The request must not block the Qt UI thread.

While a page auto-rename is running:

- retain the original source path as the operation target;
- prevent a second page auto-rename from starting;
- show a status such as `Generating page title...`;
- do not rename a different page merely because selection or navigation changes while the request is in flight.

Before applying the result, confirm that the original source path still exists. If it was moved, renamed, or deleted while AI was running, discard the result and report that the page changed.

### Model output contract

Create `sp/app/auto-rename-prompt.txt`. The prompt must instruct the model to:

- infer a concise, specific title from the page body;
- prefer 3–7 words;
- return only the title;
- return no quotes, Markdown, explanation, path, or filename extension;
- avoid generic titles such as `Notes`, `Untitled`, or `Summary` when a more specific title is possible.

The application, not only the prompt, must normalize and validate output:

1. remove reasoning/`<think>...</think>` blocks using an existing shared helper where practical;
2. normalize CR/LF and use only the first non-empty output line;
3. remove surrounding whitespace, quotes, apostrophes, and backticks;
4. collapse internal whitespace;
5. remove a trailing `.md` or legacy page suffix, case-insensitively;
6. remove trailing sentence punctuation;
7. cap the result at 72 Unicode characters without leaving trailing whitespace or punctuation;
8. reject `.` and `..`, control characters, and filesystem-unsafe characters (`/`, `\\`, `:`, `*`, `?`, `"`, `<`, `>`, `|`);
9. reject a result that becomes empty after normalization.

Do not silently substitute the old page name when output is invalid. Leave the page unchanged and show a concise failure message.

### Applying the rename

Construct the destination under the source page's existing parent. Use the existing `/api/file/rename` endpoint and the same post-success reconciliation used by manual rename (`page_map`, link-path registration, pending selection, tree refresh, and reloading the open page when applicable).

Before sending the rename request:

- if the normalized title equals the current page name, treat the operation as a successful no-op and report that the page already has the suggested name;
- do not overwrite or merge an existing destination;
- do not invent numeric suffixes to avoid a collision.

On success, show:

```text
Renamed page to '<title>'.
```

On collision, network failure, server error, invalid output, or any other failure, preserve the original page and use the existing API-error/status presentation patterns. Never fall back to manual rename automatically.

If the window closes while the request is running, request cancellation and ignore late worker signals. Clean up the worker reference on success, failure, cancellation, and window destruction.

## 4. Prompt loading and packaging

Add a small module-level loader for `auto-rename-prompt.txt`, following the existing cached one-shot prompt pattern:

- read UTF-8 text from the bundled `sp/app` location on first use;
- strip it and cache the non-empty result in memory;
- do not read the file again during that process lifetime;
- include a safe built-in fallback prompt so a missing or unreadable file produces a controlled result rather than a crash.

There is no vault-specific override for this prompt in this release.

Add the prompt file to both desktop PyInstaller data manifests:

- `packaging/sp.spec`
- `packaging/sp-macos.spec`

The server-only and quick-capture packages do not need this UI prompt.

## 5. Expected implementation touchpoints

The implementation will likely touch at least:

- `sp/app/config.py` — operations-model persistence;
- `sp/app/ui/preferences_dialog.py` — second model selector and save/load behavior;
- `sp/app/ui/ai_chat_panel.py` — shared operations resolver and chat-summary migration;
- `sp/app/ui/calendar_panel.py` — calendar insight migration to the shared resolver;
- `sp/app/ui/task_panel.py` — task insight migration to the shared resolver;
- `sp/app/ui/main_window.py` — menu labels, page request lifecycle, content bounds, normalization, and rename reconciliation;
- `sp/app/auto-rename-prompt.txt` — system prompt;
- `packaging/sp.spec` and `packaging/sp-macos.spec` — bundled data;
- focused tests under `tests/`.

This list is guidance, not a requirement to force all logic into these files. Prefer small reusable helpers over copying server/model resolution or rename reconciliation.

## 6. Tests

Add or update automated tests covering at least the following.

### Configuration and Preferences

- loading a missing operations-model key returns `None`;
- saving and loading `default_ai_operations_model` round-trips;
- both model selectors repopulate when the default server changes or models are refreshed;
- a saved valid operations model is restored;
- a missing/stale operations model displays the documented fallback;
- each model label has a keyboard-focusable question-mark help control with the documented tooltip and accessible name;
- accepting saves both values and canceling does not save changes.

### Operations model resolution

- operations model wins when available;
- general default model is the compatibility fallback;
- server default and then first model are used in the documented order;
- missing configured server falls back to the first configured server;
- missing server or model returns a controlled error;
- **Generate Chat Summary** uses the operations model without changing the chat session's model;
- calendar insight generation uses the operations model while retaining its existing prompt, streaming, and persistence behavior;
- task insight generation uses the operations model while retaining its existing prompt, streaming, and persistence behavior;
- changing the operations-model preference does not regenerate previously saved insights.

### Page body bounding and output normalization

- short pages are sent in full;
- pages beyond either bound are truncated correctly and marked as truncated;
- multibyte Unicode content is not corrupted;
- empty/whitespace-only pages do not start a worker;
- quotes, Markdown fences, suffixes, extra lines, repeated whitespace, reasoning blocks, and trailing punctuation are normalized;
- unsafe, reserved, or empty titles are rejected;
- titles longer than 72 characters are capped safely.

### Rename workflow

- File menu labels and the retained `F2` manual shortcut are correct;
- AI-disabled, read-only, invalid selection, vault-root, and concurrent-run cases do not start a request;
- dirty current-page content is saved and then read before the request;
- failed save aborts the request;
- the selected source path is retained if navigation changes during generation;
- a source changed during generation causes the result to be discarded;
- same-name output is a no-op;
- collisions do not overwrite or auto-suffix;
- success calls the existing rename API and applies returned path maps;
- an exact leading H1 is updated to the actual destination leaf while near-matches and all other content remain byte-for-byte unchanged;
- manual and AI rename queue an on-disk backlink rewrite from the returned `page_map` when the preference is enabled;
- backlink rewriting updates stored targets, preserves custom labels, and reports touched paths so a clean open editor can be refreshed;
- dirty active-editor content is saved before rename so it cannot overwrite a rewritten backlink later;
- AI and rename failures leave the page unchanged and clean up worker state;
- late signals after window close do not apply a rename.

## 7. Acceptance criteria

The feature is complete when all of the following are true:

1. Preferences presents distinct chat/agent and StillPoint-operations model selections for the default server, persists them independently, and provides accessible question-mark tooltips explaining what each selection drives.
2. Existing users with no operations-model key continue to work through the documented fallback chain.
3. **Generate Chat Summary**, calendar AI insights, and task AI insights use the operations model without otherwise changing their current behavior.
4. Previously saved calendar and task insights are not regenerated merely because the operations model changes.
5. The File menu contains **Rename (Manual)** followed immediately by **Rename Auto (AI)**; `F2` still invokes manual rename through a name-entry dialog rather than an inline tree editor.
6. Manual and AI rename operate on the open page even when it is a Journal descendant hidden from file navigation; AI uses bounded content, calls the operations model asynchronously, validates the returned title, and applies the rename through the existing rename API.
7. A leading H1 that exactly matches the old leaf name is updated to the actual destination leaf name; custom or nonmatching headings remain unchanged.
8. With backlink rewriting enabled, manual and AI rename update Markdown link targets using the returned `page_map`, preserve custom link labels, and refresh a clean open page if its file was changed by the background job.
9. Empty pages, stale targets, invalid model output, name collisions, read-only state, missing AI configuration, cancellation, and request failures never rename or overwrite a page.
10. `auto-rename-prompt.txt` is loaded once with a safe fallback and is present in Windows/Linux and macOS desktop builds.
11. **Vault > Toggle Journal**, the command-palette entry, and the file-navigation button share one persisted state and remain visually synchronized.
12. Focused tests for the behaviors above pass, along with the existing chat-summary, calendar-insight, task-insight, preferences, navigation, and rename-related test suites.

## Non-goals

- automatic/background renaming without an explicit command;
- bulk page renaming;
- folder-only naming unrelated to a page body;
- a separately selectable operations server;
- user editing or vault overrides for the auto-rename prompt;
- changing the model selection behavior of interactive or open-ended content-generating AI features not explicitly listed in scope;
- retrying with a different model or silently falling back to manual rename after a failed AI request.
- undo/unrename history for structural page operations.
