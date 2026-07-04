#!/usr/bin/env python3
"""Gate: verify every translation file is complete against en.json.

Three checks, all hard-fail (exit 1):
  1. Full-parity: every translations/<lang>.json must have the EXACT same
     recursive key set as en.json — no missing keys (untranslated fall-throughs)
     and no stale/extra keys (renamed-away entities left behind).
  2. Source sync: en.json entity section must stay in sync with strings.json.
  3. Placeholder parity: every leaf string's {placeholder} set must match
     en.json's for that same key — a translation adding/dropping a
     {variable} passes key-parity but fails at HA runtime with a
     "Validation of translation placeholders" ERROR (caught live, not by
     this gate, once: en.json gained {title} in issues.cert_expiring.description
     and all 29 other languages silently kept the old placeholder set).

en.json itself is the reference and is skipped by the per-language check.

Usage:
    python3 scripts/check-translations.py [--base custom_components/bosch_shc]
"""
import argparse
import json
import os
import re
import sys

PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


def flat_keys(d, prefix=""):
    """Recursive set of dotted key-paths for every node (leaf and branch)."""
    keys = set()
    if isinstance(d, dict):
        for k, v in d.items():
            p = f"{prefix}.{k}" if prefix else k
            keys.add(p)
            keys |= flat_keys(v, p)
    return keys


def flat_strings(d, prefix=""):
    """Recursive dotted-key-path -> leaf string value, for str leaves only."""
    out = {}
    if isinstance(d, dict):
        for k, v in d.items():
            p = f"{prefix}.{k}" if prefix else k
            if isinstance(v, str):
                out[p] = v
            else:
                out.update(flat_strings(v, p))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="custom_components/bosch_shc")
    args = ap.parse_args()
    base = args.base
    tdir = f"{base}/translations"

    errors = []

    with open(f"{tdir}/en.json", encoding="utf-8") as f:
        en = json.load(f)
    en_keys = flat_keys(en)
    en_strings = flat_strings(en)

    files = [f for f in sorted(os.listdir(tdir)) if f.endswith(".json")]
    for fname in files:
        if fname == "en.json":
            continue
        with open(f"{tdir}/{fname}", encoding="utf-8") as f:
            d = json.load(f)
        keys = flat_keys(d)
        missing = en_keys - keys
        extra = keys - en_keys
        if missing:
            errors.append(f"  {fname}: {len(missing)} MISSING keys "
                          f"(would fall back to English): {sorted(missing)}")
        if extra:
            errors.append(f"  {fname}: {len(extra)} STALE/EXTRA keys "
                          f"(not in en.json): {sorted(extra)}")

        strings = flat_strings(d)
        for key, en_value in en_strings.items():
            if key not in strings:
                continue  # already reported above as a missing key
            en_placeholders = set(PLACEHOLDER_RE.findall(en_value))
            lang_placeholders = set(PLACEHOLDER_RE.findall(strings[key]))
            if en_placeholders != lang_placeholders:
                lost = en_placeholders - lang_placeholders
                added = lang_placeholders - en_placeholders
                detail = []
                if lost:
                    detail.append(f"missing {sorted(lost)}")
                if added:
                    detail.append(f"extra {sorted(added)}")
                errors.append(
                    f"  {fname}: {key} placeholder mismatch ({', '.join(detail)})"
                )

    # en.json entity section must match strings.json entity section.
    with open(f"{base}/strings.json", encoding="utf-8") as f:
        strings = json.load(f)
    s_ent = flat_keys(strings.get("entity", {}))
    en_ent = flat_keys(en.get("entity", {}))
    if s_ent - en_ent:
        errors.append(f"  en.json: entity keys in strings.json but missing: "
                      f"{sorted(s_ent - en_ent)}")
    if en_ent - s_ent:
        errors.append(f"  en.json: extra entity keys not in strings.json: "
                      f"{sorted(en_ent - s_ent)}")

    if errors:
        print("Translation completeness FAILED:")
        for e in errors:
            print(e)
        return 1
    print(f"All {len(files) - 1} translation files match en.json "
          f"({len(en_keys)} keys); en.json entity keys in sync with strings.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
