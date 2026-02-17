# Modes

Modes help you use the same page in different ways: writing, presenting, or keyboard-driven editing.

## Mode Types
- **Normal editing**: Your regular app window with panels and tools.
- **Focus Mode**: Cleaner writing view with fewer distractions.
- **Audience Mode**: Bigger, presentation-friendly reading view.
- **Vi Mode**: Vim-style editor behavior inside the markdown editor.

## How To Open Modes
- Menu: `View -> View... -> Focus Mode` or `Audience Mode`
- Keyboard Focus Mode: `Ctrl+Alt+F`
- Keyboard Audience Mode: `Ctrl+Alt+A`
- You can also open Focus/Audience from the mode buttons in the status bar.

Note: `Ctrl+Shift+F` is **Search Across Vault**, not Focus Mode.

## Focus Mode
Focus Mode is best when you want to write without visual noise.
- Opens the current page in a dedicated overlay window.
- Keeps your cursor position and returns you to the same place when you close it.
- Uses your Focus Mode settings from Preferences.

Useful Focus settings in `Preferences -> Modes`:
- Centered column
- Max column width (characters)
- Font size
- Font scale
- Typewriter scrolling
- Current paragraph highlight

## Audience Mode
Audience Mode is tuned for reading or presenting content to others.
- Opens the current page in a clean, larger-format overlay window.
- Keeps your place and returns you to normal editing when closed.
- Uses your Audience Mode settings from Preferences.

Useful Audience settings in `Preferences -> Modes`:
- Centered column
- Max column width (characters)
- Font size
- Font scale
- Line height scale
- Cursor spotlight
- Paragraph highlight
- Soft auto-scroll
- Floating tool strip

**NOTE:**  Both Focus and Audience mode have a 'Full screen' toggle button so they can be resized and/or dragged to a second monitor.

## Vi Mode
Vi Mode changes editing behavior in the main markdown editor for keyboard-first workflows.
- Enable it in `Preferences -> Modes -> Enable Vi Mode`.
- Optional: `Use Vi Mode Block Cursor`.
- When active, StillPoint shows an `INS` status badge in the status bar for insert mode.

## Main Editor Soft Auto-Scroll
Separate from Focus/Audience overlays, you can tune soft auto-scroll for normal editing:
- Enable main editor soft auto-scroll
- Set how many lines to scroll per step

These are in `Preferences -> Modes`.

## Tips For New Users
- Start with Focus Mode first; it is the easiest mode to feel immediately.
- Use Audience Mode when sharing notes on screen.
- Try Vi Mode only if you already like Vim-style editing.
- If a mode looks too tight or too wide, adjust max column width and font scale in Preferences.
