# Process Quick Captures

Status: Implemented

## Summary

Quick Capture processing is launched from **Tools > Process Quick Captures…**.
It is not a Tasks-tab mode and does not use inbox tags, hidden identifiers, or
processed metadata. A capture remains pending for as long as its complete chunk
remains in a recognized Quick Captures section.

The command temporarily opens each source page in the main Markdown editor,
highlights the complete active capture, and presents a narrow modal immediately
outside the editor's right edge. If there is not enough screen space beside the
editor, the modal occupies the rightmost available screen position. Closing the
processor restores the page, cursor, and scroll position that were active before
processing began.

## Capture Boundaries

A capture is a timestamped Quick Capture entry beneath the configured
`## QuickCaptures` heading. Entries are separated and terminated by horizontal
rules. The timestamp header, indented body, attachments, surrounding formatting,
and trailing horizontal rule form one complete chunk.

Every structurally valid chunk is pending. No marker is added when it is created
or processed.

## Source Scope

The default **Active sources** scope includes:

- The saved custom Quick Capture page, when one is configured, including when
  Today is the currently selected capture target.
- Journal pages within seven days before or after the selected Calendar date.
- Today is used when no usable Calendar date is selected.

The configured target is processed first, followed by journal pages in path/date
order and captures from top to bottom within each page.

The modal also offers:

- **Configured page only**.
- **Calendar ±1 week**.
- **All capture pages**, discovered vault-wide from recognized headings.

For bounded scopes, the modal reports how many older captures exist outside the
current scope so they cannot be silently stranded.

## Processor UI

The modal shows the scope, current position (`N of M`), source colon path,
timestamp, a capture preview, and one terminal action: **M Move**.

There is no Keep or Delete outcome. Previous and Next only navigate; they do not
process a capture. A successful terminal action removes the source chunk and
advances to the next pending item. Undo reverses the most recent action when none
of its affected files or attachments have changed.

The complete active chunk is highlighted in the Markdown editor while keyboard
focus remains in the modal. Switching between source pages updates the editor
and highlight without adding those pages to navigation history.

## M Move

Pressing `M` or activating the Move button focuses the destination field inside
the processor; it does not open another dialog. The inline dropdown searches the
full page index as text is entered, independent of navigation or Tasks filters.
Suggestions never modify the typed value automatically; a result is moved only
when explicitly selected and confirmed with `Enter`.

Moving appends the complete capture chunk to an existing destination page,
including its timestamp and trailing horizontal rule. Relative attachments move
with the chunk, name collisions are resolved safely, and links are rewritten.
The source and destination are content-checked and indexed after the mutation.

## Keyboard Behavior

- `Up` / `Down`: previous or next capture.
- In vi mode, `j` / `k`: previous or next capture when focus is not editing the
  destination picker.
- `M`: focus the inline destination picker for the current capture.
- In vi mode, `Ctrl+Shift+J/K`: navigate inline destination suggestions.
- `Enter`: explicitly accept the highlighted destination suggestion.
- `Esc`: clear the destination text and suggestions, then return focus to Move.
- `Ctrl+Z`: undo the most recent processing action.

## Safety and Compatibility

- The current editor page is saved before processing begins.
- Mutations locate the capture using its source range and content hash, rejecting
  stale captures instead of editing the wrong text.
- Local and remote vaults use the same parsing and mutation implementation.
- Existing marker-bearing captures remain readable for backward compatibility,
  but new Quick Captures are not marked and the former Tasks Triage control is no
  longer exposed.
