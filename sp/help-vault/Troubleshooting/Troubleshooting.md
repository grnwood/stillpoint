# Troubleshooting

This page covers common problems and the fastest way to fix them.

## Before You Start
Try these first:
1. Save your current page.
2. Use `Vault -> Reload Vault`.
3. If needed, close and reopen StillPoint.

## Pages Missing or Navigation Looks Wrong
- **Symptom**: A page exists on disk but does not show correctly in the tree.
- **Fix**: Use `Tools -> Rebuild Vault Index`.
- **What it does**: Rebuilds the vault index from files.

## Vault Search Missing Results
- **Symptom**: `Search Across Vault...` does not find expected text.
- **Fix**: Use `Tools -> Rebuild Vault Search Index`.
- **What it does**: Rebuilds the full-text search tables.
- **Note**: Large vaults can take time to finish.

## Vault Opened as Read-Only
- **Symptom**: You cannot save, rename, move, or create pages.
- **Most common cause**: Another StillPoint window already holds the vault lock.
- **Fixes**:
1. Close other StillPoint windows using that same vault.
2. Reopen the vault.
3. Check `Edit -> Preferences -> General -> Force read-only mode for this vault` and disable it if you want write access.

## Links Not Navigating As Expected
- **Symptom**: Clicking a link does not open the expected page.
- **Checks**:
1. Confirm the target page actually exists.
2. Confirm path spelling/case in the link.
3. If pages were moved recently, use `Tools -> Rebuild Vault Index`.

## Tasks Panel Looks Empty
- **Symptom**: Task list has fewer items than expected.
- **Checks**:
1. Use markdown task syntax like `- [ ] Task text` or `- [x] Done task`.
2. Make sure Tasks feature is enabled in Preferences.
3. Clear task search filters in the Tasks panel.

## Calendar Looks Empty or Incomplete
- **Symptom**: Calendar or day view is missing expected activity.
- **Checks**:
1. Verify Calendar feature is enabled in Preferences.
2. Ensure journal pages are under the expected `Journal/YYYY/MM/DD/` structure.
3. Reload the vault, then reopen the Calendar tab/window.

## AI Chat Not Working
- **Symptom**: AI tab/actions fail or show no response.
- **Checks**:
1. Enable AI in `Edit -> Preferences -> AI Chats and Agents`.
2. Open **Manage Servers** and verify `Base URL`, auth settings, and model.
3. Use **Verify** (server dialog) and **Refresh Models**.
4. Confirm a default server/model is selected.

## AI Agents Not Running Tools
- **Symptom**: Chat responds but does not perform tool actions (read/write/search).
- **Checks**:
1. Enable **AI Agents in chat** in Preferences.
2. Approve agent tools for the vault when prompted.
3. Ensure a vault is open (agent tools require an active vault context).

## Mermaid / PlantUML Diagrams Not Rendering
- **Mermaid checks**:
1. In Preferences -> Mermaid, click **Check Mermaid Install**.
2. Install Mermaid CLI if missing: `npm install -g @mermaid-js/mermaid-cli`.
- **PlantUML checks**:
1. In Preferences -> PlantUML, set `plantuml.jar` path.
2. Optionally set a Java path.
3. Click **Test PlantUML Setup**.

## App Startup Feels Slow
- **Possible cause**: Font scanning overhead on some systems.
- **Fix**: Enable `Use Minimal Font Scan (For Fast Window Startup)` in Preferences and restart.

## Homebase Authentication or Sync Problems
- **Symptom**: Homebase sync is not working or the vault keeps asking you to authenticate.
- **Checks**:
1. Verify the Homebase server URL and credentials.
2. Use `Vault -> Homebase -> Login - Authenticate to Homebase`.
3. Confirm the vault is configured for Homebase sync.
4. If sync reports that Homebase is not configured, check whether the encryption passphrase is missing for this session.
5. If you want the passphrase to survive restarts, use `Reset Encryption Passphrase` and enable `Store passphrase on this device` only on a trusted device.
6. Check network access and server availability.
7. If needed, use `Vault -> Homebase -> Sync Now`.

## Files Created in Terminal or by an Agent Do Not Appear
- **Symptom**: Files exist in the vault folder but the tree has not updated yet.
- **Checks**:
1. Wait for the local filesystem quiet period to complete.
2. Confirm the files were created inside the actual vault root.
3. Use `Tools -> Rebuild Vault Index` if the structure was created outside StillPoint.
4. If Homebase is enabled, wait for sync or use `Vault -> Homebase -> Sync Now`.
5. Make sure the generated files follow StillPoint page structure rules and are not writing into `.stillpoint/`.

## If You Still Need to Reset
Use this carefully after backing up your vault:
1. Close StillPoint.
2. Reopen and choose the vault again from the vault picker.
3. Rebuild index/search from `Tools` menu.

If settings are clearly corrupted, reset only app settings files in `~/.stillpoint` (not your vault content).

## Run from Command line
Launch Stillpoint from a console.

`$ stillpoint`

There will be detailed logging on `stdout` in the terminal.  Often times you can see what the issue is that way.

## Reporting a Problem
Include:
- OS and StillPoint version
- local vault or Homebase vault
- exact steps to reproduce
- error message text (or screenshot)
- whether `Tools -> Rebuild Vault Index` or `Rebuild Vault Search Index` changed behavior

[https://github.com/grnwood/stillpoint/issues|]
