"""JPEG snapshot resizing, date-folder storage, and cleanup helpers."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from config.settings import BASE_DIR

logger = logging.getLogger(__name__)


def save_snapshot(
    frame: np.ndarray,
    snapshots_dir: Path | str,
    *,
    prefix: str = "event",
    max_width: int = 480,
    quality: int = 80,
    timestamp: Optional[datetime] = None,
) -> Path:
    """Resize frame to at most max_width and save JPEG inside a YYYY-MM-DD folder."""
    if frame is None or frame.size == 0:
        raise ValueError("Cannot save snapshot from an empty frame.")

    dt = (
        timestamp.astimezone(timezone.utc)
        if timestamp is not None
        else datetime.now(timezone.utc)
    )
    root = Path(snapshots_dir)
    date_folder = root / dt.strftime("%Y-%m-%d")
    date_folder.mkdir(parents=True, exist_ok=True)

    height, width = frame.shape[:2]
    if width > max_width:
        scale = max_width / float(width)
        target_height = max(1, int(round(height * scale)))
        resized = cv2.resize(
            frame,
            (max_width, target_height),
            interpolation=cv2.INTER_AREA,
        )
    else:
        resized = frame

    safe_prefix = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in prefix)
    filename = f"{safe_prefix}_{dt.strftime('%Y%m%d_%H%M%S_%f')}_{uuid.uuid4().hex[:6]}.jpg"
    out_path = date_folder / filename

    clamped_quality = max(10, min(100, int(quality)))
    ok = cv2.imwrite(
        str(out_path),
        resized,
        [int(cv2.IMWRITE_JPEG_QUALITY), clamped_quality],
    )
    if not ok:
        raise RuntimeError(f"Failed to encode JPEG snapshot to {out_path}")

    return out_path


def format_snapshot_path(path: Path, base_dir: Path = BASE_DIR) -> str:
    """Return a clean relative path if inside base_dir, otherwise absolute string."""
    try:
        return path.resolve().relative_to(base_dir.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def resolve_snapshot_path(stored_path: str, base_dir: Path = BASE_DIR) -> Path:
    """Resolve a stored snapshot path string back to an absolute Path."""
    path = Path(stored_path)
    if path.is_absolute():
        return path
    return base_dir / path


def delete_snapshot_file(stored_path: str, base_dir: Path = BASE_DIR) -> bool:
    """Delete a snapshot JPEG file if it exists; return True if deleted."""
    target = resolve_snapshot_path(stored_path, base_dir=base_dir)
    if not target.exists() or not target.is_file():
        return False
    target.unlink()
    return True
