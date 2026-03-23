# Homebase Vaults

![paste_image_001](./paste_image_001.png){width=600}

StillPoint is now a local-first system:
- **Local Vaults** live directly on your filesystem.
- **Homebase Vaults** add sync on top of that local filesystem model.

Homebase is the only remote-capable vault model. Each device keeps a real local copy of the vault, and Homebase synchronizes those copies through the server.

## What Homebase Is

Homebase Vaults are local-first with distributed sync.
- Each device keeps a local working copy.
- Devices sync by pushing/pulling through the Homebase server.
- The model is **Git-like**: local work first, then synchronize.
- Your vault is still a normal folder full of Markdown files and attachments.

## Sync Model
- Push/pull with **eventual consistency**.
- Works offline, then syncs when connected.
- Conflict-aware workflow when concurrent edits happen.
- Filesystem changes made by StillPoint, your editor, scripts, or CLI tools all flow into the same local vault and can then sync through Homebase.

## User Management
Homebase supports account auth and user management APIs.
- Roles and write permissions are enforced by server auth.
- Admin users can manage users.
- Normal read users can authenticate and view, but writes are blocked.

## When To Choose Homebase
- You need multi-device continuity with offline-first behavior.
- You want a distributed sync mental model similar to Git.
- You prefer local-first work with periodic synchronization.
- You want the server to be outside your plaintext trust boundary.
- You want to use local editors, coding agents, or TUIs against the vault files themselves.

## Security Model
- Server stores encrypted blobs and sync metadata.
- Encryption passphrase stays local on client devices.
- Auth tokens control access, but tokens are not your encryption key.

## Passphrase Storage
- By default, StillPoint does not store your Homebase encryption passphrase on disk.
- During Homebase setup or `Reset Encryption Passphrase`, you can opt into `Store passphrase on this device`.
- Only enable that option if you trust the device. The passphrase is then stored in the vault's local StillPoint config.
- Passwords are still not stored. You authenticate once, then StillPoint reuses Homebase access and refresh tokens.
- StillPoint also writes non-secret Homebase recovery metadata into the vault under `.stillpoint/homebase.json` so re-adding the vault later can prefill the server URL, SSL mode, vault ID, and vault name.

## Practical Takeaway
- Use a **Local Vault** when you only need files on one machine.
- Use a **Homebase Vault** when you want sync, offline-first behavior, and a stronger local-first trust boundary.

## Coding Agents and TUIs
StillPoint works especially well with coding agents and terminal workflows because the vault is a real local folder.

Recommended workflow:
1. Open the vault locally.
2. Use `Vault -> Open Vault in Terminal`.
3. Run your coding agent, TUI, or CLI tool in that vault root.
4. Let StillPoint detect filesystem changes and refresh the tree.
5. If Homebase is enabled, let sync push those local changes to the server.

StillPoint seeds an `AGENTS.md` file at the vault root if that preference is enabled. That file teaches agents the StillPoint page structure, colon-link rules, and page-folder conventions.

This makes Homebase a strong fit for:
- AI-assisted research vault growth
- script-driven imports
- TUI note workflows
- code-generated pages and attachments

## Setup Notes

### Local Vault
1. Create or open a vault folder on disk.
2. Work directly against that folder in StillPoint or other local tools.

### Homebase Vault
1. Create/connect a Homebase vault profile.
2. Authenticate to Homebase.
3. Decide whether to keep the encryption passphrase session-only or store it on this trusted device.
4. Let local sync engine pull/push changes.
5. Use sync status and conflict tools from the Homebase UI.

## Operational Tips
- Back up your local vault and Homebase server data regularly.
- Use TLS for any non-local network use.
- Review user roles periodically.
- For agent or CLI workflows, prefer additive page creation and let StillPoint/Homebase handle the sync layer.
