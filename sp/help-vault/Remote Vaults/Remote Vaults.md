# Remote Vaults

![paste_image_001](./paste_image_001.png){width=600}

StillPoint supports two remote-capable vault models:
- **Remote Vaults**: a live server-hosted vault (server is source of truth).
- **Homebase Vaults**: a local-first, Git-like sync model (devices push/pull with eventual consistency).

Use this page to decide which model fits your workflow and trust boundary.

## Remote Vaults (Live Server Model)

### What They Are
Remote Vaults are centralized vaults hosted on a server.
- The server holds the active vault content.
- Clients connect directly to that vault over HTTP(S).
- Best for teams, shared editing, and centralized administration.

### User Management
Remote Vaults support per-user access control.
- Roles: `admin` and `normal`.
- Normal user permissions: `read` or `read+write`.
- Admin-only actions: manage users, create users, delete users, edit user role/perm.
- All users can reset their own password.

### Sync and Behavior
- Pull/push is live against the server vault.
- Writes happen to the server copy.
- Read-only users can browse but cannot save changes.
- UI should indicate write restrictions when the user lacks write permission.

### When To Choose Remote Vaults
- Team collaboration in a single shared vault.
- Centralized user management and policy.
- You trust the server environment to host vault content.

## Homebase Vaults (Git-like Local-First Model)

### What They Are
Homebase Vaults are local-first with distributed sync.
- Each device keeps a local working copy.
- Devices sync by pushing/pulling through the Homebase server.
- The model is **Git-like**: local work first, then synchronize.

### Sync Model
- Push/pull with **eventual consistency**.
- Works offline, then syncs when connected.
- Conflict-aware workflow when concurrent edits happen.

### User Management
Homebase supports account auth and user management APIs.
- Roles and write permissions are enforced by server auth.
- Admin users can manage users.
- Normal read users can authenticate and view, but writes are blocked.

### When To Choose Homebase
- You need multi-device continuity with offline-first behavior.
- You want a distributed sync mental model similar to Git.
- You prefer local-first work with periodic synchronization.

## Security Model Comparison

### Remote Vaults Security
- Server enforces authentication and authorization.
- Vault content is stored server-side as the active source of truth.
- Use HTTPS, strong credentials, and proper server hardening.

### Homebase Security
- Server stores encrypted blobs and sync metadata.
- Encryption passphrase stays local on client devices.
- Auth tokens control access, but tokens are not your encryption key.

### Practical Takeaway
- **Remote Vaults** optimize for collaboration and central control.
- **Homebase Vaults** optimize for local-first workflow and privacy boundaries.

## Quick Decision Guide
- Pick **Remote Vaults** if you want one live shared vault with admin-managed access.
- Pick **Homebase Vaults** if you want Git-like local copies and eventual sync across devices.

## Setup Notes

### Remote Vault (Typical)
1. Start server mode.
2. Configure auth.
3. Add/open remote vault from the app.
4. Login and use `Vault > Remote Vault` actions as needed.

### Homebase Vault (Typical)
1. Create/connect a Homebase vault profile.
2. Authenticate to Homebase.
3. Let local sync engine pull/push changes.
4. Use sync status and conflict tools from the Homebase UI.

## Operational Tips
- Back up server-side data regularly.
- Use TLS for any non-local network use.
- Review user roles periodically.
- For read-only users, verify UI is in read-only mode and save attempts show clear status messages.
