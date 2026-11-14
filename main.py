"""Application entry point."""

from __future__ import annotations

import logging
import sys

from config.logging_config import setup_logging
from config.settings import get_settings


def main() -> int:
    """Load configuration, initialize logging, print startup banner, and exit cleanly."""
    settings = get_settings()
    setup_logging(settings)
    logger = logging.getLogger(__name__)

    banner = (
        "========================================\n"
        f"  {settings.app_name} ({settings.app_env})\n"
        f"  Log file: {settings.log_path}\n"
        "========================================"
    )
    print(banner)
    logger.info("Application initialized cleanly (%s).", settings.app_name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
