# Homebase Server Seeding

This document describes the server-side scripts for seeding Homebase vaults directly from a plaintext staging folder on the server.

These scripts are for operational seeding, not normal client sync.

They let you:

- create a Homebase vault directly on the server
- seed an existing Homebase vault from a plaintext folder already on the server
- dry-run the seeding process before writing anything

## Scripts

The scripts live in `tools/`:

- [tools/homebase-seed-vault.py](/home/grnwood/code/stillpoint/tools/homebase-seed-vault.py)
- [tools/homebase-create-and-seed-vault.py](/home/grnwood/code/stillpoint/tools/homebase-create-and-seed-vault.py)

Shared logic lives in:

- [tools/homebase_seed_lib.py](/home/grnwood/code/stillpoint/tools/homebase_seed_lib.py)

## What the Seeder Does

The seeder treats a server-local plaintext folder as a staging vault.

It:

1. walks the staging folder using the same sync file filtering rules as the client
2. encrypts each file with the Homebase vault passphrase
3. computes Homebase object ids
4. writes objects to `homebase/<vault_id>/objects/...`
5. writes a manifest to `homebase/<vault_id>/manifests/...`
6. writes a checkpoint record to `homebase/<vault_id>/checkpoints/...`
7. updates `homebase/<vault_id>/refs/latest.json`

The resulting Homebase vault is compatible with the normal StillPoint Homebase client/server sync format.

## Script 1: Seed an Existing Homebase Vault

Use this when:

- the Homebase vault already exists on the server
- auth/users are already configured
- you want to seed or replace the remote snapshot from a plaintext staging folder

Example:

```bash
python3 tools/homebase-seed-vault.py \
  --vaults-root /opt/stillpoint/vaults \
  --vault-id 123e4567-e89b-12d3-a456-426614174000 \
  --source /opt/stillpoint/staging/my-seed-vault \
  --passphrase 'shared-homebase-passphrase' \
  --device-id server-seed \
  --vault-name "Seeded Vault" \
  --overwrite-latest
```

### Required arguments

- `--vaults-root`
- `--vault-id`
- `--source`
- `--passphrase`

### Optional arguments

- `--device-id`
- `--vault-name`
- `--overwrite-latest`
- `--dry-run`

### Dry run

Dry run computes the manifest/checkpoint and prints what would happen without mutating the Homebase vault.

Example:

```bash
python3 tools/homebase-seed-vault.py \
  --vaults-root /opt/stillpoint/vaults \
  --vault-id 123e4567-e89b-12d3-a456-426614174000 \
  --source /opt/stillpoint/staging/my-seed-vault \
  --passphrase 'shared-homebase-passphrase' \
  --dry-run \
  --overwrite-latest
```

### Latest pointer safety

If `refs/latest.json` already exists, the script refuses to replace it unless you pass `--overwrite-latest`.

That is intentional because seeding can replace the remote vault head.

## Script 2: Create and Seed a New Homebase Vault

Use this when:

- the Homebase vault does not exist yet
- you want to create auth/meta and seed content in one step

Example:

```bash
python3 tools/homebase-create-and-seed-vault.py \
  --vaults-root /opt/stillpoint/vaults \
  --source /opt/stillpoint/staging/my-seed-vault \
  --username alice \
  --password 'vault-admin-password' \
  --passphrase 'shared-homebase-passphrase' \
  --vault-name "Seeded Vault"
```

### Required arguments

- `--vaults-root`
- `--source`
- `--username`
- `--password`
- `--passphrase`

### Optional arguments

- `--vault-name`
- `--vault-id`
- `--device-id`
- `--dry-run`
- `--force`

### What it creates

The companion script creates:

- `homebase/<vault_id>/auth/auth.json`
- `homebase/<vault_id>/meta.json`

Then it seeds the content and publishes the first latest checkpoint.

### Dry run

Dry run prints the planned create+seed inputs without writing anything.

Example:

```bash
python3 tools/homebase-create-and-seed-vault.py \
  --vaults-root /opt/stillpoint/vaults \
  --source /opt/stillpoint/staging/my-seed-vault \
  --username alice \
  --password 'vault-admin-password' \
  --passphrase 'shared-homebase-passphrase' \
  --vault-name "Seeded Vault" \
  --dry-run
```

## Recommended Operational Flow

### Seed a brand new Homebase vault

1. Prepare a plaintext staging folder on the server.
2. Run `homebase-create-and-seed-vault.py`.
3. Note the returned `vault_id`.
4. Connect clients to that Homebase vault id using the same passphrase.

### Replace the snapshot of an existing Homebase vault

1. Prepare or refresh the plaintext staging folder.
2. Run `homebase-seed-vault.py --overwrite-latest`.
3. Clients will see a new latest checkpoint on next sync.

## Important Notes

- These scripts bypass the normal client upload flow.
- They are intended for server-side staging and controlled operational seeding.
- The staging folder is plaintext, but Homebase storage remains encrypted.
- The passphrase must match what clients will later use for that Homebase vault.
- Re-seeding an existing vault head changes what clients will pull next.

## Verification

Focused tests for this tooling live in:

- [tests/test_homebase_seed_tool.py](/home/grnwood/code/stillpoint/tests/test_homebase_seed_tool.py)
