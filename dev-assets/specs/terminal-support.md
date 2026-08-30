# Embedded Terminal Support

## Summary

StillPoint will provide an optional terminal tab in the existing right panel. The tab can be popped into a separate window without replacing its live session. The terminal starts in the current vault root, follows normal terminal behavior, and can run interactive console applications and full-screen TUIs such as shells, editors, and user-selected AI agent CLIs.

The terminal is lazy. StillPoint must not load Qt WebEngine, create a pseudo-terminal, start a shell, or request an MCP credential until the user first opens the terminal pane.

The terminal UI will use a locally bundled xterm.js frontend. Linux and macOS will use a POSIX pseudo-terminal backend. Windows will use a ConPTY backend. If the embedded terminal cannot start, the existing external-terminal action remains available as the compatibility fallback.

StillPoint does not launch Codex, Copilot, or another agent automatically. Users start whatever shell commands and tools they want.

## Goals

- Focus the right-panel Terminal tab with `Ctrl+Shift+Enter`.
- Start the shell with the current local vault as its working directory.
- Seed the vault-root `AGENTS.md` from `SP-vault-AGENTS.md` using the existing preference and seeding behavior.
- Support interactive, full-screen TUIs on Linux, macOS, and Windows.
- Give programs launched inside the embedded terminal authenticated access to StillPoint's MCP server.
- Detect and safely reconcile vault files changed by terminal processes.
- Load all terminal-specific runtime components only on first use.
- Preserve the existing **Open Vault in Terminal** action as a fallback.

## Non-goals

- Automatically starting or configuring a specific AI agent CLI.
- Bundling Codex, Copilot, Claude, or any other agent CLI.
- Replacing StillPoint's existing in-app AI chat and agent-tool loop.
- Implementing shell commands inside StillPoint itself.
- Persisting live shell processes across application restarts.
- Providing an arbitrary shell on a remote server.
- Building a terminal emulator directly in Qt widgets.
- Supporting multiple right-panel terminal tabs; multiple sessions share one Terminal tab.

## User Experience

### Opening and closing

- `Ctrl+Shift+Enter` expands and selects the right-panel Terminal tab. If it is already selected, the shortcut returns focus to the editor.
- A **View > Terminal** menu item provides the same action.
- **View > Open Terminal Window** and its matching command-palette entry move the complete terminal workspace into a separate window.
- **Go > Terminal** and **Go / Terminal** in the command palette always reveal and focus the active terminal; they do not toggle focus back to the editor.
- The Terminal tab follows the existing right-panel sizing, collapsing, minibar, selection, and context-menu semantics.
- Collapsing the right panel or selecting another tab does not stop the shell.
- **Open in New Window** moves and explicitly reveals the complete live terminal workspace—including its active-terminal selector, all session surfaces, and Ctrl+Tab switcher—inside a top-level window. Closing that window reattaches the same workspace to the right panel without restarting a shell.
- Closing the terminal session explicitly terminates the shell and its child process tree after confirmation when child processes are still active.
- Closing the StillPoint window terminates its embedded terminal session. Sessions are not restored after an application restart.

The shortcut must be handled at the main-window level. When the terminal has focus, the same shortcut returns focus to the editor. The key chord must not be forwarded into either surface.

### Lazy startup

Before the first selection, the tab may exist as a lightweight placeholder, but the following must not occur:

- importing or initializing Qt WebEngine;
- loading the xterm.js document;
- creating a PTY or ConPTY;
- starting a shell;
- creating an MCP session token.

On first open, the pane displays a short starting state while those components initialize. Once initialized, later hide/show operations are immediate.

### Pane controls

The terminal header provides:

- **Active terminal dropdown** — identifies the visible session, switches among open sessions, and offers **New Terminal** choices for the configured default or any detected installed shell;
- **Decrease/Increase Font Size** — compact `−` and `+` controls change xterm text size immediately and persist the selection without displaying the numeric point size;
- **Restart Session** — terminates the current session and starts a new shell at the vault root;
- **Open Externally** — invokes StillPoint's existing platform terminal launcher at the vault root;
- **Close Pane** — collapses the right panel, or closes and reattaches a popped-out terminal, without terminating the session.

