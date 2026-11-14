"""Helper launcher for go2rtc that injects URL-encoded .env credentials safely."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "go2rtc.yaml"


def _find_go2rtc_binary(explicit_path: str | None) -> str | None:
    if explicit_path:
        candidate = Path(explicit_path)
        return str(candidate) if candidate.is_file() else None

    for name in ("go2rtc.exe", "go2rtc"):
        local_bin = PROJECT_ROOT / "tools" / "bin" / name
        if local_bin.is_file():
            return str(local_bin)
        found = shutil.which(name)
        if found:
            return found
    return None


def build_go2rtc_env(base_env: dict[str, str] | None = None) -> tuple[dict[str, str], list[str]]:
    """Return an environment mapping for go2rtc and any missing required keys."""
    env = dict(base_env if base_env is not None else os.environ)
    env.setdefault("CAMERA_PORT", "554")
    env.setdefault("CAMERA_SUB_STREAM", "stream2")
    if not env.get("GO2RTC_WEBRTC_LISTEN"):
        env["GO2RTC_WEBRTC_LISTEN"] = "127.0.0.1:8555"
    if not env.get("GO2RTC_WEBRTC_CANDIDATE"):
        env["GO2RTC_WEBRTC_CANDIDATE"] = "stun:8555"

    required = ("CAMERA_IP", "CAMERA_USER", "CAMERA_PASS")
    missing = [key for key in required if not env.get(key)]
    if not missing:
        env["GO2RTC_CAMERA_USER"] = quote(env["CAMERA_USER"], safe="")
        env["GO2RTC_CAMERA_PASS"] = quote(env["CAMERA_PASS"], safe="")
    return env, missing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Launch go2rtc locally using camera settings from .env."
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG),
        help="Path to go2rtc YAML config (default: config/go2rtc.yaml)",
    )
    parser.add_argument(
        "--binary",
        default=None,
        help="Optional explicit path to go2rtc executable",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Validate configuration and binary presence without starting go2rtc",
    )
    args = parser.parse_args(argv)

    load_dotenv(PROJECT_ROOT / ".env")

    env, missing = build_go2rtc_env()
    if missing:
        print(
            f"ERROR: Missing required .env variable(s): {', '.join(missing)}",
            file=sys.stderr,
        )
        return 1

    config_path = Path(args.config)
    if not config_path.is_file():
        print(f"ERROR: go2rtc config not found: {config_path}", file=sys.stderr)
        return 1

    binary = _find_go2rtc_binary(args.binary)
    if not binary:
        print(
            "ERROR: go2rtc binary not found. Place go2rtc.exe in tools/bin/ "
            "or add go2rtc to PATH.",
            file=sys.stderr,
        )
        return 1

    if args.check_only:
        print(f"OK: go2rtc binary={binary}, config={config_path}")
        return 0

    print(
        f"Starting go2rtc (local API 127.0.0.1:1984, RTSP listener disabled) "
        f"using {config_path}..."
    )
    proc = subprocess.run([binary, "-config", str(config_path)], env=env, check=False)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
