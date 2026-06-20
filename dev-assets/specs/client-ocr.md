# Client OCR For Inline Images

## Goal

When I right-click an inline image inside the desktop Markdown editor, I want an `OCR Text Extract...` action that runs OCR on that image and shows the extracted text in a modal dialog with easy copy support.

This should work in both places that currently expose the inline-image context menu:

- the main Markdown editor
- the page editor popup window

## Why This Should Exist

StillPoint already supports OCR for image attachments elsewhere in the app. This feature makes that capability directly usable while editing notes, without forcing me to leave the page or manually open the image file.

## Product Scope

This feature is specifically for the desktop app's inline Markdown image workflow.

In scope:

- right-clicking an inline image rendered inside the editor
- extracting text from the referenced image file
- showing the OCR output in a read-only popup/dialog
- copying the OCR result to the system clipboard

Out of scope for this version:

- OCR on arbitrary screen regions
- OCR on PDF pages
- OCR on images from the attachments panel context menu
- automatically inserting OCR text back into the page
- background indexing/search changes
- web client support

## UX Requirements

### Context menu

When right-clicking an inline image in the Markdown editor context menu, add:

- `OCR Text Extract...`

Placement:

- keep it in the image-specific menu
- place it near the existing image actions, after the resize actions is acceptable
- use the same label in the main editor and page editor popup

Enablement:

- enable only when the clicked image resolves to a real local image file that StillPoint can open
- if the image target cannot be resolved, show the action disabled or do nothing except surface a clear error message when chosen

### OCR result dialog

Selecting `OCR Text Extract...` opens a modal dialog with:

- title: `Extracted Text`
- a large read-only text area containing the OCR output
- `Copy to Clipboard` button
- `Close` button

Behavior:

- the text area should allow selecting text manually
- `Copy to Clipboard` copies the full OCR result, not just the current selection
- after copy, show a lightweight confirmation in the status bar or dialog
- if OCR returns an empty string, still open the dialog and clearly say no text was detected

## Functional Requirements

### Image source resolution

The feature must work for inline images already displayed by the editor.

Implementation expectation:

- reuse the editor's existing image hit-testing from the inline image context menu
- resolve the clicked image back to its underlying local file path
- do not OCR the scaled rendered pixmap from the editor widget
- OCR the original image file on disk

If the editor cannot reliably map the clicked inline image back to a file path, stop and solve that mapping first before building the dialog flow.

### OCR engine

Do not introduce a second OCR stack for this feature.

Use the app's existing Python-side OCR path where possible, specifically the same Tesseract-backed approach already used for image text extraction in the repo.

Implications:

- prefer reusing shared OCR helper code over duplicating `pytesseract` calls in UI code
- keep the OCR logic outside the widget as much as practical
- the editor should trigger OCR; a helper should perform OCR

### Supported formats

Version 1 should support the same common image types already handled by existing OCR code:

- `.png`
- `.jpg`
- `.jpeg`
- `.bmp`
- `.tiff`

If unsupported image types are encountered, show a clear error message instead of failing silently.

### Threading / responsiveness

Running Tesseract can be slow enough to freeze the UI if done inline.

Requirement:

- do not block the UI thread while OCR is running on larger images
- show a minimal busy/progress state while OCR is in flight
- disable duplicate OCR launches for the same dialog/menu interaction

Acceptable v1 behavior:

- modal progress indicator while OCR runs in a worker thread
- then replace progress state with the extracted-text dialog

## Error Handling

Handle these cases explicitly:

- image path cannot be resolved
- image file is missing
- image format is unsupported
- Tesseract is unavailable on the machine
- OCR throws an exception
- OCR succeeds but returns no text

User-facing rule:

- every failure path should produce a clear, non-technical message
- failures should not crash the editor or leave focus in a broken state

Suggested messages:

- `Could not locate the image file for OCR.`
- `This image format is not supported for OCR.`
- `Tesseract OCR is not available on this system.`
- `No readable text was detected in this image.`

## Integration Notes

Expected implementation points:

- `sp/app/ui/markdown_editor.py`
  - add the new action to the inline-image context menu in the main editor
- `sp/app/ui/page_editor_window.py`
  - add the same action to the popup editor's inline-image context menu
- shared OCR helper
  - reuse or extend existing image OCR helper logic rather than duplicating it in both menu handlers

Important:

- keep the main editor and popup editor behavior identical
- avoid copy/pasting separate OCR implementations into both files

## Acceptance Criteria

1. Right-clicking an inline image in the main Markdown editor shows `OCR Text Extract...`.
2. Right-clicking an inline image in the page editor popup shows the same action.
3. Choosing the action runs OCR against the original image file on disk.
4. The UI stays responsive while OCR runs.
5. A modal result dialog opens with the extracted text in a read-only text area.
6. `Copy to Clipboard` copies the entire OCR result.
7. Empty OCR results are handled cleanly with a clear message.
8. Missing-file, unsupported-format, and Tesseract-not-installed cases all fail gracefully.
9. No duplicate OCR engine implementation is introduced just for this feature.

## Implementation Advice Before Lock-In

The main design risk here is calling this "client OCR" as if it should be separate from the app's existing OCR path. In this repo, that would be the wrong direction. The better contract is:

- desktop UI entry point
- shared Python OCR helper
- worker-thread execution
- result dialog

That keeps packaging, behavior, and dependency handling aligned with the rest of the app.

The other risk is assuming the image context menu already knows the file path. The current menu code clearly knows which inline image was clicked, but this spec should require path resolution to the source attachment before implementation is considered complete. Without that, the feature will become fragile fast.