If the shell exits, the pane retains its last output and displays its exit status with a **Start New Session** action.

There is no **Start Codex** or other agent-specific control.

Each open terminal keeps its own frontend, scrollback, PTY/ConPTY process, and MCP credential. Switching the active terminal changes the stacked terminal surface without stopping background sessions. The right-panel tab label and popped-out window title include the active terminal number and shell name.

When a shell exits normally, including through the `exit` command, its terminal is removed from the open-session list. If it was active, the next still-running terminal is selected; an exited background terminal does not steal focus. When the last terminal exits, the tab returns to one fresh lazy-start terminal placeholder.

The terminal surface handles `Ctrl+-` and `Ctrl+Shift++` while focused. `Ctrl+mouse-wheel` adjusts the same persisted font-size setting. The terminal header does not repeat the active shell command or expose a second shell selector; shell choice for a new session belongs in the active-terminal dropdown.

While xterm has focus, `Ctrl+Shift+T` creates and focuses a new terminal using the configured default shell. The chord is consumed by the frontend and is not forwarded to the shell process.

When the Terminal panel or its popped-out window has keyboard focus, `Ctrl+Tab` and `Ctrl+Shift+Tab` open an in-panel switcher and move its selection forward or backward through open terminal sessions. Each entry displays the terminal name, running state, and current foreground command line when the PTY backend can determine it, falling back to the configured shell command. The visible terminal changes only when Control is released. Outside the Terminal surface, those shortcuts retain StillPoint's recent-page picker behavior.

### Vault changes

A terminal session belongs to one StillPoint window and one vault.

- Switching or closing the vault terminates the existing terminal session before changing roots.
- Opening a different vault creates a fresh session only when the terminal is next shown.
- A hidden pane does not cause a shell to start for the new vault.
- The feature is enabled only when the active vault has a local filesystem workspace.
- A purely remote vault does not expose an embedded or local terminal action because there is no corresponding remote shell.
- A Homebase vault may use the terminal only against its local synchronized workspace and follows the normal Homebase conflict/sync rules.

## Terminal Architecture

```text
Right-panel Terminal tab (optionally reparented into QMainWindow)
        |
        v
QWebEngineView + bundled xterm.js
        |
        v
QWebChannel terminal bridge
        |
        v
TerminalSession interface
        |
        +-- PosixPtySession (Linux and macOS)
        |
        +-- ConPtySession (Windows)
        |
        v
user shell and child processes
```

### xterm.js frontend

- xterm.js and required addons are vendored into StillPoint's packaged assets. The terminal must not use a CDN or require network access.
- The frontend is loaded from a trusted local application resource.
- Navigation, downloads, popups, and access to arbitrary web content are disabled.
- The page receives terminal output, process state, title changes, and dimensions through a narrow QWebChannel bridge.
- The page sends user input, paste input, and resize events through that bridge.
- The fit addon calculates rows and columns when the right panel, application window, or pop-out window resizes.
- Clipboard operations use explicit StillPoint/Qt integration and preserve the platform's normal copy and paste shortcuts.
- Terminal scrollback has a bounded, configurable limit. The default is 10,000 lines.
- The frontend supports ANSI color, cursor addressing, alternate-screen buffers, Unicode, bracketed paste, and full-screen interactive applications.

The terminal page must not receive the MCP token through JavaScript. Credentials remain in the Python process and the child shell environment.

### Shared session interface

The platform backends implement a common interface with at least:

- `start(cwd, shell, environment, rows, columns)`;
- `write(data)`;
- `resize(rows, columns)`;
- `terminate()`;
- output/data notification;
- process-exited notification with exit code or termination reason.

PTY reads and process waits must not block the Qt UI thread. Output is buffered and delivered to the frontend in bounded batches to avoid one UI event per byte or an unbounded queue when a command produces heavy output.

Terminal input and output are bytes at the backend boundary and UTF-8 text at the xterm.js bridge boundary. Decoding must handle incomplete multibyte sequences across reads.

### Linux and macOS

Linux and macOS use a POSIX PTY with the shell as the session leader and controlling terminal. The implementation must:

