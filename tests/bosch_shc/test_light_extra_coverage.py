"""Extra coverage for light.py.

Targets:
- line 31: device_excluded in ledvance/dimmer/hue loop
- lines 41-46: device_excluded + async_migrate_to_new_unique_id + MotionDetectorLight creation
- lines 165-167: MotionDetectorLight.brightness when multi_level_switch is None
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from custom_components.bosch_shc.const import (
    DATA_SESSION,
    DOMAIN,
    OPT_EXCLUDED_DEVICES,
)
from custom_components.bosch_shc.light import (
    MotionDetectorLight,
    async_setup_entry,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_light_device(device_id="light-1", room_id="room-1"):
    """Minimal device double for a ledvance/dimmer/hue light."""
    return SimpleNamespace(
        id=device_id,
        root_device_id="shc-root",
        room_id=room_id,
        name="Test Light",
        serial=f"serial-{device_id}",
        manufacturer="Bosch",
        device_model="LightModel",
        status="AVAILABLE",
        deleted=False,
        device_services=[],
        subscribe_callback=lambda eid, cb: None,
        unsubscribe_callback=lambda eid: None,
        supports_color_hsb=False,
        supports_color_temp=False,
        supports_brightness=False,
        min_color_temperature=None,
        max_color_temperature=None,
        binarystate=True,
        brightness=None,
    )


def _make_motion_detector2(device_id="md2-1", room_id="room-2"):
    """Minimal device double for a motion detector II."""
    return SimpleNamespace(
        id=device_id,
        root_device_id="shc-root",
        room_id=room_id,
        name="Motion Detector II",
        serial=f"serial-{device_id}",
        manufacturer="Bosch",
        device_model="MD2Model",
        status="AVAILABLE",
        deleted=False,
        device_services=[],
        subscribe_callback=lambda eid, cb: None,
        unsubscribe_callback=lambda eid: None,
        binaryswitch=False,
        multi_level_switch=None,
    )


def _make_hass_and_entry(
    ledvance=None,
    motion2=None,
    excluded_device_ids=None,
):
    """Return (hass, config_entry) with faked session and options."""
    ledvance = ledvance or []
    motion2 = motion2 or []
    excluded = excluded_device_ids or []

    session = SimpleNamespace(
        device_helper=SimpleNamespace(
            ledvance_lights=ledvance,
            micromodule_dimmers=[],
            hue_lights=[],
            motion_detectors2=motion2,
        ),
    )

    entry_id = "entry-test"
    options = {OPT_EXCLUDED_DEVICES: excluded}

    hass = SimpleNamespace(
        data={DOMAIN: {entry_id: {DATA_SESSION: session}}},
    )
    config_entry = SimpleNamespace(
        entry_id=entry_id,
        options=options,
    )
    return hass, config_entry


def _run_setup(hass, config_entry):
    """Run async_setup_entry synchronously, return list passed to async_add_entities."""
    added = []

    def _sync_add(entities):
        added.extend(entities)

    with patch(
        "custom_components.bosch_shc.light.async_migrate_to_new_unique_id",
        new=AsyncMock(return_value=None),
    ):
        asyncio.run(async_setup_entry(hass, config_entry, _sync_add))

    return added


# ---------------------------------------------------------------------------
# Tests for line 31: device_excluded in first for-loop (ledvance/dimmer/hue)
# ---------------------------------------------------------------------------


class TestLightSetupExcluded:
    def test_excluded_ledvance_light_not_added(self):
        """Excluded ledvance light (line 31) must not appear in entities."""
        dev = _make_light_device(device_id="excl-light")
        hass, entry = _make_hass_and_entry(
            ledvance=[dev],
            excluded_device_ids=["excl-light"],
        )
        added = _run_setup(hass, entry)
        assert all(
            getattr(e, "_device", None) is not dev for e in added
        ), "Excluded ledvance light should not be added"

    def test_non_excluded_ledvance_light_is_added(self):
        """Non-excluded ledvance light must appear in entities."""
        dev = _make_light_device(device_id="keep-light")
        hass, entry = _make_hass_and_entry(
            ledvance=[dev],
            excluded_device_ids=[],
        )
        added = _run_setup(hass, entry)
        assert any(
            getattr(e, "_device", None) is dev for e in added
        ), "Non-excluded ledvance light should be added"

    def test_mixed_lights_only_excluded_is_skipped(self):
        """When one of two lights is excluded, only the non-excluded one is added."""
        keep = _make_light_device(device_id="keep")
        excl = _make_light_device(device_id="excl")
        hass, entry = _make_hass_and_entry(
            ledvance=[keep, excl],
            excluded_device_ids=["excl"],
        )
        added = _run_setup(hass, entry)
        device_ids = [getattr(e, "_device", SimpleNamespace()).id for e in added]
        assert "keep" in device_ids
        assert "excl" not in device_ids


# ---------------------------------------------------------------------------
# Tests for lines 41-46: motion_detectors2 loop
# ---------------------------------------------------------------------------


class TestMotionDetector2Setup:
    def test_excluded_motion_detector2_not_added(self):
        """Excluded MD2 (line 41) must not appear in entities."""
        dev = _make_motion_detector2(device_id="excl-md2")
        hass, entry = _make_hass_and_entry(
            motion2=[dev],
            excluded_device_ids=["excl-md2"],
        )
        added = _run_setup(hass, entry)
        assert all(
            getattr(e, "_device", None) is not dev for e in added
        ), "Excluded motion_detector2 should not be added"

    def test_non_excluded_motion_detector2_is_added(self):
        """Non-excluded MD2 must result in a MotionDetectorLight entity (lines 42-50)."""
        dev = _make_motion_detector2(device_id="keep-md2")
        hass, entry = _make_hass_and_entry(
            motion2=[dev],
            excluded_device_ids=[],
        )
        added = _run_setup(hass, entry)
        assert any(
            isinstance(e, MotionDetectorLight) and e._device is dev for e in added
        ), "Non-excluded MD2 should produce a MotionDetectorLight entity"

    def test_async_migrate_called_for_motion_detector2(self):
        """async_migrate_to_new_unique_id must be called with attr_name='MotionLight'."""
        dev = _make_motion_detector2(device_id="md2-migrate")
        hass, entry = _make_hass_and_entry(motion2=[dev])
        migrate_calls = []

        async def _fake_migrate(hass_arg, platform, device, attr_name=None, **kw):
            migrate_calls.append((platform, device, attr_name))

        with patch(
            "custom_components.bosch_shc.light.async_migrate_to_new_unique_id",
            side_effect=_fake_migrate,
        ):
            added = []

            def _add(entities):
                added.extend(entities)

            asyncio.run(async_setup_entry(hass, entry, _add))

        assert any(
            call[1] is dev and call[2] == "MotionLight" for call in migrate_calls
        ), f"Expected migrate call with attr_name='MotionLight', got: {migrate_calls}"

    def test_excluded_motion_detector2_skips_migrate(self):
        """async_migrate must NOT be called for excluded MD2 devices."""
        dev = _make_motion_detector2(device_id="excl-migrate")
        hass, entry = _make_hass_and_entry(
            motion2=[dev],
            excluded_device_ids=["excl-migrate"],
        )
        migrate_calls = []

        async def _fake_migrate(hass_arg, platform, device, **kw):
            migrate_calls.append(device)

        with patch(
            "custom_components.bosch_shc.light.async_migrate_to_new_unique_id",
            side_effect=_fake_migrate,
        ):
            asyncio.run(async_setup_entry(hass, entry, lambda entities: None))

        assert not any(
            c is dev for c in migrate_calls
        ), "Excluded MD2 must not trigger async_migrate_to_new_unique_id"


# ---------------------------------------------------------------------------
# Tests for lines 165-167: MotionDetectorLight.brightness when level is None
# ---------------------------------------------------------------------------


class TestMotionDetectorLightBrightness:
    def _make_entity(self, multi_level_switch_value=None):
        """Build a MotionDetectorLight bypassing __init__ via __new__."""
        ent = MotionDetectorLight.__new__(MotionDetectorLight)
        ent._device = SimpleNamespace(
            id="md2-ent",
            root_device_id="shc-root",
            name="MD2 Light",
            manufacturer="Bosch",
            device_model="MD2",
            status="AVAILABLE",
            deleted=False,
            device_services=[],
            subscribe_callback=lambda eid, cb: None,
            unsubscribe_callback=lambda eid: None,
            binaryswitch=False,
            multi_level_switch=multi_level_switch_value,
        )
        ent._entry_id = "entry-test"
        ent._attr_name = "Motion Light"
        ent._attr_unique_id = "shc-root_md2-ent_motionlight"
        return ent

    def test_brightness_returns_zero_when_level_is_none(self):
        """Line 165-167: brightness must be 0 when multi_level_switch is None."""
        ent = self._make_entity(multi_level_switch_value=None)
        assert ent.brightness == 0

    def test_brightness_scales_correctly_when_level_set(self):
        """Sanity: brightness scales 0-100 → 0-255 correctly."""
        ent = self._make_entity(multi_level_switch_value=100)
        assert ent.brightness == 255

    def test_brightness_midpoint(self):
        """Midpoint level 50 → 128 (rounded)."""
        ent = self._make_entity(multi_level_switch_value=50)
        assert ent.brightness == round(50 * 255 / 100)

    def test_brightness_zero_level_gives_zero(self):
        """Level 0 → brightness 0."""
        ent = self._make_entity(multi_level_switch_value=0)
        assert ent.brightness == 0
