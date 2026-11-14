"""Evaluate local face match scores on test photos to tune FACE_MATCH_THRESHOLD."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

# Ensure project root is importable when running `python tools/evaluate_faces.py`.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import cv2

from config.logging_config import setup_logging
from config.settings import get_settings
from faces.matcher import UNKNOWN_NAME, FaceMatcher
from storage.db import Database

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Print cosine similarity scores on test photos for threshold tuning."
    )
    parser.add_argument(
        "--test-dir",
        required=True,
        help=(
            "Directory containing subfolders per person name (e.g. Alice/*.jpg, "
            "Unknown/*.jpg or Stranger/*.jpg)."
        ),
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="Optional threshold override (default: FACE_MATCH_THRESHOLD from .env).",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    settings = get_settings()
    setup_logging(settings)

    test_dir = Path(args.test_dir)
    if not test_dir.exists() or not test_dir.is_dir():
        print(f"Test directory not found: {test_dir}", file=sys.stderr)
        return 1

    with Database(settings=settings) as db:
        matcher = FaceMatcher(db, settings=settings, threshold=args.threshold)
        if matcher.enrolled_count == 0:
            print("Warning: No enrolled embeddings found in database.")

        genuine_scores: list[float] = []
        impostor_scores: list[float] = []
        total = 0
        correct = 0

        print(f"Evaluating photos in {test_dir} (threshold={matcher.threshold:.3f})\n")
        print(f"{'Expected':<16} {'Predicted':<16} {'Score':>7}  {'OK':<4}  Photo")
        print("-" * 76)

        for person_dir in sorted(p for p in test_dir.iterdir() if p.is_dir()):
            expected_raw = person_dir.name.strip()
            expected = (
                UNKNOWN_NAME
                if expected_raw.lower() in ("unknown", "stranger", "strangers", "other")
                else expected_raw
            )

            for img_path in sorted(person_dir.iterdir()):
                if img_path.suffix.lower() not in IMAGE_EXTENSIONS:
                    continue
                img = cv2.imread(str(img_path))
                if img is None:
                    print(f"{expected:<16} {'UNREADABLE':<16} {'-':>7}  {'NO':<4}  {img_path.name}")
                    continue

                match = matcher.match_image(img)
                if match is None:
                    print(f"{expected:<16} {'NO_FACE':<16} {'-':>7}  {'NO':<4}  {img_path.name}")
                    total += 1
                    continue

                total += 1
                is_ok = match.name.lower() == expected.lower()
                if is_ok:
                    correct += 1

                if expected == UNKNOWN_NAME:
                    impostor_scores.append(match.score)
                else:
                    genuine_scores.append(match.score)

                print(
                    f"{expected:<16} {match.name:<16} {match.score:>7.4f}  "
                    f"{'YES' if is_ok else 'NO':<4}  {person_dir.name}/{img_path.name}"
                )

        print("-" * 76)
        if total == 0:
            print("No test images found.")
            return 1

        print(f"Accuracy: {correct}/{total} ({(correct / total) * 100.0:.1f}%)")
        if genuine_scores:
            print(
                f"Genuine scores  : min={min(genuine_scores):.4f}, "
                f"avg={sum(genuine_scores) / len(genuine_scores):.4f}, "
                f"max={max(genuine_scores):.4f}"
            )
        if impostor_scores:
            print(
                f"Stranger scores : min={min(impostor_scores):.4f}, "
                f"avg={sum(impostor_scores) / len(impostor_scores):.4f}, "
                f"max={max(impostor_scores):.4f}"
            )
        if genuine_scores and impostor_scores:
            suggested = (min(genuine_scores) + max(impostor_scores)) / 2.0
            print(f"Suggested midpoint threshold: {suggested:.4f}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