- start the user's configured shell, falling back to the platform login shell;
- set the PTY window size before the child begins interactive work;
- propagate later row and column changes;
- provide a normal terminal environment including `TERM=xterm-256color` and UTF-8 locale behavior;
- signal the process group during shutdown so descendants do not remain orphaned;
- distinguish normal exit from forced termination.

The preferred default shell is the user's login shell. StillPoint must not assume Bash on macOS or Linux.

### Windows

Windows uses ConPTY, not redirected `QProcess` pipes. Redirected pipes are insufficient for full-screen TUIs and correct console behavior.

The implementation must:

- use a maintained ConPTY binding or a small bundled native helper behind the shared `TerminalSession` interface;
- propagate terminal dimensions to the pseudoconsole;
- support UTF-8 input/output and Windows console key behavior;
- terminate the pseudoconsole and descendant job/process tree when the session closes;
- prefer `pwsh.exe` when configured or available, otherwise use Windows PowerShell as the default;
- allow Command Prompt and WSL to be configured as alternative shell profiles.

WSL is an optional profile, not the default. When selected, StillPoint must translate the local vault path to the corresponding WSL path and report a clear error when the path cannot be mapped.

### Shell configuration

Preferences provide:

- default shell/profile, with an automatic platform default;
- optional shell executable and arguments;
- terminal font family, filtered to installed fixed-width system fonts;
- terminal font size;
- scrollback line limit;
- whether terminal startup should seed `AGENTS.md` using the existing setting.

Arguments are stored as a structured list, not interpolated into a shell command string. The vault working directory and environment values must be passed through process APIs without shell-string concatenation.

## Agent Workspace Seeding

Before starting either an embedded or external terminal, StillPoint calls the existing `_seed_agents_file_if_needed` behavior.

- Seeding remains controlled by **Add agent guidance and MCP client configs when opening a terminal**.
- StillPoint copies `SP-vault-AGENTS.md` only when the vault-root `AGENTS.md` does not exist.
- StillPoint copies `SP-vault-codex-config.toml` to `.codex/config.toml` only when that destination does not exist.
- StillPoint copies `SP-vault-copilot-mcp.json` to `.mcp.json` only when that destination does not exist.
- An existing `AGENTS.md` is never overwritten or merged automatically.
- Existing Codex and Copilot configuration files are never overwritten or merged automatically. Each seed is independent, so an existing `AGENTS.md` does not prevent a missing client configuration from being created.
- `AGENTS.md` remains excluded from page navigation, indexing, and Homebase page synchronization as it is today.
- No access token, port, user name, machine path, or other session secret is written to any seeded file. Client configurations contain only the `stillpoint-mcp` command and environment-variable references.

The template may document the names and intended use of StillPoint MCP tools, but authentication and endpoint discovery occur through the terminal environment.

## StillPoint MCP Access

### Dependency

Embedded-terminal MCP access depends on a StillPoint MCP server or bridge exposing the approved StillPoint-aware tools. The terminal feature must use that shared server implementation rather than duplicating the in-app agent tool implementations inside the terminal pane.

The MCP surface remains scoped to vault-aware operations. Arbitrary shell execution is not an MCP tool because the terminal already provides a shell.

The server advertises concise `initialize` instructions telling agents to prefer StillPoint tools over direct filesystem access for supported vault operations. The beginning of the instruction must be self-contained because clients may truncate server guidance.

The supported tool groups are:

- discovery and context: ranked `vault.search`, `vault.read`, `page.context`, `page.list_children`, `vault.backlinks`, and `vault.recent_changes`;
- safe content mutation: `page.patch`, legacy-compatible `vault.write`, and `vault.create_child`;
- structured tasks: `tasks.list`, `tasks.create`, `tasks.update`, and `tasks.complete`;
- journals: `journal.open`, with `daily.open` retained as a compatibility alias;
- structural changes: dry-run-capable `page.move`, including a page map and optional incoming-link rewrite.

Search results include canonical paths, colon links, useful snippets, rank-derived scores, headings where available, tags, revisions, and modification metadata. Page context includes page content and identity plus ancestors, direct children, backlinks, outgoing links, headings, tasks, tags, and attachments.

