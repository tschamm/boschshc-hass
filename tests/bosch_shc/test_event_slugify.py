"""Tests for event.py entity_id slug validity after slugify migration."""

import re
import pytest
from homeassistant.util import slugify


VALID_OBJECT_ID_RE = re.compile(r"^[a-z0-9_]+$")


def _is_valid_object_id(slug: str) -> bool:
    """Return True if slug is a valid HA object_id (all lower, ascii, underscores)."""
    return bool(VALID_OBJECT_ID_RE.match(slug))


@pytest.mark.parametrize(
    "name",
    [
        "Haus verlassen",
        "Lichtschalter WZ 2",
        "Außentür",
        "Guten Morgen",
        "Überwachung EIN",
        "Öffner Küche",
        "UPPER CASE NAME",
        "mixed_CASE_with_underscores",
        "Türsensor Flur",
        "Rauchmelder Büro",
        "Fenster öffnen",
    ],
)
def test_slugify_produces_valid_object_id(name: str) -> None:
    """slugify(name) must produce a valid HA object_id for all Bosch device/scenario names."""
    slug = slugify(name)
    assert slug, f"slugify({name!r}) returned empty string"
    assert _is_valid_object_id(slug), (
        f"slugify({name!r}) = {slug!r} is not a valid HA object_id "
        f"(must match ^[a-z0-9_]+$)"
    )


def test_universal_switch_entity_id_slug() -> None:
    """Simulate the UniversalSwitchEvent entity_id slug construction."""
    device_name = "Lichtschalter WZ 2"
    key_id = "UPPER1"
    slug = f"{slugify(device_name)}_button_{key_id.casefold()}"
    assert _is_valid_object_id(slug), (
        f"UniversalSwitchEvent slug {slug!r} is not a valid HA object_id"
    )


def test_scenario_entity_id_slug() -> None:
    """Simulate the SHCScenarioEvent entity_id slug construction."""
    scenario_name = "Haus verlassen"
    slug = f"scenario_{slugify(scenario_name)}"
    assert _is_valid_object_id(slug), (
        f"SHCScenarioEvent slug {slug!r} is not a valid HA object_id"
    )


def test_umlaut_transliteration() -> None:
    """Umlauts (ä/ö/ü/ß/Ä/Ö/Ü) must be transliterated, not dropped."""
    assert slugify("Außentür") != ""
    assert slugify("Überwachung") != ""
    # Result must be valid object_id
    assert _is_valid_object_id(slugify("Außentür"))
    assert _is_valid_object_id(slugify("Überwachung"))
    assert _is_valid_object_id(slugify("Öffner Küche"))
