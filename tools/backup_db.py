"""Hot WAL-safe SQLite database backup to an external drive with integrity check & pruning."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def backup_sqlite_database(
    source_db: Path | str,
    dest_dir: Path | str,
    *,
    keep_count: int = 8,
    dry_run: bool = False,
    timestamp: Optional[datetime] = None,
) -> Path:
    """Create an online WAL-consistent SQLite backup, verify integrity, and prune old copies."""
    src = Path(source_db)
    dst_root = Path(dest_dir)
    if not src.is_file():
        raise FileNotFoundError(f"Source database does not exist: {src}")

    dt = timestamp.astimezone(timezone.utc) if timestamp else datetime.now(timezone.utc)
    filename = f"{src.stem}_backup_{dt.strftime('%Y%m%d_%H%M%S')}{src.suffix or '.db'}"
    target = dst_root / filename

    if dry_run:
        return target

    dst_root.mkdir(parents=True, exist_ok=True)
    tmp_target = dst_root / f".{filename}.tmp"
    if tmp_target.exists():
        tmp_target.unlink()

    src_conn = sqlite3.connect(f"file:{src.resolve()}?mode=ro", uri=True, timeout=30.0)
    dst_conn = sqlite3.connect(str(tmp_target), timeout=30.0)
    try:
        src_conn.backup(dst_conn)
        dst_conn.commit()
        row = dst_conn.execute("PRAGMA integrity_check").fetchone()
        if not row or str(row[0]).lower() != "ok":
            raise RuntimeError(f"Backup integrity_check failed: {row}")
    finally:
        dst_conn.close()
        src_conn.close()

    tmp_target.replace(target)

    # Prune oldest backups beyond keep_count
    pattern = f"{src.stem}_backup_*{src.suffix or '.db'}"
    backups = sorted(dst_root.glob(pattern), key=lambda p: p.name, reverse=True)
    for stale in backups[max(1, int(keep_count)) :]:
        try:
            stale.unlink()
        except OSError:
            pass

    return target


def main(argv: list[str] | None = None) -> int:
    from config.settings import get_settings

    cfg = get_settings()
    parser = argparse.ArgumentParser(
        description="Perform a hot SQLite WAL backup to an external drive."
    )
    parser.add_argument(
        "--source",
        default=str(cfg.db_path),
        help="Path to SQLite database (default: DB_PATH from .env)",
    )
    parser.add_argument(
        "--dest",
        default=str(cfg.backup_dir),
        help="Destination folder or external drive path (default: BACKUP_DIR from .env)",
    )
    parser.add_argument(
        "--keep",
        type=int,
        default=cfg.backup_keep_count,
        help="Number of recent backups to retain (default: BACKUP_KEEP_COUNT)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned backup path without modifying files",
    )
    args = parser.parse_args(argv)

    result = backup_sqlite_database(
        args.source,
        args.dest,
        keep_count=args.keep,
        dry_run=args.dry_run,
    )
    prefix = "DRY-RUN" if args.dry_run else "OK"
    print(f"{prefix}: Backup target={result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
