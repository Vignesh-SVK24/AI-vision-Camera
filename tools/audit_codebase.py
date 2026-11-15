"""Comprehensive static analysis audit tool for Python backend and React frontend."""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

EXCLUDE_DIRS = {"venv", ".venv", "env", "node_modules", ".git", "__pycache__", "dist", ".vite", ".pytest_cache"}

def find_py_files() -> list[Path]:
    py_files = []
    for root, dirs, files in os.walk(PROJECT_ROOT):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for f in files:
            if f.endswith(".py"):
                py_files.append(Path(root) / f)
    return py_files

def find_frontend_files() -> list[Path]:
    frontend_dir = PROJECT_ROOT / "dashboard" / "src"
    if not frontend_dir.exists():
        return []
    ts_files = []
    for root, dirs, files in os.walk(frontend_dir):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for f in files:
            if f.endswith((".ts", ".tsx", ".css")):
                ts_files.append(Path(root) / f)
    return ts_files

def audit_python_files(py_files: list[Path]):
    results = {
        "unused_imports": [],
        "debug_prints": [],
        "todos": [],
        "commented_code": [],
        "unreachable_code": [],
    }

    for path in py_files:
        rel = path.relative_to(PROJECT_ROOT)
        try:
            content = path.read_text(encoding="utf-8")
        except Exception as e:
            continue

        lines = content.splitlines()

        # Check TODOs and debug prints via regex / lines
        for idx, line in enumerate(lines, start=1):
            stripped = line.strip()
            # Stale TODOs
            if re.search(r"#\s*(TODO|FIXME|HACK|XXX)\b", stripped, re.IGNORECASE):
                results["todos"].append(f"{rel}:{idx} - {stripped}")
            # Debug prints (excluding tools where printing to console is intended CLI behavior)
            if re.match(r"^print\s*\(", stripped) and not str(rel).startswith("tools"):
                results["debug_prints"].append(f"{rel}:{idx} - {stripped}")
            # Commented-out code heuristic
            if stripped.startswith("#") and not stripped.startswith("#!") and not stripped.startswith("# noqa"):
                comment_body = stripped.lstrip("#").strip()
                if re.match(r"^(import |from |def |class |return |if |for |while |self\.|app\.|\w+\s*=)", comment_body):
                    if not comment_body.startswith("For ") and not comment_body.startswith("See ") and not comment_body.startswith("TODO"):
                        results["commented_code"].append(f"{rel}:{idx} - {stripped}")

        # AST analysis
        try:
            tree = ast.parse(content, filename=str(rel))
        except SyntaxError as e:
            continue

        # Check unused imports
        imported_names = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.asname or alias.name
                    imported_names[name] = (alias.lineno, alias.name)
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    name = alias.asname or alias.name
                    imported_names[name] = (alias.lineno, f"{node.module}.{alias.name}")

        # Collect all Name and Attribute uses
        used_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Store):
                used_names.add(node.id)
            elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                used_names.add(node.value.id)

        # Check if imported names are used
        for imp_name, (lineno, full_path) in imported_names.items():
            if imp_name == "__future__" or full_path.startswith("__future__"):
                continue
            if imp_name not in used_names:
                # Exclude __init__.py re-exports and special cases
                if path.name == "__init__.py":
                    continue
                results["unused_imports"].append(f"{rel}:{lineno} - {imp_name} ({full_path})")

    return results

def audit_frontend(ts_files: list[Path]):
    results = {
        "files": [],
        "unreferenced_files": [],
        "filler_text": [],
    }

    all_contents = {}
    for f in ts_files:
        try:
            all_contents[f] = f.read_text(encoding="utf-8")
        except Exception:
            pass

    for path in ts_files:
        rel = path.relative_to(PROJECT_ROOT)
        stem = path.stem
        # Check if referenced anywhere else
        if stem in ("main", "App", "vite-env.d", "index"):
            continue
        referenced = False
        for other_path, content in all_contents.items():
            if other_path == path:
                continue
            if stem in content:
                referenced = True
                break
        if not referenced:
            results["unreferenced_files"].append(str(rel))

        # Check for placeholder / lorem ipsum / filler text
        content = all_contents.get(path, "")
        for idx, line in enumerate(content.splitlines(), start=1):
            if re.search(r"lorem ipsum|placeholder|todo|demo only", line, re.IGNORECASE):
                results["filler_text"].append(f"{rel}:{idx} - {line.strip()[:80]}")

    return results

def main():
    py_files = find_py_files()
    ts_files = find_frontend_files()

    py_results = audit_python_files(py_files)
    fe_results = audit_frontend(ts_files)

    print("=" * 60)
    print("PYTHON AUDIT RESULTS")
    print("=" * 60)
    print(f"Total Python files: {len(py_files)}")
    print(f"Unused imports found: {len(py_results['unused_imports'])}")
    for item in py_results["unused_imports"]:
        print("  [UNUSED IMPORT]", item)

    print(f"\nDebug prints in core code: {len(py_results['debug_prints'])}")
    for item in py_results["debug_prints"]:
        print("  [DEBUG PRINT]", item)

    print(f"\nStale TODOs: {len(py_results['todos'])}")
    for item in py_results["todos"]:
        print("  [TODO]", item)

    print(f"\nCommented-out code lines: {len(py_results['commented_code'])}")
    for item in py_results["commented_code"][:20]:
        print("  [COMMENTED CODE]", item)

    print("\n" + "=" * 60)
    print("FRONTEND AUDIT RESULTS")
    print("=" * 60)
    print(f"Total frontend source files: {len(ts_files)}")
    print(f"Unreferenced files: {len(fe_results['unreferenced_files'])}")
    for item in fe_results["unreferenced_files"]:
        print("  [UNREFERENCED]", item)

    print(f"\nFiller / Demo / Placeholder text: {len(fe_results['filler_text'])}")
    for item in fe_results["filler_text"]:
        print("  [FILLER TEXT]", item)

if __name__ == "__main__":
    main()