Patch, task, and move operations support dry-run previews. Page writes use optimistic `mtime_ns` checks, task mutations verify their expected source text and status, and moves may verify the vault tree version. A stale mutation fails visibly instead of silently overwriting current content.

The server also advertises MCP resources and resource templates for open tasks, recent changes, existing journals, page content, and structured page context. Reading a resource is side-effect-free; in particular, a journal resource never creates a missing journal page.

### Session authentication

The print-preview token flow is the model, but print-preview tokens must not be reused. Add a dedicated terminal/MCP session credential with these properties:

- minted only when an embedded terminal session starts;
- scoped to MCP access and the active vault identity;
- scoped to the current authenticated StillPoint user and window;
- read-only when the vault or user is read-only;
- revocable by StillPoint;
- revoked when the shell session ends, the vault changes, the user logs out, or the window closes;
- rejected for another vault even if its endpoint is reachable;
- bounded by a maximum lifetime as a defense against abandoned sessions.

The suggested maximum lifetime is 12 hours. A terminal session that outlives the token may request a replacement through a StillPoint-owned local bridge without exposing long-lived credentials. Token refresh must not require placing the user's password in the shell environment.

### Environment and discovery

The embedded shell receives session-specific environment variables such as:

```text
STILLPOINT_VAULT_ROOT=<absolute local vault path>
STILLPOINT_MCP_URL=<loopback MCP endpoint>
STILLPOINT_MCP_TOKEN=<session-scoped bearer token>
STILLPOINT_MCP_VAULT=<stable vault identifier>
```

Names are provisional until the MCP server contract is finalized, but the behavior is required.

- These variables are added only to the child environment and do not modify the user's global environment.
- Values are never printed automatically, logged, written into shell startup files, stored in command history, or placed in the vault.
- The MCP service binds to loopback unless an independently configured remote-server mode explicitly requires otherwise.
- Authentication is required even on loopback; the embedded terminal must not rely on the server's general localhost bypass.
- Server and application logs redact bearer tokens and credential-bearing URLs.

StillPoint seeds project configuration for Codex and GitHub Copilot CLI when agent workspace seeding is enabled. Codex uses `.codex/config.toml` with explicit `env_vars` forwarding. Copilot CLI uses `.mcp.json` with environment-variable substitution. Both configurations launch the generic `stillpoint-mcp` bridge on the embedded terminal's `PATH`; neither contains credential values. Other clients may configure the same bridge using their own MCP format. Direct Streamable HTTP access may also be used by clients that support it.

Documentation should include generic MCP connection details plus separate examples for supported clients, but terminal startup itself remains client-neutral.

## Filesystem Changes and Editor Safety

Programs in the terminal can edit vault files directly, bypassing StillPoint's page APIs. The feature must therefore integrate with the existing local-filesystem scan, reindex, and Homebase synchronization paths.

### Before terminal focus

When opening the pane or moving focus from the editor into the terminal:

- flush the current editor's pending autosave when it can be done safely;
- do not start a shell against a vault that is in a blocking merge/conflict state;
- do not silently discard an editor save error.

### External changes

- Terminal-created pages that follow StillPoint's folder/page conventions appear in navigation and search after the local change scan.
- Changed pages are reindexed and relevant task/link panels refresh.
- Terminal-originated changes should use a short targeted debounce rather than waiting for the full configurable background quiet period when the changed path is known.
- Bulk agent edits are still coalesced so the tree and indexes are not rebuilt for every individual write.
- `AGENTS.md` and `.stillpoint/` remain excluded from page indexing and page-change handling.

### Open-page conflicts

If the current page changes on disk:

- reload automatically only when the editor has no unsaved changes;
- when the editor also has unsaved changes, do not overwrite either version;
- offer **Compare**, **Reload from Disk**, and **Keep Editor Version** actions;
- preserve the disk version until the user decides;
- route Homebase conflicts through the existing Homebase conflict handling.

This behavior applies to all external filesystem edits, not only edits believed to originate from the embedded terminal.

## Security

