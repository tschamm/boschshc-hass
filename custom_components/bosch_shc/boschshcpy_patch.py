"""Temporary shim: attach supports_eco to boschshcpy until the library releases it.

Remove this file and the two import/apply lines in climate.py once the
boschshcpy release that adds supports_eco is set as the minimum requirement in
manifest.json.
"""
from __future__ import annotations


def apply() -> None:
    """Monkey-patch supports_eco onto SHCClimateControl if not already present."""
    try:
        from boschshcpy import SHCClimateControl
    except ImportError:
        return

    if not hasattr(SHCClimateControl, "supports_eco"):
        SHCClimateControl.supports_eco = property(lambda self: True)
