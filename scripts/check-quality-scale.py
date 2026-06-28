#!/usr/bin/env python3
"""Gate: verify quality_scale.yaml satisfies the target HA quality scale tier.

Reads quality_scale.yaml from the current working directory (or --file path).
Fails with exit code 1 if any rule for the target tier has status: todo or
is absent from the file entirely.  status: done and status: exempt both pass.

Usage:
    python3 scripts/check-quality-scale.py [--tier bronze|silver|gold] [--file PATH]

Tier is cumulative: --tier gold checks Bronze + Silver + Gold rules.

Rule sets are sourced from:
    https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML not installed.  Run: pip install pyyaml", file=sys.stderr)
    sys.exit(2)

# ---------------------------------------------------------------------------
# Authoritative rule tier tables
# ---------------------------------------------------------------------------

BRONZE: set[str] = {
    "action-setup",
    "appropriate-polling",
    "brands",
    "common-modules",
    "config-flow",
    "config-flow-test-coverage",
    "dependency-transparency",
    "docs-actions",
    "docs-high-level-description",
    "docs-installation-instructions",
    "docs-removal-instructions",
    "entity-event-setup",
    "entity-unique-id",
    "has-entity-name",
    "runtime-data",
    "test-before-configure",
    "test-before-setup",
    "unique-config-entry",
}

SILVER: set[str] = {
    "action-exceptions",
    "config-entry-unloading",
    "docs-configuration-parameters",
    "docs-installation-parameters",
    "entity-unavailable",
    "integration-owner",
    "log-when-unavailable",
    "parallel-updates",
    "reauthentication-flow",
    "test-coverage",
}

GOLD: set[str] = {
    "devices",
    "diagnostics",
    "discovery",
    "discovery-update-info",
    "docs-data-update",
    "docs-examples",
    "docs-known-limitations",
    "docs-supported-devices",
    "docs-supported-functions",
    "docs-troubleshooting",
    "docs-use-cases",
    "dynamic-devices",
    "entity-category",
    "entity-device-class",
    "entity-disabled-by-default",
    "entity-translations",
    "exception-translations",
    "icon-translations",
    "reconfiguration-flow",
    "repair-issues",
    "stale-devices",
}

PLATINUM: set[str] = {
    "async-dependency",
    "inject-websession",
    "strict-typing",
}

TIERS: dict[str, set[str]] = {
    "bronze": BRONZE,
    "silver": BRONZE | SILVER,
    "gold": BRONZE | SILVER | GOLD,
    "platinum": BRONZE | SILVER | GOLD | PLATINUM,
}

PASS_STATUSES = {"done", "exempt"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tier",
        choices=["bronze", "silver", "gold", "platinum"],
        default="silver",
        help="Quality scale tier to enforce (default: silver)",
    )
    parser.add_argument(
        "--file",
        default="quality_scale.yaml",
        help="Path to quality_scale.yaml (default: quality_scale.yaml)",
    )
    args = parser.parse_args()

    qs_path = Path(args.file)
    if not qs_path.exists():
        print(f"ERROR: {qs_path} not found (run from repo root)", file=sys.stderr)
        return 2

    with qs_path.open() as fh:
        data = yaml.safe_load(fh)

    rules: dict[str, dict] = data.get("rules", {})
    required: set[str] = TIERS[args.tier]

    todo: list[str] = []
    missing: list[str] = []

    for rule in sorted(required):
        if rule not in rules:
            missing.append(rule)
        elif rules[rule].get("status") not in PASS_STATUSES:
            todo.append(f"{rule} ({rules[rule].get('status', '?')})")

    if missing:
        print(f"MISSING from quality_scale.yaml ({len(missing)}) — add and assess:")
        for r in missing:
            print(f"  - {r}")

    if todo:
        print(f"TODO rules block {args.tier.upper()} gate ({len(todo)}):")
        for r in todo:
            print(f"  - {r}")

    if missing or todo:
        return 1

    done_count = sum(
        1 for r in required if rules.get(r, {}).get("status") == "done"
    )
    exempt_count = sum(
        1 for r in required if rules.get(r, {}).get("status") == "exempt"
    )
    print(
        f"OK: {args.tier.upper()} gate passed "
        f"({done_count} done, {exempt_count} exempt / {len(required)} rules)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
