# Quick Capture

Quick Capture is a fast, no-friction way to drop a thought into today's notes.
It always goes to a predictable place so you can sort it later.

## Where Captures Go

Captures are appended to today's page under:

```
## Inbox / Captures
```

Each capture is a bullet with a timestamp in italics.

Example:

```
## Inbox / Captures
- *08:16am* - Remember to follow up with Dave about API limits

---
```

If the capture goes to a non-journal page, the timestamp includes the date:

```
- *2026-01-20: 08:19am* - Idea: graph filter as project mode
```

## How to Use It

- **Vault menu:** Vault -> Quick Capture...
- **Tray icon:** Use the tray menu item "Quick Capture..."
- **CLI:** `stillpoint --quick-capture`
  - `echo "Idea..." | stillpoint --quick-capture`
  - `stillpoint --quick-capture --text "Idea..."`

## Configure the Destination

Open **Preferences -> General -> Capture**:

- Home Quick Capture Vault
- Default Capture Page
  - Today Journal Page
  - Custom Page (a single page name or colon path)

If no home vault is set, StillPoint falls back to the currently open vault.

## Troubleshooting

- If the overlay does not open, make sure a home vault is set.
- If your capture doesn't appear, hit the refresh button on the left nav.
