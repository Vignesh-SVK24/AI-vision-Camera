"""CLI tool for local privacy-first family face enrollment and deletion."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

# Ensure project root is importable when running `python tools/enroll.py`.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.logging_config import setup_logging
from config.settings import get_settings
from faces.enrollment import ConsentRecord, EnrollmentError, add_person, delete_person
from storage.db import Database


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Manage locally enrolled people (no data is ever uploaded)."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    add_cmd = sub.add_parser("add", help="Enroll a person with explicit consent.")
    add_cmd.add_argument("--name", required=True, help="Display name of the person.")
    add_cmd.add_argument(
        "--images",
        nargs="+",
        required=True,
        help="One or more image file paths.",
    )
    add_cmd.add_argument(
        "--consent-date",
        required=True,
        help="Consent date (YYYY-MM-DD).",
    )
    add_cmd.add_argument(
        "--consent-note",
        required=True,
        help="Short consent record note (e.g. 'Signed family consent').",
    )
    add_cmd.add_argument(
        "--no-save-photos",
        action="store_true",
        help="Store embeddings only without saving photo copies.",
    )

    del_cmd = sub.add_parser("delete", help="Delete an enrolled person and all their data.")
    group = del_cmd.add_mutually_exclusive_group(required=True)
    group.add_argument("--id", type=int, help="Person ID to delete.")
    group.add_argument("--name", type=str, help="Person name to delete.")

    sub.add_parser("list", help="List locally enrolled people.")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    settings = get_settings()
    setup_logging(settings)

    with Database(settings=settings) as db:
        if args.command == "add":
            try:
                result = add_person(
                    db,
                    name=args.name,
                    images=args.images,
                    consent=ConsentRecord(
                        date=args.consent_date,
                        note=args.consent_note,
                    ),
                    settings=settings,
                    save_photos=not args.no_save_photos,
                )
            except (EnrollmentError, FileNotFoundError) as exc:
                print(f"Enrollment failed: {exc}", file=sys.stderr)
                return 1

            print(
                f"Enrolled '{result.name}' (id={result.person_id}) with "
                f"{result.enrolled_embeddings} embedding(s)."
            )
            if result.rejected_reasons:
                print("Skipped photos:")
                for path, reason in result.rejected_reasons.items():
                    print(f"  - {path}: {reason}")
            return 0

        if args.command == "delete":
            identifier = args.id if args.id is not None else args.name
            removed = delete_person(db, identifier, settings=settings)
            if removed == 0:
                print(f"No matching person found for '{identifier}'.")
                return 1
            print(f"Deleted {removed} person record(s), embeddings, and local photos.")
            return 0

        if args.command == "list":
            people = db.list_people()
            if not people:
                print("No enrolled people.")
                return 0
            for p in people:
                print(
                    f"id={p['id']} | name={p['name']} | embeddings={p['embedding_count']} | "
                    f"photos={p['photo_count']} | consent={p.get('consent_date') or 'N/A'}"
                )
            return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
