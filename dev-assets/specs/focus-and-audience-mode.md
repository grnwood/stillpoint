# StillPoint Editor Modes: Focus Mode and Audience Mode

## Summary

Add two single-window editor modes to StillPoint:

Both of these create a new modal popup overlay over the application that can only be closed by pressing the "X" in the upper right corner, or by repeating the hotkey to toggle it off.

In both modes editing via normal editing or vi editing continues to work as well as any navigation keys (arrows up/down or ctrl-shift-j/k in vi mode).

- **Focus Mode**: distraction-free writing for the author (you).
- **Audience Mode** (Screen-Share Mode): screen-share-friendly editing optimized for legibility while typing on a call.

Both modes operate in a second window (simliar to the page editor popup, which may be reused / adjusted if that makess sense).  Ohterwise create new files for each mode if needed (think code maintainability).  This window should feel like it overtakes the stillpoint application (on top of it) and is full screen of the monitor regardless of what the stillpoint main window geometry is.

Both Modes:
- Maintain live editing (this is not a slide or deck mode).
    - make sure normal editing or 'vi' mode editing continue to work.
    - they should have their own scope so they do not mess with anything in the underlying editor key bindings/behavior.
    - audience and focus mode dirty/clean buffer should work exacty like the popup page editor (sending signals on edits when focus obtained).

---

## Goals

### Focus Mode
- Remove visual distractions (panels, chrome, toolbars) while keeping editing fluid.
- Support optional writing aids (typewriter scrolling, paragraph focus).

### Audience Mode (Screen-Share Mode)
- Improve readability for viewers during screen sharing (font/spacing, cursor/focus aids).
- Simplify the UI so observers can easily follow along.
- Keep a minimal, intentional set of on-screen controls.

## Non-Goals (v1)

- Real-time multi-user collaboration.
- Automatic detection of screen sharing (manual toggle in v1).

---

## Terminology

- **Normal Mode**: default StillPoint editing experience.
- **Focus Mode**: minimal UI, author-centric writing.
- **Audience Mode**: minimal UI, viewer-centric legibility during screen sharing.

---

## User Stories

1. As a writer in focus mode, I want a modal popup that overtakes the entire screen, covering the main application so I can write without distraction.
2. As someone screen sharing in audience mode, I want my modal popup text to be easy to read and follow while I type.
3. As a user, I want fast keyboard toggles and a clear indicator of which mode I’m in.
4. As a user, I want to temporarily reveal minimal controls in the pop over layer.

---

## UX Requirements

### Shared Requirements (both modes)

- Fast toggle with no perceptible lag.
- Persistent but unobtrusive mode indicator on-screen.
- Overlay window closes on exit, leaving the main appliction visible and undisturbed (exception would be a reload of the page if it receives the signal to reload)
---

### Focus Mode UI

Keep:
- Show only Editor surface
- Page title (minimal header)
- Keyboard-driven editing

Optional writing aids (configurable):
- Mode indicator (`FOCUS`)
- Typewriter scrolling
- Paragraph focus (dim non-current paragraphs)
- Centered column with maximum width

---

### Audience Mode UI (Screen-Share Mode)

Show intentionally:
- Page title
- Mode indicator (`AUDIENCE`)
- Editor surface with readability enhancements
- Optional floating tool strip

Readability defaults:
- Increased font size (relative to Normal Mode)
- Increased line height
- Stronger visual hierarchy for headings
- Cursor spotlight or halo
- Highlight current paragraph
- Soft auto-scroll (scroll only when caret would leave viewport)

---

## Floating Tools (Audience Mode)

Optional minimal floating toolbar (auto-hide):

- Increase/decrease text size
- Jump to heading
- Toggle paragraph highlight
- Toggle soft auto-scroll

This toolbar must not steal focus while typing.

---

## Keyboard Shortcuts

Defaults (configurable):

- `Ctrl+Alt+F` → Toggle Focus Mode
- `Ctrl+Alt+A` → Toggle Audience Mode

Audience Mode helpers:
- `Ctrl+Alt+=` / `Ctrl+Alt+-` → Increase/decrease font size
- `Ctrl+Alt+H` → Toggle paragraph highlight
- `Ctrl+Alt+S` → Toggle soft auto-scroll

---

## Settings / Configuration

Settings location: `Editor > Modes`

### Focus Mode Settings

- `focus.hide_side_panels` (default: true)
- `focus.hide_toolbars` (default: true)
- `focus.center_column` (default: true)
- `focus.max_column_width_chars` (default: 80)
- `focus.typewriter_scrolling` (default: false)
- `focus.paragraph_focus` (default: false)

---

### Audience Mode Settings

- `audience.hide_side_panels` (default: true)
- `audience.hide_toolbars` (default: true)
- `audience.font_scale` (default: 1.15)
- `audience.line_height_scale` (default: 1.15)
- `audience.cursor_spotlight` (default: true)
- `audience.paragraph_highlight` (default: true)
- `audience.soft_autoscroll` (default: true)
- `audience.show_floating_tools` (default: true)

---

## Functional Requirements

### Mode Transitions

- main page editor and application is untouched
- new 'modes' popup as a new layer/window
---

### Editor Rendering Behavior

- Focus Mode:
  - Layout simplification and optional writing aids only.
- Audience Mode:
  - Readability and visual-following aids only.

No changes to underlying document content or format.

---

## UI Components

### Mode Indicator Overlay

- Small text label in a corner (bottom-right preferred).
- Values: `FOCUS`, `AUDIENCE`.
- Always visible while mode is active.
- click exits the mode.

---

### Floating Tools (Audience Mode)

- Semi-transparent, minimal, auto-hide when typing.
- Viewer-oriented tools only.
- No formatting or document-structure editing controls.

---

### Focus Mode

- Toggling Focus Mode shows overlay window which hides the underlying app.
- Editor remains fully editable with no lag.
- Optional writing aids function without disrupting caret or scroll behavior.

---

### Audience Mode

- Toggling Audience Mode applies readability settings.
- Mode indicator displays `AUDIENCE`.
- Cursor spotlight and paragraph highlight function correctly.
- Floating tools (if enabled) appear and auto-hide without stealing focus.

---

## Compatibility / Regression

- No changes to file format.
- No regression to Normal Mode editing.
- Printing and export behave as in Normal Mode.
- Works on Linux (primary) and Windows (secondary).

---

