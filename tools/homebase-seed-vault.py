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
        description="Seed an existing Homebase vault directly from a plaintext staging folder on the server."
    )
    parser.add_argument("--vaults-root", required=True, help="StillPoint vaults root that contains the homebase/ directory.")
    parser.add_argument("--vault-id", required=True, help="Existing Homebase vault id to seed.")
    parser.add_argument("--source", required=True, help="Plaintext staging folder to ingest.")
    parser.add_argument("--passphrase", required=True, help="Homebase encryption passphrase for this vault.")
    parser.add_argument("--device-id", default="server-seed", help="Device id recorded in the manifest.")
    parser.add_argument("--vault-name", default="", help="Optional vault name to write into meta.json if needed.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute the Homebase manifest/checkpoint and print what would be written without mutating the vault.",
    )
    parser.add_argument(
        "--overwrite-latest",
        action="store_true",
        help="Allow replacing an existing latest checkpoint pointer for this Homebase vault.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = seed_homebase_vault(
            vaults_root=Path(args.vaults_root).expanduser().resolve(),
            vault_id=str(args.vault_id).strip(),
            source_root=Path(args.source).expanduser().resolve(),
            passphrase=args.passphrase,
            device_id=str(args.device_id or "server-seed").strip() or "server-seed",
            overwrite_latest=bool(args.overwrite_latest),
            vault_name=str(args.vault_name or "").strip() or None,
            dry_run=bool(args.dry_run),
            progress=_print_progress,
        )
    except Exception as exc:
        print(f"Homebase seed failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
