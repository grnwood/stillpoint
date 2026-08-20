
This is probably not a normal passphrase-entry problem. The client successfully downloads the server object for `0-Home/0-Home.md`, but its authenticated decryption fails. “Reset Sync State (Server Authoritative)” cannot fix that because it downloads the same broken server snapshot again.

A vault reorganization could have exposed an older root-page object, especially around the canonical `0-Home/0-Home.md` path, but moving files alone does not damage encryption.

Do this first:

1. Stop StillPoint/Homebase sync on every device.
2. Back up the complete local vault from the device with the best copy. Do not run server-authoritative reset on that device.
3. Back up the server directory:
   `/opt/stillpoint/vaults/homebase/<vault-id>`

Then verify the affected server object:

```bash
HB=/opt/stillpoint/vaults/homebase/<vault-id>
CP=$(jq -r '.checkpoint_id' "$HB/refs/latest.json")
OID=$(jq -r '.entries["0-Home/0-Home.md"].object_id' \
  "$HB/manifests/${CP:0:2}/$CP")

echo "checkpoint=$CP"
echo "object=$OID"
sha256sum "$HB/objects/${OID:0:2}/$OID"
```

The hash printed by `sha256sum` must equal `$OID`.

- If it differs, the object is physically corrupted. Restore that object from a server backup.
- If it matches, the object is intact but encrypted using a different key—usually an older/different passphrase or vault ID. Resetting the passphrase in StillPoint does not re-encrypt existing server objects.

The most reliable recovery, assuming you have one complete and correct local vault, is to publish that copy as a new server snapshot using the included seeder:

```bash
python3 tools/homebase-seed-vault.py \
  --vaults-root /opt/stillpoint/vaults \
  --vault-id <vault-id> \
  --source /path/to/good/plaintext-vault-copy \
  --passphrase '<the-shared-passphrase>' \
  --device-id recovery-seed \
  --dry-run \
  --overwrite-latest
```

Inspect the dry-run result, then repeat without `--dry-run`. On a packaged server, the command is normally:

```bash
/opt/stillpoint/app/tools/homebase-seed-vault ...
```

After the new checkpoint is published, use “Reset Sync State (Server Authoritative)” on the other devices. Keep the old server directory backup until every device has synced successfully.

Do not replace the encrypted object file in place: its filename is its ciphertext hash, so repairing content requires a new object, manifest, and checkpoint.
