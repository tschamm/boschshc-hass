#!/usr/bin/env python3
"""Gate: verify every entity platform file sets PARALLEL_UPDATES.

The parallel-updates quality-scale rule requires every platform module to
declare PARALLEL_UPDATES (1, to serialize writes against the SHC controller,
or 0 with a documented reason). quality_scale.yaml's comment used to hardcode
an exact platform list/count that silently went stale when a new platform
file (update.py) was added without updating the doc (caught in the
2026-07-01 quality-scale audit) — this gate makes the check self-updating
instead of relying on a hand-maintained list.

A platform file is any module that defines `async_setup_entry` — that's the
HA entry point every platform module must have, so it's a reliable signal
independent of a maintained filename list.

Fails with exit code 1 and lists every offending file if any platform module
is missing a top-level PARALLEL_UPDATES assignment.

Usage:
    python3 scripts/check-parallel-updates.py [--dir custom_components/bosch_shc]
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

# Not entity platforms even though they live alongside them / may define
# similar-looking setup functions — excluded from this check.
NON_PLATFORM_FILES = {
    "__init__.py",
    "config_flow.py",
    "const.py",
    "data.py",
    "device_trigger.py",
    "diagnostics.py",
    "entity.py",
    "certificate.py",
}


def is_platform_module(tree: ast.Module) -> bool:
    """True if the module defines a top-level async_setup_entry function."""
    return any(
        isinstance(node, ast.AsyncFunctionDef) and node.name == "async_setup_entry"
        for node in tree.body
    )


def has_parallel_updates(tree: ast.Module) -> bool:
    """True if the module has a top-level PARALLEL_UPDATES assignment."""
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "PARALLEL_UPDATES":
                return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dir",
        default="custom_components/bosch_shc",
        help="Directory of platform .py files to scan (default: custom_components/bosch_shc)",
    )
    args = parser.parse_args()

    base = Path(args.dir)
    if not base.is_dir():
        print(f"ERROR: directory not found: {base}", file=sys.stderr)
        return 2

    missing = []
    checked = []
    for py_file in sorted(base.glob("*.py")):
        if py_file.name in NON_PLATFORM_FILES:
            continue
        tree = ast.parse(py_file.read_text(), filename=str(py_file))
        if not is_platform_module(tree):
            continue
        checked.append(py_file.name)
        if not has_parallel_updates(tree):
            missing.append(py_file.name)

    if missing:
        print("FAIL: platform module(s) missing PARALLEL_UPDATES:")
        for name in missing:
            print(f"  {name}")
        return 1

    print(f"OK: PARALLEL_UPDATES present in all {len(checked)} platform modules")
    return 0


if __name__ == "__main__":
    sys.exit(main())
