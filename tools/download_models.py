"""Download official OpenCV Zoo YuNet and SFace ONNX models locally."""

from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path
from typing import Optional

# Ensure project root is importable when running `python tools/download_models.py`.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import get_settings

OFFICIAL_MODELS: dict[str, str] = {
    "yunet": (
        "https://github.com/opencv/opencv_zoo/raw/main/models/"
        "face_detection_yunet/face_detection_yunet_2023mar.onnx"
    ),
    "sface": (
        "https://github.com/opencv/opencv_zoo/raw/main/models/"
        "face_recognition_sface/face_recognition_sface_2021dec.onnx"
    ),
}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download_file(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = destination.with_suffix(destination.suffix + ".part")
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "vision-surveillance-model-downloader/1.0"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp, tmp_path.open("wb") as out:
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            out.write(chunk)

    if tmp_path.stat().st_size == 0:
        tmp_path.unlink(missing_ok=True)
        raise RuntimeError(f"Downloaded empty file from {url}")
    tmp_path.replace(destination)


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download official OpenCV YuNet and SFace ONNX models."
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip interactive confirmation prompt and proceed with download.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download models even if they already exist locally.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    settings = get_settings()

    targets = [
        ("YuNet (face detection)", OFFICIAL_MODELS["yunet"], settings.face_yunet_model),
        ("SFace (face recognition)", OFFICIAL_MODELS["sface"], settings.face_sface_model),
    ]

    pending = [
        (label, url, dest)
        for label, url, dest in targets
        if args.force or not dest.exists() or dest.stat().st_size == 0
    ]

    if not pending:
        for label, _, dest in targets:
            print(f"[OK] {label} already present: {dest} ({dest.stat().st_size} bytes)")
        return 0

    print("The following official OpenCV Zoo model files will be downloaded locally:")
    for label, url, dest in pending:
        print(f"  - {label}")
        print(f"    URL : {url}")
        print(f"    Dest: {dest}")

    if not args.yes:
        try:
            answer = input("Proceed with download? [y/N]: ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            print("Download cancelled.")
            return 1

    for label, url, dest in pending:
        print(f"Downloading {label}...")
        _download_file(url, dest)
        checksum = _sha256_file(dest)
        print(
            f"[DONE] {dest} ({dest.stat().st_size} bytes, sha256={checksum[:16]}...)"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