- The terminal is an explicit local code-execution surface. It starts only after a user action.
- The shell runs with the same operating-system identity and permissions as StillPoint. The UI should state this in Preferences/help; it is not a sandbox.
- StillPoint does not inspect, approve, or restrict commands typed into the terminal.
- The working directory is resolved and validated before process creation.
- No terminal-provided title, hyperlink, escape sequence, or OSC command may cause arbitrary application actions without explicit validation.
- Opening file links or URLs from terminal output requires normal StillPoint URL/path validation and, where appropriate, user confirmation.
- Paste containing line breaks uses xterm.js bracketed-paste behavior. Optional paste confirmation for multiline commands may be added later but is not required for v1.
- The bundled web content uses a restrictive policy and no remote network resources.
- QWebChannel exposes only terminal input, sizing, clipboard, and lifecycle operations. It does not expose general Python or application object access.
- MCP authorization is enforced by the server for every operation; possession of a local vault path is not treated as authentication.
- A read-only StillPoint session never receives write-capable MCP credentials, although the OS may still permit direct filesystem writes. The UI must not describe the terminal itself as read-only or sandboxed.

## External Terminal Compatibility

The existing **Open Vault in Terminal** behavior remains supported:

- Linux tries the supported installed terminal applications.
- macOS opens Terminal.app at the vault directory.
- Windows opens a supported Windows terminal or shell at the vault directory.
- `AGENTS.md` seeding occurs before launch.

External launch is a fallback for missing WebEngine support, PTY/ConPTY initialization failure, accessibility issues, or user preference. StillPoint should show **Open Externally** directly in an embedded-terminal startup error.

Passing the embedded session's MCP token to an already-running external terminal application is not reliably portable and is not required for v1. External agents can use the documented persistent client configuration and authenticate through a separately supported MCP login/connection flow. StillPoint must not place a bearer token into a visible shell command merely to make external launch inherit it.

## Packaging

- Vendor pinned xterm.js and addon assets with license notices.
- Include the local terminal HTML, JavaScript, CSS, and icons in PyInstaller builds for Linux, macOS, and Windows.
- Include the selected Windows ConPTY dependency/helper and its license in Windows packages.
- Do not initialize Qt WebEngine during normal startup when the terminal is never opened.
- Follow StillPoint's existing WebEngine environment setup and failure handling.
- The packaged application must work without internet access.
- Development and packaged builds must resolve terminal assets through one shared resource helper rather than source-tree-relative assumptions.

## State and Settings

Persist:

- existing right-panel width, collapsed state, and tab semantics;
- configured shell/profile and structured arguments;
- scrollback limit;
- existing `AGENTS.md` seeding preference.

Do not persist:

- shell process identifiers;
- MCP session tokens;
- terminal environment snapshots;
- live terminal sessions;
- terminal output or command history in StillPoint configuration.

Application startup may restore the right-panel layout, but the terminal remains a **Start Terminal** placeholder until explicitly selected. This preserves the lazy-start guarantee.

## Accessibility and Interaction

- Keyboard focus moves predictably between editor and terminal.
- Terminal colors derive from the active StillPoint theme while preserving ANSI color distinctions and adequate contrast.
- Terminal font defaults to an installed monospace font and has configurable size.
- Standard terminal selection, copy, paste, scroll, and search behavior is supported.
- The terminal header and right-panel tab controls have accessible names and keyboard focus.
- Screen-reader limitations inherited from the chosen WebEngine/xterm.js combination must be documented and are a reason to retain external-terminal fallback.

## Error Handling

The pane presents actionable errors for:

- Qt WebEngine unavailable or failing to initialize;
- bundled terminal assets missing;
- configured shell missing or not executable;
- PTY/ConPTY creation failure;
- invalid or unavailable vault working directory;
- MCP server unavailable or token creation failure;
- shell process exiting unexpectedly.

An MCP failure does not prevent the shell from starting. The terminal displays that StillPoint MCP access is unavailable for that session, while ordinary shell and filesystem use continue to work.

## Delivery Plan

### Phase 1: shared pane and POSIX backend

