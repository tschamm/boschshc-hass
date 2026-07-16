"""Shared fixtures for tests/bosch_shc.

Provides an autouse patch for async_get_clientsession so that all tests that
call async_setup_entry (which now calls async_get_clientsession as part of
Phase 3c inject-websession) work without a real HA HTTP stack.

Also provides the shared platform-test fixtures (mock_config_entry,
device_buckets, mock_session) used by every platform's async_setup_entry
test -- these replace what used to be 2-5 bespoke, divergent
_make_entry()/_make_session()-style helpers duplicated per test file.
Pure-unit-style __new__-bypass entity tests (the majority of this suite's
volume) don't go through async_setup_entry at all and are unaffected by
these fixtures.
"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# Every device_helper bucket any bosch_shc platform reads (boschshcpy's
# SHCDeviceHelper, ground truth: boschshcpy/device_helper.py). All but the
# two SUPPORTED_MODELS-gated singletons default to an empty list; those two
# default to None (device_helper itself never returns a bare object without
# checking presence first).
_EMPTY_DEVICE_BUCKETS: dict[str, Any] = {
    bucket: []
    for bucket in (
        "shutter_contacts",
        "shutter_contacts2",
        "shutter_controls",
        "micromodule_shutter_controls",
        "micromodule_blinds",
        "micromodule_relays",
        "micromodule_impulse_relays",
        "light_switches_bsm",
        "micromodule_light_attached",
        "micromodule_light_controls",
        "smart_plugs",
        "smart_plugs_compact",
        "smoke_detectors",
        "climate_controls",
        "thermostats",
        "wallthermostats",
        "roomthermostats",
        "motion_detectors",
        "motion_detectors2",
        "twinguards",
        "universal_switches",
        "camera_eyes",
        "camera_360",
        "camera_outdoor_gen2",
        "ledvance_lights",
        "hue_lights",
        "water_leakage_detectors",
        "heating_circuits",
        "micromodule_dimmers",
        "outdoor_sirens",
    )
} | {
    "presence_simulation_system": None,
    "smoke_detection_system": None,
}


@pytest.fixture
def mock_config_entry(request: pytest.FixtureRequest) -> SimpleNamespace:
    """Fake bosch_shc config entry (SimpleNamespace, no real hass involved).

    Override ``options``/``entry_id`` via
    ``@pytest.mark.parametrize("mock_config_entry", [{"options": {...}}], indirect=True)``.
    """
    overrides: dict[str, Any] = getattr(request, "param", {})
    entry = SimpleNamespace(
        options=overrides.get("options", {}),
        entry_id=overrides.get("entry_id", "E1"),
    )
    entry.runtime_data = SimpleNamespace(session=None)
    return entry


@pytest.fixture
def device_buckets(request: pytest.FixtureRequest) -> dict[str, Any]:
    """device_helper buckets for the mock session.

    Empty (or None, for the two singleton buckets) by default; a test
    overrides specific buckets via
    ``@pytest.mark.parametrize("device_buckets", [{...}], indirect=True)``.
    """
    overrides: dict[str, Any] = getattr(request, "param", {})
    return {**_EMPTY_DEVICE_BUCKETS, **overrides}


@pytest.fixture
def mock_session(device_buckets: dict[str, Any]) -> SimpleNamespace:
    """Fake SHCSession exposing device_helper buckets and basic information."""
    session = SimpleNamespace()
    session.device_helper = SimpleNamespace(**device_buckets)
    session.information = SimpleNamespace(
        unique_id="test-mac",
        updateState=SimpleNamespace(name="UP_TO_DATE"),
        version="2.0",
    )
    # Sane defaults for optional/always-on session features -- individual
    # tests override these when they actually exercise the feature.
    session.intrusion_system = None
    session.water_alarm_system = None
    session.automation_rules = []
    return session


async def run_setup_entry(
    async_setup_entry: Any,
    mock_config_entry: SimpleNamespace,
    mock_session: SimpleNamespace,
) -> list[Any]:
    """Run a platform's async_setup_entry(hass, entry, async_add_entities) and
    return the collected entities.

    Replaces each platform test file's own bespoke "_run_setup"/"_run" helper
    (same shape everywhere: wire session onto the entry, call the platform's
    async_setup_entry with a fake hass and a collecting async_add_entities).
    """
    mock_config_entry.runtime_data.session = mock_session
    hass = SimpleNamespace()
    collected: list[Any] = []

    def add(entities: list[Any], update_before_add: bool = False) -> None:
        collected.extend(entities)

    await async_setup_entry(hass, mock_config_entry, add)
    return collected


@pytest.fixture(autouse=True)
def mock_async_get_clientsession():
    """Patch async_get_clientsession for all tests in this package.

    async_setup_entry calls async_get_clientsession(hass, verify_ssl=False) to
    obtain HA's managed aiohttp ClientSession (Phase 3c inject-websession).
    The tests use a MagicMock hass that does not have the real HA network
    internals, so without this patch the call raises KeyError: 'network'.

    Two patch targets are required because:
    - Some test files import via ``from custom_components.bosch_shc import ...``
      → async_setup_entry.__globals__ points at the bosch_shc package module dict
    - test_init_setup.py imports via ``from custom_components.bosch_shc.__init__ import ...``
      → async_setup_entry.__globals__ points at the bosch_shc.__init__ module dict
      (these are different Python module objects with separate __dict__s)
    Patching both ensures coverage regardless of import style.
    """
    mock_session = MagicMock()
    with (
        patch(
            "custom_components.bosch_shc.async_get_clientsession",
            return_value=mock_session,
        ),
        patch(
            "custom_components.bosch_shc.__init__.async_get_clientsession",
            return_value=mock_session,
        ),
    ):
        yield
