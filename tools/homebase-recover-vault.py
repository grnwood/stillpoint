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

from tools.homebase_recover_lib import load_passphrases, recover_homebase_vault


def _print_progress(message: str) -> None:
    print(f"[HomebaseRecover] {message}", file=sys.stderr, flush=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read a Homebase checkpoint directly from server storage and extract every object "
            "that one of the supplied passphrases can decrypt. The Homebase store is never modified."
        )
    )
    parser.add_argument(
        "--vaults-root",
        required=True,
        help="StillPoint vaults root that contains the homebase/ directory.",
    )
    parser.add_argument("--vault-id", required=True, help="Homebase vault id to recover.")
    parser.add_argument(
        "--output",
        required=True,
        help="New plaintext staging directory to create outside the vaults root.",
    )
    parser.add_argument(
        "--checkpoint-id",
        default="",
        help="Optional checkpoint to recover; defaults to refs/latest.json.",
    )
    parser.add_argument(
        "--passphrase",
        action="append",
        default=[],
        help="Candidate encryption passphrase. Repeat to try multiple values.",
    )
    parser.add_argument(
        "--passphrase-file",
        action="append",
        default=[],
        help="File containing one candidate passphrase per line. Repeatable.",
    )
    parser.add_argument(
        "--report",
        default="",
        help="Optional new report path; defaults next to the output directory.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        passphrases = load_passphrases(
            direct_values=args.passphrase,
            passphrase_files=[Path(value).expanduser().resolve() for value in args.passphrase_file],
        )
        report = recover_homebase_vault(
            vaults_root=Path(args.vaults_root).expanduser().resolve(),
            vault_id=str(args.vault_id).strip(),
            output_root=Path(args.output).expanduser().resolve(),
            passphrases=passphrases,
            checkpoint_id=str(args.checkpoint_id or "").strip(),
            report_path=(
                Path(args.report).expanduser().resolve()
                if str(args.report or "").strip()
                else None
            ),
            progress=_print_progress,
        )
    except Exception as exc:
        print(f"Homebase recovery failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
