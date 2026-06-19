"""Tests for switch.py user-defined-state entity_id slug generation.

Verifies that homeassistant.util.slugify produces a valid HA entity_id slug
for device names containing spaces, umlauts, and uppercase letters.
Does NOT require HA runtime — imports slugify directly.
"""
import re

import pytest

from homeassistant.util import slugify

VALID_SLUG_RE = re.compile(r"^[a-z0-9_]+$")


def _make_slug(name: str) -> str:
    """Replicate the entity_id object-id production used in SHCUserDefinedStateSwitch."""
    return f"userdefinedstate_{slugify(name)}"


@pytest.mark.parametrize(
    "name",
    [
        "My State",
        "Schlafzimmer",
        "Küche Licht",
        "Büro",
        "Außenbereich",
        "State With UPPERCASE",
        "Gäste WC",
        "123 Numbers OK",
        "mixed CASE with Ümlauts",
        "state_already_slug",
    ],
)
def test_userdefined_state_slug_is_valid(name: str) -> None:
    """entity_id slug must match ^[a-z0-9_]+$ for any device name."""
    slug = _make_slug(name)
    # The full entity_id would be "switch.<slug>"; test the object-id portion.
    object_id = slug  # already prefixed with "userdefinedstate_"
    assert VALID_SLUG_RE.match(object_id), (
        f"Slug {object_id!r} (from name {name!r}) contains invalid characters"
    )


def test_umlaut_names_do_not_produce_empty_slug() -> None:
    """Umlaut-only names must not yield an empty or underscore-only slug."""
    slug = slugify("Ää Öö Üü")
    assert slug, "slugify must not return an empty string for umlaut names"
    assert VALID_SLUG_RE.match(slug), f"Slug {slug!r} contains invalid characters"
