#!/usr/bin/env python3
"""Gate: catch entities that hardcode _attr_icon alongside a translation key.

HA's icon-translations quality-scale rule requires icons for translation-keyed
entities to live in icons.json, not hardcoded in Python — a class-level
_attr_icon takes priority over icons.json's default icon lookup, so setting
both silently defeats the point of the rule (found in 18 classes across 4
files during the 2026-07-01 quality-scale audit; see quality_scale.yaml's
icon-translations entry).

Fails with exit code 1 and lists every offending class if any entity class
defines BOTH `_attr_icon` and `_attr_translation_key` as class attributes.

Usage:
    python3 scripts/check-icon-translations.py [--dir custom_components/bosch_shc]
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path


def find_conflicts(path: Path) -> list[str]:
    """Return class names in `path` that set both _attr_icon and _attr_translation_key."""
    tree = ast.parse(path.read_text(), filename=str(path))
    conflicts = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        has_icon = False
        has_key = False
        for stmt in node.body:
            if not isinstance(stmt, ast.Assign):
                continue
            for target in stmt.targets:
                if not isinstance(target, ast.Name):
                    continue
                if target.id == "_attr_icon":
                    has_icon = True
                elif target.id == "_attr_translation_key":
                    has_key = True
        if has_icon and has_key:
            conflicts.append(node.name)
    return conflicts


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

    findings: dict[str, list[str]] = {}
    for py_file in sorted(base.glob("*.py")):
        conflicts = find_conflicts(py_file)
        if conflicts:
            findings[str(py_file)] = conflicts

    if findings:
        print("FAIL: entities set BOTH _attr_icon and _attr_translation_key")
        print(
            "(the hardcoded _attr_icon wins over icons.json, defeating icon "
            "translations — move the icon into icons.json instead, keyed by "
            "the translation_key, and remove _attr_icon):"
        )
        for path, classes in findings.items():
            for cls in classes:
                print(f"  {path}: {cls}")
        return 1

    print("OK: no entity class hardcodes _attr_icon alongside _attr_translation_key")
    return 0


if __name__ == "__main__":
    sys.exit(main())