- Right-panel tab, pop-out/reattach behavior, shortcut, and lazy lifecycle.
- Bundled xterm.js frontend and narrow QWebChannel bridge.
- POSIX PTY support for Linux and macOS.
- Vault-root startup and existing `AGENTS.md` seeding.
- Filesystem refresh and open-page conflict protection.
- External-terminal fallback.

### Phase 2: Windows backend

- ConPTY session implementation.
- PowerShell default and configurable Command Prompt/WSL profiles.
- Windows packaging, job-tree shutdown, Unicode, resizing, and TUI tests.

Phase 2 may be developed in parallel, but terminal support is not considered cross-platform complete until the Windows acceptance criteria pass.

### Phase 3: MCP connection

- Dedicated MCP session-token endpoint and authorization scope.
- Child environment injection and token revocation.
- Generic `stillpoint-mcp` bridge command.
- Client-neutral documentation and integration tests.

The pane and shell must remain useful when MCP is disabled or unavailable.

## Acceptance Criteria

### General

- No terminal process, WebEngine initialization, or MCP token is created during application startup when the terminal is not used.
- `Ctrl+Shift+Enter` selects and focuses the Terminal tab, and pressing it again returns focus to the editor.
- The first shell starts in the exact current vault root.
- Existing `AGENTS.md` files are never overwritten.
- Selecting another tab, collapsing the right panel, and pop-out/reattach preserve the active session.
- Restarting the session starts a clean shell at the vault root.
- Closing the window leaves no terminal child processes behind.
- Full-screen terminal applications render, resize, accept input, and exit correctly.
- A startup failure offers the external-terminal fallback.

### Platform coverage

- Linux: Bash or the user's configured login shell passes interactive and full-screen TUI tests.
- macOS: Zsh or the user's configured login shell passes the same tests.
- Windows: PowerShell through ConPTY passes the same tests.
- Each packaged build works offline with bundled assets.

### Vault integration

- A page created from the terminal appears in the tree and search index without restarting StillPoint.
- A terminal edit to an unmodified open page reloads safely.
- A terminal edit concurrent with unsaved editor changes produces a conflict choice and loses neither version.
- Terminal changes in a Homebase local workspace enter the established synchronization flow.

### MCP

- A process launched in the embedded terminal can discover the StillPoint MCP endpoint without a token file in the vault.
- An authorized MCP client can read and, where permitted, modify only the terminal session's vault.
- A read-only session receives a read-only MCP scope.
- Server initialization returns usage instructions and advertises tools plus resources.
- Search and page-context responses expose canonical identity, graph/tree context, and revision metadata.
- Dry-run and stale-write tests prove that page patches, task mutations, and page moves do not silently overwrite concurrent work.
- Dated journal reads can be non-creating, and MCP resource reads never create pages.
- A token fails after terminal shutdown, vault switch, logout, or window close.
- Tokens and credential-bearing URLs do not appear in application logs, terminal startup output, configuration files, or `AGENTS.md`.

## Test Strategy

- Unit tests for session lifecycle, shell/profile selection, environment construction, path validation, and credential redaction.
- Unit tests for token scope, expiration, refresh, and revocation.
- Backend tests for PTY/ConPTY input, output, resize, exit, Unicode, and process-tree termination.
- UI tests for tab selection, focus return, lazy construction, pop-out/reattach, and error fallback.
- Integration tests using a deterministic terminal fixture that exercises ANSI colors, cursor movement, alternate screen, resize, bracketed paste, and high-volume output.
- Filesystem integration tests for page create/update/delete, debounce/coalescing, reindexing, and editor conflicts.
- Packaged smoke tests on Linux, macOS, and Windows rather than relying only on source-tree runs.

## Decisions Captured

- Use xterm.js rather than implementing terminal emulation in Qt widgets.
- Support Linux, macOS, and Windows with native PTY/ConPTY backends.
- Keep startup lazy and terminal sessions ephemeral across app restarts.
- Start at the vault root and seed `AGENTS.md` without overwriting user content.
- Provide MCP access with a dedicated, session-scoped credential rather than a print-preview token.
- Keep terminal startup client-neutral; users choose and start their own tools.
- Retain external-terminal launch as the compatibility and accessibility fallback.
