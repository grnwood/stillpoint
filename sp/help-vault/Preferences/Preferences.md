# Preferences

Preferences let you shape StillPoint to your workflow: what features are on, how the app looks, how capture works, and how AI/diagram tools behave.

## Open Preferences
- Keyboard: `Ctrl+.`
- Menu: `Edit -> Preferences`

## Global vs Vault Preferences
- Global preferences apply across all vaults.
- Vault preferences override selected settings for one specific vault, including accent color, feature/AI toggles, and read-only access.
- Open vault preferences from `Vault -> Vault Preferences`.

## General
### Markdown Editor
- **Enable TOC Widget**: Shows a floating heading navigator while editing.

### Code Highlighting
- **Pygments style**: Chooses syntax-highlighting style for code blocks.

### Tray
- **Enable system tray icon**: Keeps quick access in your OS tray.
- **Minimize to tray on close**: Closing the window hides it to tray instead of fully exiting.

### Features
- **Enable Tasks**: Shows task parsing and task views in the app.
- **Enable Calendar**: Shows calendar/journal navigation panels and related actions.
- **Enable Link Navigator**: Enables the page-link graph/navigation tools.
- **Enable Page Tags**: Enables tag parsing and the tags panel/workflows.
- **Force read-only mode for this vault**: Opens the current vault without taking a write lock.

Note: Feature toggles may require reopening the vault or restarting the app.

### Capture
- **Home Quick Capture Vault**: Where tray-based quick captures are written.
- **Default Capture Page**: Choose between `Today Journal Page` and `Custom Page`.
- **Custom Page**: Enter a page name or a path-like name.
- **Quick Capture heading**: Sets the shared heading used for captures (`QuickCaptures` by default). Repeated captures reuse this section.
- **Quick Capture Hotkey (in-app)**: Keyboard shortcut for quick capture inside StillPoint.

## Appearance
### Fonts
- **Application font**: Sets the default UI font used across the app.
- **Application font size** (`0` uses system default): Controls overall UI text size.
- **Default Markdown font**: Sets the default editor font for markdown pages.
- **Default Markdown font size**: Controls markdown editor text size.
- **AI chat font**: Sets the font used in AI chat panels.
- **Use Minimal Font Scan**: Faster startup on some systems (restart required).

### Theme
- **Theme**: Choose Default or a custom JSON theme.
- **Refresh Themes**: Reload the theme list.
- **Open Theme Folder**: Opens `~/.stillpoint/themes`.

## Modes
### Vi Mode
- **Enable Vi Mode**: Turns on Vim-style navigation/editing behavior in the editor.
- **Use Vi Mode Block Cursor**: Shows a block cursor style while Vi mode is active.

### Focus Mode
- **Centered column**: Keeps writing centered for a calmer reading/editing layout.
- **Max column width (chars)**: Limits line width so lines stay readable.
- **Font size**: Sets the base font size used in Focus mode.
- **Font scale**: Multiplies font size for quick overall sizing.
- **Enable typewriter scrolling**: Keeps the active line near a consistent vertical position.
- **Highlight current paragraph**: Visually emphasizes the paragraph you are editing.

### Audience Mode
- **Centered column**: Centers content for presentation-friendly framing.
- **Max column width (chars)**: Limits line length for easier on-screen reading.
- **Font size**: Sets the base text size for Audience mode.
- **Font scale**: Scales text larger or smaller without changing the base value.
- **Line height scale**: Adds or reduces spacing between lines for readability.
- **Show cursor spotlight**: Highlights cursor location so viewers can follow along.
- **Highlight current paragraph**: Emphasizes the active paragraph while presenting.
- **Enable soft auto-scroll**: Smoothly scrolls as you move through content.
- **Show floating tool strip**: Shows quick controls while in Audience mode.

### Main Editor
- **Enable main editor soft auto-scroll**: Enables smooth incremental scrolling in normal editing mode.
- **Soft auto-scroll lines to scroll**: Sets how many lines each soft-scroll move advances.

## Tasks
- **Non-actionable task tags**: Space-separated tags such as `@wait @wt @someday`.
- **Show Start Date in Tasks**: Adds each task's start date column/label in task views.
- **Show Page in Tasks**: Shows the source page for each task in task views.

## AI Chats and Agents
- **Enable AI Chats**: Turns on AI chat features across the app.
- **Manage Servers**: Add/edit AI server profiles.
- **Default Server**: Chooses which configured AI server chats use by default.
- **Default Model**: Chooses which model is preselected for chats.
- **Refresh Models**: Reloads the available model list from the selected server.
- **Enable AI Agents in chat**: Allows tool-using agent behavior in chat.
- **Add AGENTS.md to vault workspace when opening a terminal**: Seeds vault guidance for coding agents and terminal workflows.
- **Local filesystem quiet time (s)**: Controls how long StillPoint waits after local file changes before refreshing the UI.
- **Agent Tools table**: Edit tool examples and tool settings used by agents.

## PlantUML
- **Enable PlantUML rendering**: Turns PlantUML code blocks into rendered diagrams.
- **PlantUML JAR path**: Points StillPoint to your `plantuml.jar` file.
- **Java path (optional)**: Lets you pick a specific Java executable.
- **Render debounce (ms)**: Wait time before rerendering after edits.
- **Editor font**: Font used in the PlantUML editor window.
- **Editor font size**: Text size used in the PlantUML editor window.
- **Test PlantUML Setup**: Runs a quick check to verify your PlantUML setup works.

## Mermaid
- **Enable Mermaid rendering**: Turns Mermaid code blocks into rendered diagrams.
- **Built-in renderer**: Mermaid preview uses the app's bundled web renderer.
- **Editor font**: Font used in the Mermaid editor window.
- **Editor font size**: Text size used in the Mermaid editor window.

## Templates and Vault/Link Options
You may also see options for:
- **Default template for new page**: Chooses which template is used when creating new pages.
- **Default template for new journal entry**: Chooses which template is used for journal day pages.
- **Rebuild vault index**: Recreates the vault index used by search/navigation helpers.
- **Rewrite backlinks on page move**: Updates links in other pages when you rename or move a page.
- **Prefer shorter links on link generation**: Uses shorter link text when possible.

These control default content and how links are maintained during page moves/renames.

## Vault Preferences (Per-Vault Overrides)
Vault Preferences combine direct per-vault settings with three-state overrides.

Three-state overrides use:
- **Checked**: Force enabled for this vault.
- **Unchecked**: Force disabled for this vault.
- **Dash/partial**: Use global default.

Available vault-specific settings:
- **Vault Accent**: Sets an accent color for the current vault, or uses the theme default.
- **Force read-only mode for this vault**: Opens the current vault without taking a write lock.

Available three-state overrides:
- **Tasks**: Overrides whether task features are enabled in this vault.
- **Calendar**: Overrides whether calendar features are enabled in this vault.
- **Link Navigator**: Overrides whether link graph/navigation tools are enabled in this vault.
- **Page Tags**: Overrides whether tag features are enabled in this vault.
- **AI Chats**: Overrides whether AI chat is enabled in this vault.

Use **Use Global Defaults** to reset the accent color and three-state overrides back to global behavior.
