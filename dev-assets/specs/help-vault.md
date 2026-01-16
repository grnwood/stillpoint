# StillPoint Help Vault

This document defines the content and tone of the built-in Help Vault. The Help Vault is a real StillPoint vault that ships with the app and opens in a separate window. It teaches StillPoint by example: every page is a normal Markdown note, cross-linked like a real knowledge base.

## Requirements
- Create an empty vault at `sp/help-vault`.
- Populate it with StillPoint-format Markdown pages that explain the app from a user perspective.
- Add a top-level **Help** menu with a **Documentation** action and `F1` shortcut.
- Launch the Help Vault in a new StillPoint window when **Documentation** is selected.
- Bundle the Help Vault in the PyInstaller build (`packaging/sp.spec`).

## Content Strategy
- Write for everyday users first, with a friendly, action-oriented tone.
- Keep pages short; link out to deeper topics.
- Use real StillPoint link styles and show off labeled links.
- Include quick steps and small examples rather than long prose.
- Keep wording consistent with StillPoint design principles: local-first, fast, explicit AI, no lock-in.

## Vault Structure
Use the following page set. Each page is a standalone Markdown file. The names below should be treated as note titles (and thus page names).

### Home
**Purpose:** Landing page for the help vault.
- What StillPoint is, in 2-3 sentences.
- A short “Start here” list linking to Getting Started, Editing, and Navigation.
- A quick summary of the design philosophy.

### Getting Started
**Purpose:** First-run walkthrough.
- Create/open a vault.
- Open a page.
- Save and navigate.
- Explain that StillPoint uses plain Markdown files and folders.

### Editing Basics
**Purpose:** Core editing behaviors.
- Plain Markdown editing with live formatting.
- Headings, bold/italic, lists, and checkboxes.
- Keyboard shortcuts (Ctrl+B/I/K/U/T, Ctrl+1..5, Ctrl+7, Ctrl+9).
- Inline images and attachments (drag/drop or paste).

### Links and Navigation
**Purpose:** How to move through a knowledge base.
- Page links and link labels.
- Backlinks and "where am I referenced?"
- Jump to page / quick navigation.
- Link activation and editing links in place.

### Search and Replace
**Purpose:** Find anything quickly.
- Find bar (forward/back, case sensitivity).
- Replace current and replace all.
- Search word under cursor.

### Tasks and Checkboxes
**Purpose:** Task workflow.
- Task syntax and toggling.
- How task views are updated.
- Working with task lists inside notes.

### Calendar and Journals
**Purpose:** Dated notes and daily workflows.
- Daily journal pages and templates.
- Calendar navigation to dated pages.
- How to customize templates.

### Tags and Filters
**Purpose:** Organize across pages.
- Tag style (e.g., `@tag`).
- Filtering by tag or path.
- Using tags to create topical views.

### AI Helpers
**Purpose:** Explain AI features without overpromising.
- AI actions are opt-in and explicit.
- How to send selections or whole pages to AI.
- Local vs. remote AI options.
- Privacy expectations.

### Attachments and Images
**Purpose:** Media workflows.
- Drag/drop attachments.
- Inline images in notes.
- Link behavior for local files.

### Keyboard and Focus
**Purpose:** Flow-focused features.
- Keyboard navigation basics.
- Read-only mode behavior.
- Vi navigation (if enabled).
- Focus/overlay behaviors (e.g., heading picker).

### Advanced Setup (Tech)
**Purpose:** Power-user and multi-device setups.
- Remote vaults: connect to a server-backed vault.
- Web client vaults: access your vault in a browser.
- Offline-first behavior and syncing expectations.
- Read-only vs. writable sessions.

### Troubleshooting
**Purpose:** Common issues and fixes.
- Vault not found or not writable.
- Remote auth problems.
- Broken links or missing attachments.
- Performance tips (large vaults).

## Design Philosophy (Short Form)
Include a page with a concise, skimmable list of principles. Avoid duplication. Use this exact tone and keep it tight:

- Your notes stay yours. Everything is plain Markdown.
- Works offline by default.
- Fast, focused, and distraction-free.
- AI is opt-in and visible.
- No lock-in, ever.
- Organize your way: links, tags, tasks, or all three.
- Built for long-term thinking.

## Cross-Linking Guidelines
- Link every page to at least two other pages.
- Use labeled links (e.g., `[Editing Basics|Learn editing shortcuts]`).
- Keep link labels clear and action-oriented.

## Tone and Formatting Rules
- Use short paragraphs and bullet lists.
- Avoid marketing fluff; be direct and helpful.
- Show examples in fenced code blocks where relevant.
- Keep headings consistent: H1 for the title, H2 for sections.

