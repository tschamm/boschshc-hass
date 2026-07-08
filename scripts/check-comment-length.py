#!/usr/bin/env python3
"""Gate: flag comment blocks that read like a design memo, not an inline note.

A standalone `#`-comment block of 3+ consecutive lines is flagged as too
long. Section-divider blocks (e.g. a row of `-`/`=` characters) are exempt.
This is a blunt heuristic — it does not distinguish "over-explained design
justification" from "genuinely dense hardware-quirk documentation". Known
existing violations are listed in scripts/comment_length_baseline.txt and
are not re-flagged; new violations must be added to that baseline or fixed.

Usage:
    python3 scripts/check-comment-length.py [--path DIR] [--max-lines N]
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys

MAX_LINES_DEFAULT = 2
DIVIDER_RE = re.compile(r"^[-=─_]{3,}$")


def _is_divider(text: str) -> bool:
    return bool(DIVIDER_RE.match(text.strip()))


def find_violations(root: Path, max_lines: int) -> list[tuple[Path, int, int]]:
    """Return (file, start_line, block_length) for each over-long comment block."""
    violations = []
    for path in sorted(root.rglob("*.py")):
        lines = path.read_text().splitlines()
        i = 0
        while i < len(lines):
            stripped = lines[i].strip()
            is_comment = (
                stripped.startswith("#")
                and not stripped.startswith("#!")
                and "type: ignore" not in stripped
                and "noqa" not in stripped
            )
            if not is_comment:
                i += 1
                continue
            start = i
            block: list[str] = []
            while i < len(lines):
                s = lines[i].strip()
                if not (
                    s.startswith("#")
                    and "type: ignore" not in s
                    and "noqa" not in s
                ):
                    break
                block.append(s.lstrip("#").strip())
                i += 1
            if len(block) > max_lines and not all(_is_divider(b) for b in block):
                violations.append((path, start + 1, len(block)))
    return violations


def load_baseline(baseline_path: Path) -> set[tuple[str, int]]:
    if not baseline_path.exists():
        return set()
    entries = set()
    for line in baseline_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        file_part, _, line_no = line.rpartition(":")
        entries.add((file_part, int(line_no)))
    return entries


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", default="custom_components/bosch_shc")
    parser.add_argument("--max-lines", type=int, default=MAX_LINES_DEFAULT)
    parser.add_argument(
        "--baseline",
        default="scripts/comment_length_baseline.txt",
        help="File listing known/accepted violations as path:line, one per line.",
    )
    args = parser.parse_args()

    root = Path(args.path)
    baseline = load_baseline(Path(args.baseline))
    violations = find_violations(root, args.max_lines)
    new_violations = [
        (f, ln, n) for f, ln, n in violations if (str(f), ln) not in baseline
    ]

    if not new_violations:
        print(f"OK: no comment blocks longer than {args.max_lines} lines "
              f"({len(violations)} known/baselined).")
        return 0

    print(f"FAIL: {len(new_violations)} comment block(s) exceed "
          f"{args.max_lines} lines and are not in the baseline:\n")
    for file, line, length in new_violations:
        print(f"  {file}:{line} ({length} lines)")
    print(
        "\nCondense to state only the non-obvious constraint, or — if this "
        "genuinely needs the detail (e.g. real hardware-quirk documentation "
        "with an issue reference) — add it to "
        f"{args.baseline} as '{new_violations[0][0]}:{new_violations[0][1]}'."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
