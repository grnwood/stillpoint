#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
INTERNAL_ROOT = PROJECT_ROOT / "_internal"
if INTERNAL_ROOT.exists() and str(INTERNAL_ROOT) not in sys.path:
    sys.path.insert(0, str(INTERNAL_ROOT))

from tools.homebase_seed_lib import create_homebase_vault, seed_homebase_vault


def _print_progress(message: str) -> None:
    print(f"[HomebaseSeed] {message}", file=sys.stderr, flush=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a Homebase vault directly on the server and seed it from a plaintext staging folder."
    )
    parser.add_argument("--vaults-root", required=True, help="StillPoint vaults root that contains the homebase/ directory.")
    parser.add_argument("--source", required=True, help="Plaintext staging folder to ingest.")
    parser.add_argument("--username", required=True, help="Admin username to create for the Homebase vault.")
    parser.add_argument("--password", required=True, help="Admin password to create for the Homebase vault.")
    parser.add_argument("--passphrase", required=True, help="Homebase encryption passphrase for the seeded content.")
    parser.add_argument("--vault-name", default="", help="Optional Homebase vault name.")
    parser.add_argument("--vault-id", default="", help="Optional explicit Homebase vault id. Defaults to a generated UUID.")
    parser.add_argument("--device-id", default="server-seed", help="Device id recorded in the manifest.")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be created and seeded without writing.")
    parser.add_argument("--force", action="store_true", help="Allow reusing an existing non-empty Homebase vault directory.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    vaults_root = Path(args.vaults_root).expanduser().resolve()
    source_root = Path(args.source).expanduser().resolve()
    vault_id = str(args.vault_id or "").strip() or "<generated>"
    if args.dry_run:
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "vaults_root": str(vaults_root),
                    "source_root": str(source_root),
                    "vault_id": vault_id,
                    "username": str(args.username).strip(),
                    "vault_name": str(args.vault_name or "").strip(),
                    "device_id": str(args.device_id or "server-seed").strip() or "server-seed",
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    try:
        created = create_homebase_vault(
            vaults_root=vaults_root,
            username=str(args.username).strip(),
            password=args.password,
            vault_name=str(args.vault_name or "").strip(),
            vault_id=str(args.vault_id or "").strip(),
            force=bool(args.force),
            progress=_print_progress,
        )
        seeded = seed_homebase_vault(
            vaults_root=vaults_root,
            vault_id=created["vault_id"],
            source_root=source_root,
            passphrase=args.passphrase,
            device_id=str(args.device_id or "server-seed").strip() or "server-seed",
            overwrite_latest=True,
            vault_name=str(args.vault_name or "").strip() or None,
            dry_run=False,
            progress=_print_progress,
        )
    except Exception as exc:
        print(f"Homebase create+seed failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"created": created, "seeded": seeded}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
