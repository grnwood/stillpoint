# Clipboard Behavior Matrix (Markdown Editor)

This document defines the expected clipboard contract for `MarkdownEditor`.

## Core principles

- Never leak display sentinels into clipboard output.
- Preserve rich internal markdown fidelity when copying inside StillPoint.
- Keep external clipboard output readable and standards-friendly.
- Route copy/cut/paste flows through shared normalization helpers.

## MIME contract by action

### 1) Standard Copy (`Ctrl+C`, context `Copy`)

- Source: selected editor content.
- Clipboard MIME:
  - `text/plain`: sanitized markdown-like text.
  - `application/x-stillpoint-markdown`: storage markdown payload.
- Goal: best internal round-trip while still producing useful plain text externally.

### 2) Copy As Markdown (context `Copy As Markdown`)

- Source: selection (or whole page if no selection).
- Transform: storage links `[target|label]` -> markdown links `[label](target)`.
- Clipboard MIME:
  - `text/plain`: sanitized markdown text.
  - `text/markdown`: markdown payload.
  - no `application/x-stillpoint-markdown`.
- Goal: external markdown interoperability.

### 3) HTML Copy helper (`_copy_selection_as_html`)

- Source: selected storage markdown.
- Clipboard MIME:
  - `text/html`: rendered markdown HTML.
  - `text/plain`: markdown text fallback.
  - `text/markdown`: markdown payload.
  - `application/x-stillpoint-markdown`: internal payload.

### 4) Standard Paste (`Ctrl+V`, `insertFromMimeData`)

Paste priority order:

1. `application/x-stillpoint-markdown`
2. image payload
3. `text/markdown`
4. HTML + text heuristics
5. HTML conversion to markdown-like plain text
6. `text/plain`

Notes:

- Input is sanitized to strip control/problematic characters.
- Plain URLs may be wrapped as `[url|]` for stable internal rendering.
- Link-ish input is converted through display encoding for immediate visual behavior.

### 5) vi copy/cut/paste

- vi copy/cut (`c`, `x`) stores normalized markdown in `_vi_clipboard` and updates system plain text.
- vi paste (`p`) prefers clipboard payload in this order:
  1. `application/x-stillpoint-markdown`
  2. `text/markdown`
  3. `text/plain`
- vi paste inserts through `_insert_markdown_text` (same normalization/render path as standard paste text path).

## Selection semantics (important)

- Link-preserving collapse to a single storage link only occurs when selection is link-scoped.
- Multi-line/multi-paragraph selections containing links must preserve the full selected text.

## Key shared helpers

- `_selection_storage_markdown(cursor)`
- `_set_clipboard_markdown(...)`
- `_clipboard_markdown_payload()`
- `_insert_markdown_text(text)`

## Regression tests that enforce this contract

- `tests/test_markdown_link_rendering.py`
  - copy payload and sentinel stripping
  - copy-as-markdown conversion and MIME behavior
  - multi-paragraph copy-as-markdown with links
  - paste priority and html/markdown normalization
- `tests/test_vi_mode.py`
  - vi copy/cut/paste cycle
  - vi paste preference for internal markdown payload
