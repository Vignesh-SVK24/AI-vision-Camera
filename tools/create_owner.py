"""CLI command to create the local-first dashboard owner account."""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path
from typing import Optional

# Ensure project root is importable when running `python tools/create_owner.py`.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.auth import create_owner_account
from config.logging_config import setup_logging
from config.settings import get_settings
from storage.db import Database


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create the local owner account for the backend API."
    )
    parser.add_argument(
        "--username",
        required=True,
        help="Owner username.",
    )
    parser.add_argument(
        "--password",
        default=None,
        help="Optional password (omit to enter securely via prompt).",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    settings = get_settings()
    setup_logging(settings)

    password = args.password
    if password is None:
        pw1 = getpass.getpass("Owner password (min 8 chars): ")
        pw2 = getpass.getpass("Confirm password: ")
        if pw1 != pw2:
            print("Passwords do not match.", file=sys.stderr)
            return 1
        password = pw1

    with Database(settings=settings) as db:
        try:
            user_id = create_owner_account(
                db,
                username=args.username,
                password=password,
            )
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

    print(f"Created owner account '{args.username}' (id={user_id}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
