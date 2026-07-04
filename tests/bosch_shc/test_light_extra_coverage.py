"""Extra coverage for light.py.

Targets:
- line 31: device_excluded in ledvance/dimmer/hue loop
- lines 41-46: device_excluded + async_migrate_to_new_unique_id + MotionDetectorLight creation
- lines 165-167: MotionDetectorLight.brightness when multi_level_switch is None
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from homeassistant.const import Platform

from custom_components.bosch_shc.const import (
    DATA_SESSION,
    DOMAIN,
    OPT_EXCLUDED_DEVICES,
    OPT_LIGHTS_AS_LIGHT,
)
from custom_components.bosch_shc.light import (
    MotionDetectorLight,
    RelayLight,
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


def _make_motion_detector2(device_id="md2-1", room_id="room-2", supports_light=True):
    """Minimal device double for a motion detector II.

    supports_light=True models the OUTDOOR/[+M] installation profile (has the
    indicator-light services); False models the base/GENERIC profile MD2,
    which has neither BinarySwitch nor MultiLevelSwitch (#325/#303).
    """
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
        supports_light=supports_light,
    )


def _make_light_switch_bsm(device_id="bsm-1", room_id="room-3"):
    """Minimal device double for a #338 light-relay (BSM/light-attached)."""
    return SimpleNamespace(
        id=device_id,
        root_device_id="shc-root",
        room_id=room_id,
        name="Light Relay",
        serial=f"serial-{device_id}",
        manufacturer="Bosch",
        device_model="BSM",
        status="AVAILABLE",
        deleted=False,
        device_services=[],
        subscribe_callback=lambda eid, cb: None,
        unsubscribe_callback=lambda eid: None,
        switchstate=True,
    )


def _make_hass_and_entry(
    ledvance=None,
    motion2=None,
    light_switches_bsm=None,
    excluded_device_ids=None,
    lights_as_light_ids=None,
):
    """Return (hass, config_entry) with faked session and options."""
    ledvance = ledvance or []
    motion2 = motion2 or []
    light_switches_bsm = light_switches_bsm or []
    excluded = excluded_device_ids or []

    session = SimpleNamespace(
        device_helper=SimpleNamespace(
            ledvance_lights=ledvance,
            micromodule_dimmers=[],
            hue_lights=[],
            motion_detectors2=motion2,
            light_switches_bsm=light_switches_bsm,
        ),
    )

    entry_id = "entry-test"
    options = {OPT_EXCLUDED_DEVICES: excluded}
    if lights_as_light_ids is not None:
        options[OPT_LIGHTS_AS_LIGHT] = lights_as_light_ids

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

    with (
        patch(
            "custom_components.bosch_shc.light.async_migrate_to_new_unique_id",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "custom_components.bosch_shc.light.async_remove_stale_entity",
            new=AsyncMock(return_value=None),
        ),
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
        assert all(getattr(e, "_device", None) is not dev for e in added), (
            "Excluded ledvance light should not be added"
        )

    def test_non_excluded_ledvance_light_is_added(self):
        """Non-excluded ledvance light must appear in entities."""
        dev = _make_light_device(device_id="keep-light")
        hass, entry = _make_hass_and_entry(
            ledvance=[dev],
            excluded_device_ids=[],
        )
        added = _run_setup(hass, entry)
        assert any(getattr(e, "_device", None) is dev for e in added), (
            "Non-excluded ledvance light should be added"
        )

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
        assert all(getattr(e, "_device", None) is not dev for e in added), (
            "Excluded motion_detector2 should not be added"
        )

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

    def test_base_profile_motion_detector2_skipped_no_light_services(self):
        """Regression: a base/GENERIC profile MD2 (supports_light=False, no
        BinarySwitch/MultiLevelSwitch services) must NOT get a
        MotionDetectorLight entity — previously this crashed on every state
        read/write with AttributeError on the None service (#325/#303)."""
        dev = _make_motion_detector2(device_id="base-md2", supports_light=False)
        hass, entry = _make_hass_and_entry(
            motion2=[dev],
            excluded_device_ids=[],
        )
        added = _run_setup(hass, entry)
        assert all(getattr(e, "_device", None) is not dev for e in added), (
            "Base-profile MD2 (no [+M] light services) must not get a MotionDetectorLight"
        )

    def test_unsupported_profile_motion_detector2_removes_stale_entity(self):
        """#356: a MD2 whose profile no longer supports the light (e.g. after
        switching [+M] -> GENERIC via select.installation_profile) must have
        any previously-registered MotionDetectorLight entity actively removed,
        not just skipped on this setup pass."""
        dev = _make_motion_detector2(device_id="was-plusm-md2", supports_light=False)
        hass, entry = _make_hass_and_entry(motion2=[dev], excluded_device_ids=[])
        remove_calls = []

        async def _fake_remove(hass_arg, entity_domain, unique_id):
            remove_calls.append((entity_domain, unique_id))

        with (
            patch(
                "custom_components.bosch_shc.light.async_migrate_to_new_unique_id",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "custom_components.bosch_shc.light.async_remove_stale_entity",
                side_effect=_fake_remove,
            ),
        ):
            asyncio.run(async_setup_entry(hass, entry, lambda entities: None))

        assert remove_calls == [
            (Platform.LIGHT, "shc-root_was-plusm-md2_motionlight")
        ], (
            f"Expected one stale-removal call for the unsupported MD2, got {remove_calls}"
        )

    def test_excluded_motion_detector2_removes_stale_entity(self):
        """#356: excluding a device that previously had a light entity must
        also clean up the stale registry entry, not just skip creation."""
        dev = _make_motion_detector2(device_id="excl-had-light", supports_light=True)
        hass, entry = _make_hass_and_entry(
            motion2=[dev], excluded_device_ids=["excl-had-light"]
        )
        remove_calls = []

        async def _fake_remove(hass_arg, entity_domain, unique_id):
            remove_calls.append((entity_domain, unique_id))

        with (
            patch(
                "custom_components.bosch_shc.light.async_migrate_to_new_unique_id",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "custom_components.bosch_shc.light.async_remove_stale_entity",
                side_effect=_fake_remove,
            ),
        ):
            asyncio.run(async_setup_entry(hass, entry, lambda entities: None))

        assert remove_calls == [
            (Platform.LIGHT, "shc-root_excl-had-light_motionlight")
        ], f"Expected one stale-removal call for the excluded MD2, got {remove_calls}"

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

        with (
            patch(
                "custom_components.bosch_shc.light.async_migrate_to_new_unique_id",
                side_effect=_fake_migrate,
            ),
            patch(
                "custom_components.bosch_shc.light.async_remove_stale_entity",
                new=AsyncMock(return_value=None),
            ),
        ):
            asyncio.run(async_setup_entry(hass, entry, lambda entities: None))

        assert not any(c is dev for c in migrate_calls), (
            "Excluded MD2 must not trigger async_migrate_to_new_unique_id"
        )


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


# ---------------------------------------------------------------------------
# #338 light_switch_devices loop: RelayLight opt-out stale-entity cleanup
# ---------------------------------------------------------------------------


class TestRelayLightOptOutSetup:
    def test_opted_in_bsm_gets_relaylight(self):
        dev = _make_light_switch_bsm(device_id="bsm-in")
        hass, entry = _make_hass_and_entry(
            light_switches_bsm=[dev], lights_as_light_ids=["bsm-in"]
        )
        added = _run_setup(hass, entry)
        assert any(
            isinstance(e, RelayLight) and e._device is dev for e in added
        ), "Opted-in BSM should produce a RelayLight entity"

    def test_not_opted_in_bsm_produces_no_relaylight(self):
        dev = _make_light_switch_bsm(device_id="bsm-out")
        hass, entry = _make_hass_and_entry(
            light_switches_bsm=[dev], lights_as_light_ids=[]
        )
        added = _run_setup(hass, entry)
        assert all(getattr(e, "_device", None) is not dev for e in added)

    def test_opted_out_bsm_removes_stale_relaylight_entity(self):
        """Regression: a device previously opted in to "expose as light"
        (RelayLight created, unique_id = root_device_id_device_id) that gets
        opted back out must have that entity actively removed — an options
        change reloads the entry (OptionsFlowWithReload), so simply not
        re-creating the entity left an orphaned registry entry behind,
        exactly the failure mode #356 already fixed for MotionDetectorLight."""
        dev = _make_light_switch_bsm(device_id="was-light")
        hass, entry = _make_hass_and_entry(
            light_switches_bsm=[dev], lights_as_light_ids=[]
        )
        remove_calls = []

        async def _fake_remove(hass_arg, entity_domain, unique_id):
            remove_calls.append((entity_domain, unique_id))

        with (
            patch(
                "custom_components.bosch_shc.light.async_migrate_to_new_unique_id",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "custom_components.bosch_shc.light.async_remove_stale_entity",
                side_effect=_fake_remove,
            ),
        ):
            asyncio.run(async_setup_entry(hass, entry, lambda entities: None))

        assert remove_calls == [(Platform.LIGHT, "shc-root_was-light")], (
            f"Expected one stale-removal call for the opted-out BSM, got {remove_calls}"
        )

    def test_excluded_bsm_that_was_opted_in_removes_stale_relaylight_entity(self):
        dev = _make_light_switch_bsm(device_id="excl-was-light")
        hass, entry = _make_hass_and_entry(
            light_switches_bsm=[dev],
            excluded_device_ids=["excl-was-light"],
            lights_as_light_ids=["excl-was-light"],
        )
        remove_calls = []

        async def _fake_remove(hass_arg, entity_domain, unique_id):
            remove_calls.append((entity_domain, unique_id))

        with (
            patch(
                "custom_components.bosch_shc.light.async_migrate_to_new_unique_id",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "custom_components.bosch_shc.light.async_remove_stale_entity",
                side_effect=_fake_remove,
            ),
        ):
            asyncio.run(async_setup_entry(hass, entry, lambda entities: None))

        assert remove_calls == [(Platform.LIGHT, "shc-root_excl-was-light")], (
            f"Expected one stale-removal call for the excluded+opted-in BSM, got {remove_calls}"
        )
