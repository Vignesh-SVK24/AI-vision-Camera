"""Repository secret scanner and .gitignore verification tool."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

SKIP_DIRS = frozenset(
    {
        ".git",
        "venv",
        ".venv",
        "env",
        "node_modules",
        "dist",
        "__pycache__",
        ".pytest_cache",
        ".vite",
        "logs",
        "snapshots",
        "faces_data",
        "models",
    }
)

BINARY_EXTENSIONS = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".ico",
        ".db",
        ".sqlite",
        ".sqlite3",
        ".onnx",
        ".pt",
        ".pth",
        ".exe",
        ".dll",
        ".pyd",
        ".zip",
        ".mp4",
        ".avi",
    }
)

SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "Private key block",
        re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----"),
    ),
    (
        "Telegram bot token",
        re.compile(r"\b\d{8,11}:[A-Za-z0-9_-]{35}\b"),
    ),
    (
        "RTSP URL with literal credentials",
        re.compile(
            r"rtsp://(?!\$\{)(?!(?:user|u|admin):(?:password|pass|secret|p|my_pass)@)[^/\s:@]+:[^/\s@]+@"
        ),
    ),
    (
        "AWS access key ID",
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    ),
    (
        "GitHub personal access token",
        re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36}\b"),
    ),
    (
        "Non-empty secret assignment in env/config",
        re.compile(
            r"^[ \t]*(?:JWT_SECRET_KEY|TELEGRAM_BOT_TOKEN|CAMERA_PASS|SUPABASE_KEY)[ \t]*=[ \t]*(?![ \t]*(?:\r?\n|$)|change_me\b|<[^>]+>)[^\r\n\s]+",
            re.MULTILINE,
        ),
    ),
)


@dataclass(frozen=True)
class SecretFinding:
    path: str
    line_no: int
    rule: str


def check_gitignore(project_root: Path = PROJECT_ROOT) -> list[str]:
    """Return missing required ignore patterns in .gitignore."""
    gitignore_path = project_root / ".gitignore"
    if not gitignore_path.is_file():
        return [".gitignore file is missing"]
    lines = {
        line.strip()
        for line in gitignore_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    required = (".env", ".env.*", "*.db", "snapshots/", "faces_data/", "models/", "venv/")
    return [pattern for pattern in required if pattern not in lines]


def scan_repository(
    project_root: Path = PROJECT_ROOT, *, include_tests: bool = False
) -> list[SecretFinding]:
    """Scan workspace text files for accidentally hardcoded secrets."""
    findings: list[SecretFinding] = []
    for path in sorted(project_root.rglob("*")):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(project_root).parts
        if any(part in SKIP_DIRS for part in rel_parts):
            continue
        if not include_tests and rel_parts and rel_parts[0] == "tests":
            continue
        if path.name == ".env" or path.name.startswith(".env.") and path.name != ".env.example":
            continue
        if path.suffix.lower() in BINARY_EXTENSIONS:
            continue
        # Do not flag regex definitions inside this scanner or its unit tests
        rel_str = path.relative_to(project_root).as_posix()
        if rel_str in ("tools/secret_scan.py", "tests/test_security_hardening.py"):
            continue

        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue

        for rule_name, pattern in SECRET_PATTERNS:
            for match in pattern.finditer(content):
                line_no = content.count("\n", 0, match.start()) + 1
                findings.append(SecretFinding(path=rel_str, line_no=line_no, rule=rule_name))

    return findings


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scan workspace for hardcoded secrets and verify .gitignore rules."
    )
    parser.add_argument(
        "--root",
        default=str(PROJECT_ROOT),
        help="Root directory to scan (default: project root)",
    )
    parser.add_argument(
        "--include-tests",
        action="store_true",
        help="Also scan unit-test fixture files under tests/",
    )
    args = parser.parse_args()
    root = Path(args.root).resolve()

    missing_ignores = check_gitignore(root)
    findings = scan_repository(root, include_tests=args.include_tests)

    if missing_ignores:
        print(f"FAIL: .gitignore missing entries: {', '.join(missing_ignores)}", file=sys.stderr)
    else:
        print("OK: .gitignore ignores .env, databases, snapshots, faces_data, models, and venv.")

    if findings:
        for item in findings:
            print(f"SECRET FINDING: {item.path}:{item.line_no} [{item.rule}]", file=sys.stderr)
        return 1

    print("OK: Zero committed/hardcoded secrets detected.")
    return 1 if missing_ignores else 0


if __name__ == "__main__":
    raise SystemExit(main())
