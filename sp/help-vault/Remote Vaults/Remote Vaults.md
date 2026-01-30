# Remote Vaults

## What Are Remote Vaults?
Remote vaults let you access your notes from anywhere.
- Your notes are stored on a server instead of just locally.
- Access the same vault from multiple devices.
- Collaborate with others (if server allows).

## Setting Up a Remote Vault
1. **Start the Server**: Run StillPoint with `--server` flag
2. **Configure Access**: Set up authentication on the server
3. **Connect Client**: Use File → Open Remote Vault in the app
4. **Enter Details**: Provide server URL and credentials

## Server Setup
To run your own server:
```bash
stillpoint --server --port 8080
```
- The server serves your local vault over HTTP.
- Add `--auth` for password protection.
- Use `--ssl` for encrypted connections.

## Connecting to a Remote Vault
- Choose File → Open Remote Vault
- Enter the server URL (e.g., `http://server.com:8080`)
- Provide username/password if required
- The remote vault appears alongside local vaults

## Syncing Changes
- Changes sync automatically when you save.
- The server keeps the master copy of your notes.
- Conflicts are rare but resolved by timestamp.

## Offline Access
- Remote vaults work when online.
- Changes are cached locally when offline.
- Sync resumes when connection returns.

## Security Considerations
- Use HTTPS for public networks.
- Choose strong passwords.
- Keep server software updated.
- Consider firewall rules for your server.

## Tips
- Back up your server data regularly.
- Test connections from different networks.
- Use remote vaults for shared team notes.
