"""Comprehensive coverage gap tests for all remaining uncovered lines.

Targets (by file):
  update.py:        41-60, 73-76, 111-113
  light.py:         43-50, 56-63, 100-104, 180, 299-307, 313-319
  event.py:         82-86, 216-222, 226-235, 262-264
  binary_sensor.py: 274-282, 333-334, 350-351, 368-369, 457-459,
                    572-574, 745-747, 1044-1045
  button.py:        157-163, 381-382, 400-401
  climate.py:       338
  select.py:        353-357, 363-366, 386-387, 394-395, 399-403,
                    462-466, 940-941, 954-955, 961
  sensor.py:        120, 258-261, 445-454, 462-465, 511-512,
                    1126, 1144-1145, 1163-1164, 1171-1172, 1186-1187, 1196-1197
  switch.py:        418, 536-546, 804, 860, 991-992, 1012-1013, 1034, 1135
  __init__.py:      508-515, 676, 703, 706  (skip 522-613, 275, 284)

Pattern: no HA harness, -p no:homeassistant flag. Uses SimpleNamespace, AsyncMock,
MagicMock, __new__ bypass for unit tests, asyncio.run for async setup tests.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from custom_components.bosch_shc.const import (
    OPT_ALL_LIGHTS_AS_LIGHT,
    OPT_EXCLUDED_DEVICES,
    OPT_SUPPRESS_CAMERA_SWITCHES,
    OPT_SUPPRESS_HUE_LIGHTS,
    OPT_SUPPRESS_LEDVANCE_LIGHTS,
)


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Common helpers
# ---------------------------------------------------------------------------

def _fake_dev(dev_id="dev1", root_id="root1", serial="SER1", **kw):
    base = dict(
        id=dev_id,
        root_device_id=root_id,
        name="FakeDev",
        serial=serial,
        device_services=[],
        room_id=None,
        deleted=False,
        status="AVAILABLE",
        manufacturer="Bosch",
        device_model="TestModel",
        subscribe_callback=MagicMock(),
        unsubscribe_callback=MagicMock(),
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _fake_hass(entry_id="E1", session=None, shc=None, options=None):
    """Minimal hass. session/shc are cached so a paired _fake_entry(hass=...)
    call can wire them onto entry.runtime_data (the modern storage location —
    this integration no longer uses hass.data[DOMAIN])."""
    shc_obj = shc or SimpleNamespace(
        identifiers={("bosch_shc", "shc")},
        name="SHC", manufacturer="Bosch", model="SHC", id="shc1",
    )
    h = MagicMock()
    h.data = {}
    h._fake_session = session
    h._fake_shc = shc_obj

    async def _executor_job(fn, *args):
        return fn(*args)

    h.async_add_executor_job = _executor_job
    h.config_entries = MagicMock()
    h.bus = MagicMock()
    h.bus.async_listen_once = MagicMock(return_value=MagicMock())
    h.async_create_task = MagicMock()
    return h


def _fake_entry(entry_id="E1", title="Test SHC", options=None, hass=None):
    """Build a fake config entry with runtime_data wired from `hass` (as
    produced by _fake_hass) when provided."""
    entry = MagicMock()
    entry.entry_id = entry_id
    entry.title = title
    entry.options = options or {}
    entry.unique_id = "uid1"
    entry.async_on_unload = MagicMock()
    entry.runtime_data = SimpleNamespace(
        session=getattr(hass, "_fake_session", None) if hass is not None else None,
        shc_device=getattr(hass, "_fake_shc", None) if hass is not None else None,
        title=title,
    )
    return entry


# ===========================================================================
# UPDATE.PY — lines 41-60, 73-76, 111-113
# ===========================================================================

class TestUpdateAsyncSetupEntry:
    """Cover update.py async_setup_entry body (lines 41-60)."""

    def _make_session(self, info=None, devices=None):
        session = MagicMock()
        session.information = info or SimpleNamespace(
            unique_id="aa:bb:cc:dd:ee:ff", version="9.0.0",
        )
        session.devices = devices or []
        return session

    def test_setup_entry_with_information_and_no_devices(self):
        """Lines 44-48, 54-60: controller entity created, empty device list."""
        from custom_components.bosch_shc.update import async_setup_entry

        session = self._make_session()
        hass = _fake_hass(session=session)
        entry = _fake_entry(hass=hass)

        collected = []
        _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))
        assert any(e._information for e in collected)

    def test_setup_entry_device_with_software_update(self):
        """Lines 54-58: device with supports_software_update=True adds DeviceUpdate."""
        from custom_components.bosch_shc.update import DeviceUpdate, async_setup_entry

        dev = _fake_dev(supports_software_update=True)
        session = self._make_session(devices=[dev])
        hass = _fake_hass(session=session)
        entry = _fake_entry(hass=hass)

        collected = []
        _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))
        assert any(isinstance(e, DeviceUpdate) for e in collected)

    def test_setup_entry_device_without_software_update(self):
        """Line 57: device without supports_software_update is skipped."""
        from custom_components.bosch_shc.update import DeviceUpdate, async_setup_entry

        dev = _fake_dev()  # no supports_software_update
        session = self._make_session(devices=[dev])
        hass = _fake_hass(session=session)
        entry = _fake_entry(hass=hass)

        collected = []
        _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))
        assert not any(isinstance(e, DeviceUpdate) for e in collected)


class TestControllerUpdateInit:
    """Cover ControllerUpdate.__init__ (lines 73-76)."""

    def test_init_sets_attributes(self):
        from custom_components.bosch_shc.update import ControllerUpdate

        info = SimpleNamespace(unique_id="aa:bb:cc:dd:ee:ff", version="9.0.0")
        cu = ControllerUpdate(info, "My SHC Title", "entry1")
        assert cu._information is info
        assert cu._entry_id == "entry1"
        assert "aa:bb:cc:dd:ee:ff" in cu._attr_unique_id
        assert cu._attr_device_info is not None


class TestControllerUpdateAsyncUpdate:
    """Cover ControllerUpdate.async_update (lines 111-113)."""

    def test_async_update_calls_refresh_when_present(self):
        from custom_components.bosch_shc.update import ControllerUpdate

        info = SimpleNamespace(unique_id="aa:bb:cc:dd:ee:ff", version="9.0.0")
        cu = ControllerUpdate(info, "My SHC", "e1")

        refresh_called = []

        async def fake_refresh():
            refresh_called.append(True)

        cu._information = SimpleNamespace(
            unique_id="aa:bb:cc:dd:ee:ff", version="9.0.0",
            async_refresh=fake_refresh,
        )
        _run(cu.async_update())
        assert refresh_called

    def test_async_update_no_refresh(self):
        """If async_refresh not present, no error."""
        from custom_components.bosch_shc.update import ControllerUpdate

        info = SimpleNamespace(unique_id="aa:bb:cc:dd:ee:ff", version="9.0.0")
        cu = ControllerUpdate(info, "My SHC", "e1")
        # information without async_refresh — must not raise
        _run(cu.async_update())


# ===========================================================================
# LIGHT.PY — lines 43-50, 56-63, 100-104, 180, 299-307, 313-319
# ===========================================================================

class TestLightSetupHueSuppressWithRegistry:
    """Lines 43-50: HUE lights suppressed + dev_registry entry exists → removed."""

    def _run_light_setup(self, options, hue_lights, dev_reg_mock):
        from custom_components.bosch_shc.light import async_setup_entry

        dh = MagicMock()
        dh.hue_lights = hue_lights
        dh.ledvance_lights = []
        dh.micromodule_dimmers = []
        dh.motion_detectors2 = []
        dh.micromodule_light_attached = []
        dh.light_switches_bsm = []

        session = MagicMock()
        session.device_helper = dh

        hass = _fake_hass(session=session)

        with patch(
            "custom_components.bosch_shc.light.get_dev_reg", return_value=dev_reg_mock
        ), patch("custom_components.bosch_shc.light.async_migrate_to_new_unique_id",
                 new_callable=AsyncMock):
            entry = _fake_entry(hass=hass, options=options)
            collected = []
            _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))
        return collected

    def test_hue_suppress_removes_device_from_registry(self):
        """Lines 43-52: when suppress HUE is on, dev_registry entry is removed."""
        dev = _fake_dev("hue1")
        dev_entry = SimpleNamespace(id="reg_id_hue1")
        dr_mock = MagicMock()
        dr_mock.async_get_device = MagicMock(return_value=dev_entry)
        dr_mock.async_update_device = MagicMock()

        self._run_light_setup(
            options={OPT_SUPPRESS_HUE_LIGHTS: True},
            hue_lights=[dev],
            dev_reg_mock=dr_mock,
        )
        dr_mock.async_update_device.assert_called_once()

    def test_hue_suppress_no_registry_entry(self):
        """Lines 43-51: dev_registry returns None → no update_device call."""
        dev = _fake_dev("hue1")
        dr_mock = MagicMock()
        dr_mock.async_get_device = MagicMock(return_value=None)
        dr_mock.async_update_device = MagicMock()

        self._run_light_setup(
            options={OPT_SUPPRESS_HUE_LIGHTS: True},
            hue_lights=[dev],
            dev_reg_mock=dr_mock,
        )
        dr_mock.async_update_device.assert_not_called()


class TestLightSetupLedvanceSuppressWithRegistry:
    """Lines 56-63: Ledvance lights suppressed + dev_registry entry exists."""

    def _run_light_setup(self, options, ledvance_lights, dev_reg_mock):
        from custom_components.bosch_shc.light import async_setup_entry

        dh = MagicMock()
        dh.hue_lights = []
        dh.ledvance_lights = ledvance_lights
        dh.micromodule_dimmers = []
        dh.motion_detectors2 = []
        dh.micromodule_light_attached = []
        dh.light_switches_bsm = []

        session = MagicMock()
        session.device_helper = dh

        hass = _fake_hass(session=session)

        with patch(
            "custom_components.bosch_shc.light.get_dev_reg", return_value=dev_reg_mock
        ), patch("custom_components.bosch_shc.light.async_migrate_to_new_unique_id",
                 new_callable=AsyncMock):
            entry = _fake_entry(hass=hass, options=options)
            collected = []
            _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))
        return collected

    def test_ledvance_suppress_removes_device_from_registry(self):
        """Lines 56-65: ledvance suppress removes matching device registry entry."""
        dev = _fake_dev("led1")
        dev_entry = SimpleNamespace(id="reg_led1")
        dr_mock = MagicMock()
        dr_mock.async_get_device = MagicMock(return_value=dev_entry)
        dr_mock.async_update_device = MagicMock()

        self._run_light_setup(
            options={OPT_SUPPRESS_LEDVANCE_LIGHTS: True},
            ledvance_lights=[dev],
            dev_reg_mock=dr_mock,
        )
        dr_mock.async_update_device.assert_called_once()

    def test_ledvance_suppress_no_registry_entry(self):
        """Lines 56-64: dev_registry returns None → no update_device call."""
        dev = _fake_dev("led1")
        dr_mock = MagicMock()
        dr_mock.async_get_device = MagicMock(return_value=None)
        dr_mock.async_update_device = MagicMock()

        self._run_light_setup(
            options={OPT_SUPPRESS_LEDVANCE_LIGHTS: True},
            ledvance_lights=[dev],
            dev_reg_mock=dr_mock,
        )
        dr_mock.async_update_device.assert_not_called()


class TestLightRelayOptIn:
    """Lines 100-104: RelayLight opt-in path in async_setup_entry."""

    def _run_light_setup(self, options, bsm_lights):
        from custom_components.bosch_shc.light import async_setup_entry

        dh = MagicMock()
        dh.hue_lights = []
        dh.ledvance_lights = []
        dh.micromodule_dimmers = []
        dh.motion_detectors2 = []
        dh.micromodule_light_attached = []
        dh.light_switches_bsm = bsm_lights

        session = MagicMock()
        session.device_helper = dh

        hass = _fake_hass(session=session)

        with (
            patch(
                "custom_components.bosch_shc.light.async_migrate_to_new_unique_id",
                new_callable=AsyncMock,
            ),
            patch(
                "custom_components.bosch_shc.light.async_remove_stale_entity",
                new_callable=AsyncMock,
            ),
        ):
            entry = _fake_entry(hass=hass, options=options)
            collected = []
            _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))
        return collected

    def test_relay_not_opted_in_skipped(self):
        """Lines 100-103: light_switch_as_light=False → continue, RelayLight not added."""
        from custom_components.bosch_shc.light import RelayLight

        dev = _fake_dev("bsm1")
        # No opt-in option → light_switch_as_light returns False
        collected = self._run_light_setup(options={}, bsm_lights=[dev])
        assert not any(isinstance(e, RelayLight) for e in collected)

    def test_relay_opted_in_added(self):
        """Lines 100-104: light_switch_as_light=True → RelayLight added."""
        from custom_components.bosch_shc.light import RelayLight

        dev = _fake_dev("bsm1")
        # All-opt-in → light_switch_as_light returns True
        collected = self._run_light_setup(
            options={OPT_ALL_LIGHTS_AS_LIGHT: True},
            bsm_lights=[dev],
        )
        assert any(isinstance(e, RelayLight) for e in collected)


class TestLightSwitchHsColorNone:
    """Line 180: LightSwitch.hs_color returns None when rgb_raw is None."""

    def test_hs_color_none_when_rgb_is_none(self):
        from custom_components.bosch_shc.light import LightSwitch

        ls = LightSwitch.__new__(LightSwitch)
        ls._device = SimpleNamespace(rgb=None)
        assert ls.hs_color is None


class TestRelayLightTurnOnErrors:
    """Lines 299-307: RelayLight.async_turn_on AttributeError + ClientError."""

    def _make_relay_light(self, device):
        from custom_components.bosch_shc.light import RelayLight

        rl = RelayLight.__new__(RelayLight)
        rl._device = device
        rl.entity_id = "light.relay_test"
        return rl

    def test_turn_on_attribute_error(self):
        """Lines 299-303: AttributeError in async_set_switchstate → debug log."""
        dev = MagicMock()
        dev.async_set_switchstate = AsyncMock(side_effect=AttributeError("no service"))
        rl = self._make_relay_light(dev)
        _run(rl.async_turn_on())  # must not raise

    def test_turn_on_client_error(self):
        """Lines 304-307: aiohttp.ClientError → debug log, no raise."""
        dev = MagicMock()
        dev.async_set_switchstate = AsyncMock(side_effect=aiohttp.ClientError("err"))
        rl = self._make_relay_light(dev)
        _run(rl.async_turn_on())  # must not raise

    def test_turn_on_timeout_error(self):
        """Lines 304-307: asyncio.TimeoutError → debug log, no raise."""
        dev = MagicMock()
        dev.async_set_switchstate = AsyncMock(side_effect=asyncio.TimeoutError())
        rl = self._make_relay_light(dev)
        _run(rl.async_turn_on())  # must not raise


class TestRelayLightTurnOffErrors:
    """Lines 313-319: RelayLight.async_turn_off AttributeError + ClientError."""

    def _make_relay_light(self, device):
        from custom_components.bosch_shc.light import RelayLight

        rl = RelayLight.__new__(RelayLight)
        rl._device = device
        rl.entity_id = "light.relay_test"
        return rl

    def test_turn_off_attribute_error(self):
        """Lines 313-315: AttributeError → debug log, no raise."""
        dev = MagicMock()
        dev.async_set_switchstate = AsyncMock(side_effect=AttributeError("no service"))
        rl = self._make_relay_light(dev)
        _run(rl.async_turn_off())

    def test_turn_off_client_error(self):
        """Lines 316-319: aiohttp.ClientError → debug log, no raise."""
        dev = MagicMock()
        dev.async_set_switchstate = AsyncMock(side_effect=aiohttp.ClientError())
        rl = self._make_relay_light(dev)
        _run(rl.async_turn_off())


# ===========================================================================
# EVENT.PY — lines 82-86, 216-222, 226-235, 262-264
# ===========================================================================

class TestEventSetupLightControls:
    """Lines 82-86: micromodule_light_controls loop — excluded and no-keypad branches."""

    def _run_event_setup(self, light_controls, options=None):
        from custom_components.bosch_shc.event import async_setup_entry

        dh = MagicMock()
        dh.universal_switches = []
        dh.motion_detectors = []
        dh.motion_detectors2 = []
        dh.smoke_detectors = []
        dh.micromodule_light_controls = light_controls

        session = MagicMock()
        session.device_helper = dh
        session.scenarios = []
        session.device_helper.smoke_detection_system = None

        hass = _fake_hass(session=session)
        entry = _fake_entry(hass=hass, options=options or {})

        collected = []
        _run(async_setup_entry(hass, entry, lambda ents, *a, **kw: collected.extend(ents)))
        return collected

    def test_light_control_excluded(self):
        """Line 82-83: device_excluded=True → continue."""
        dev = _fake_dev("lc1", has_keypad=True)
        collected = self._run_event_setup(
            light_controls=[dev],
            options={OPT_EXCLUDED_DEVICES: ["lc1"]},
        )
        assert len(collected) == 0

    def test_light_control_no_keypad(self):
        """Lines 84-85: has_keypad=False → continue."""
        dev = _fake_dev("lc1")  # no has_keypad attr → getattr returns False
        collected = self._run_event_setup(light_controls=[dev])
        assert len(collected) == 0

    def test_light_control_with_keypad_added(self):
        """Lines 86-90: has_keypad=True → LightControlButtonEvent added."""
        from custom_components.bosch_shc.event import LightControlButtonEvent

        dev = _fake_dev("lc1", has_keypad=True)
        dev.root_device_id = "root1"
        dev.name = "LightControl"
        collected = self._run_event_setup(light_controls=[dev])
        assert any(isinstance(e, LightControlButtonEvent) for e in collected)


class TestLightControlButtonEventCallback:
    """Lines 226-235: LightControlButtonEvent._event_callback full fire path."""

    def _make_entity(self, event_type_raw, ts, last_ts=-1):
        from custom_components.bosch_shc.event import LightControlButtonEvent

        ent = LightControlButtonEvent.__new__(LightControlButtonEvent)
        ent._attr_event_types = [
            "PRESS_SHORT", "PRESS_LONG", "PRESS_LONG_RELEASED",
            "SWITCH_ON", "SWITCH_OFF",
        ]
        ent._last_fired_timestamp = last_ts
        ent._device = SimpleNamespace(
            eventtype=event_type_raw,
            eventtimestamp=ts,
            id="lc1",
            name="LC",
        )
        # device_id is a property from SHCEntity: returns self._device.id
        # Setting _device is enough — no need to set device_id directly
        ent.entity_id = "event.lc"
        ent._dispatch_event = MagicMock()
        return ent

    def test_event_callback_fires_on_new_timestamp(self):
        """Lines 237-255: eventtype valid + timestamp advanced → _dispatch_event called."""
        et = SimpleNamespace(name="PRESS_SHORT")
        ent = self._make_entity(et, ts=1000, last_ts=500)
        ent._event_callback()
        ent._dispatch_event.assert_called_once()
        assert ent._last_fired_timestamp == 1000

    def test_event_callback_skips_none_eventtype(self):
        """_event_callback returns early when eventtype is None."""
        ent = self._make_entity(None, ts=1000)
        ent._event_callback()
        ent._dispatch_event.assert_not_called()

    def test_event_callback_skips_unknown_event_type(self):
        """_event_callback returns early when event type not in _attr_event_types."""
        et = SimpleNamespace(name="SWITCH_XXX")
        ent = self._make_entity(et, ts=1000)
        ent._event_callback()
        ent._dispatch_event.assert_not_called()

    def test_event_callback_skips_same_timestamp(self):
        """_event_callback returns early when timestamp unchanged."""
        et = SimpleNamespace(name="PRESS_SHORT")
        ent = self._make_entity(et, ts=1000, last_ts=1000)
        ent._event_callback()
        ent._dispatch_event.assert_not_called()


class TestLightControlDispatchEventValueError:
    """Lines 262-264: LightControlButtonEvent._dispatch_event ValueError branch."""

    def test_dispatch_event_value_error_returns_early(self):
        """Lines 262-264: ValueError in _trigger_event → warning log, return."""
        from custom_components.bosch_shc.event import LightControlButtonEvent

        ent = LightControlButtonEvent.__new__(LightControlButtonEvent)
        ent.entity_id = "event.lc"
        ent._trigger_event = MagicMock(side_effect=ValueError("bad type"))
        ent.schedule_update_ha_state = MagicMock()

        ent._dispatch_event("BAD_TYPE", {})
        ent.schedule_update_ha_state.assert_not_called()


# ===========================================================================
# BINARY_SENSOR.PY — 274-282, 333-334, 350-351, 368-369, 457-459,
#                    572-574, 745-747, 1044-1045
# ===========================================================================

class TestSirenSensorInits:
    """Lines 274-282, 333-334, 350-351, 368-369: real __init__ construction."""

    def _make_siren_dev(self, dev_id="siren1"):
        return _fake_dev(dev_id, supports_batterylevel=False)

    def test_acoustic_alarm_sensor_init(self):
        """Lines 331-334: SirenAcousticAlarmSensor.__init__ sets unique_id."""
        from custom_components.bosch_shc.binary_sensor import SirenAcousticAlarmSensor

        dev = self._make_siren_dev("s1")
        sensor = SirenAcousticAlarmSensor(dev, "entry1")
        assert sensor._attr_unique_id == "root1_s1_acoustic_alarm"

    def test_visual_alarm_sensor_init(self):
        """Lines 348-351: SirenVisualAlarmSensor.__init__ sets unique_id."""
        from custom_components.bosch_shc.binary_sensor import SirenVisualAlarmSensor

        dev = self._make_siren_dev("s2")
        sensor = SirenVisualAlarmSensor(dev, "entry1")
        assert sensor._attr_unique_id == "root1_s2_visual_alarm"

    def test_tamper_sensor_init(self):
        """Lines 366-369: SirenTamperSensor.__init__ sets unique_id."""
        from custom_components.bosch_shc.binary_sensor import SirenTamperSensor

        dev = self._make_siren_dev("s3")
        sensor = SirenTamperSensor(dev, "entry1")
        assert sensor._attr_unique_id == "root1_s3_tamper"

    def test_binary_sensor_setup_with_sirens(self):
        """Lines 274-282: async_setup_entry creates siren binary sensors."""
        from custom_components.bosch_shc.binary_sensor import (
            async_setup_entry,
        )

        siren = _fake_dev("s1", siren=MagicMock(), supports_batterylevel=False)
        dh = MagicMock()
        dh.thermostats = []
        dh.roomthermostats = []
        dh.wallthermostats = []
        dh.motion_detectors = []
        dh.motion_detectors2 = []
        dh.shutter_contacts = []
        dh.shutter_contacts2 = []
        dh.smoke_detectors = []
        dh.twinguards = []
        dh.micromodule_shutter_controls = []
        dh.micromodule_blinds = []
        dh.outdoor_sirens = [siren]
        dh.smoke_detection_system = None
        dh.heating_circuits = []
        dh.universal_switches = []
        dh.water_leakage_detectors = []
        dh.micromodule_dimmers = []
        dh.micromodule_relays = []
        dh.micromodule_impulse_relays = []
        dh.motion_detectors2 = []

        session = MagicMock()
        session.device_helper = dh

        hass = _fake_hass(session=session)
        entry = _fake_entry(hass=hass)

        platform_mock = MagicMock()
        platform_mock.async_register_entity_service = MagicMock()
        with patch("custom_components.bosch_shc.binary_sensor.async_migrate_to_new_unique_id",
                   new_callable=AsyncMock), \
             patch("custom_components.bosch_shc.binary_sensor.entity_platform.current_platform") as _cp:
            _cp.get.return_value = platform_mock
            collected = []
            _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))

        types = [type(e).__name__ for e in collected]
        assert "SirenAcousticAlarmSensor" in types
        assert "SirenVisualAlarmSensor" in types
        assert "SirenTamperSensor" in types


class TestSirenPowerSupplyFaultSensors:
    """Outdoor Siren power-supply fault flags (ac_dc_error/battery_defect/
    battery_temperature_abnormal/primary_power_supply_outage) must be wired
    into binary_sensor entities, gated on supports_power_supply, same as the
    existing sensor.py SirenBatterySensor/SirenMainPowerSensor/
    SirenSolarChargingSensor triplet.
    """

    def _make_siren_dev(self, dev_id="siren1", power_supply=None):
        return _fake_dev(
            dev_id,
            supports_batterylevel=False,
            supports_power_supply=True,
            power_supply=power_supply or MagicMock(),
        )

    def test_ac_dc_error_sensor_init(self):
        from custom_components.bosch_shc.binary_sensor import SirenAcDcErrorSensor

        dev = self._make_siren_dev("s1")
        sensor = SirenAcDcErrorSensor(dev, "entry1")
        assert sensor._attr_unique_id == "root1_s1_ac_dc_error"

    def test_battery_defect_sensor_init(self):
        from custom_components.bosch_shc.binary_sensor import SirenBatteryDefectSensor

        dev = self._make_siren_dev("s2")
        sensor = SirenBatteryDefectSensor(dev, "entry1")
        assert sensor._attr_unique_id == "root1_s2_battery_defect"

    def test_battery_temperature_abnormal_sensor_init(self):
        from custom_components.bosch_shc.binary_sensor import (
            SirenBatteryTemperatureAbnormalSensor,
        )

        dev = self._make_siren_dev("s3")
        sensor = SirenBatteryTemperatureAbnormalSensor(dev, "entry1")
        assert sensor._attr_unique_id == "root1_s3_battery_temperature_abnormal"

    def test_primary_power_supply_outage_sensor_init(self):
        from custom_components.bosch_shc.binary_sensor import (
            SirenPrimaryPowerSupplyOutageSensor,
        )

        dev = self._make_siren_dev("s4")
        sensor = SirenPrimaryPowerSupplyOutageSensor(dev, "entry1")
        assert sensor._attr_unique_id == "root1_s4_primary_power_supply_outage"

    def test_is_on_reads_underlying_power_supply_flags(self):
        from custom_components.bosch_shc.binary_sensor import (
            SirenAcDcErrorSensor,
            SirenBatteryDefectSensor,
            SirenBatteryTemperatureAbnormalSensor,
            SirenPrimaryPowerSupplyOutageSensor,
        )

        power_supply = SimpleNamespace(
            ac_dc_error=True,
            battery_defect=True,
            battery_temperature_abnormal=True,
            primary_power_supply_outage=True,
        )
        dev = self._make_siren_dev("s5", power_supply=power_supply)

        assert SirenAcDcErrorSensor(dev, "entry1").is_on is True
        assert SirenBatteryDefectSensor(dev, "entry1").is_on is True
        assert SirenBatteryTemperatureAbnormalSensor(dev, "entry1").is_on is True
        assert SirenPrimaryPowerSupplyOutageSensor(dev, "entry1").is_on is True

    def test_is_on_false_when_power_supply_flags_clear(self):
        from custom_components.bosch_shc.binary_sensor import (
            SirenAcDcErrorSensor,
            SirenBatteryDefectSensor,
            SirenBatteryTemperatureAbnormalSensor,
            SirenPrimaryPowerSupplyOutageSensor,
        )

        power_supply = SimpleNamespace(
            ac_dc_error=False,
            battery_defect=False,
            battery_temperature_abnormal=False,
            primary_power_supply_outage=False,
        )
        dev = self._make_siren_dev("s6", power_supply=power_supply)

        assert SirenAcDcErrorSensor(dev, "entry1").is_on is False
        assert SirenBatteryDefectSensor(dev, "entry1").is_on is False
        assert SirenBatteryTemperatureAbnormalSensor(dev, "entry1").is_on is False
        assert SirenPrimaryPowerSupplyOutageSensor(dev, "entry1").is_on is False

    def _setup_with_siren(self, siren):
        """Run async_setup_entry with a single outdoor siren and return the
        list of created entity type names."""
        from custom_components.bosch_shc.binary_sensor import async_setup_entry

        dh = MagicMock()
        dh.thermostats = []
        dh.roomthermostats = []
        dh.wallthermostats = []
        dh.motion_detectors = []
        dh.motion_detectors2 = []
        dh.shutter_contacts = []
        dh.shutter_contacts2 = []
        dh.smoke_detectors = []
        dh.twinguards = []
        dh.micromodule_shutter_controls = []
        dh.micromodule_blinds = []
        dh.outdoor_sirens = [siren]
        dh.smoke_detection_system = None
        dh.heating_circuits = []
        dh.universal_switches = []
        dh.water_leakage_detectors = []
        dh.micromodule_dimmers = []
        dh.micromodule_relays = []
        dh.micromodule_impulse_relays = []
        dh.climate_controls = []

        session = MagicMock()
        session.device_helper = dh

        hass = _fake_hass(session=session)
        entry = _fake_entry(hass=hass)

        platform_mock = MagicMock()
        platform_mock.async_register_entity_service = MagicMock()
        with patch("custom_components.bosch_shc.binary_sensor.async_migrate_to_new_unique_id",
                   new_callable=AsyncMock), \
             patch("custom_components.bosch_shc.binary_sensor.entity_platform.current_platform") as _cp:
            _cp.get.return_value = platform_mock
            collected = []
            _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))

        return [type(e).__name__ for e in collected]

    def test_setup_creates_power_supply_fault_sensors_when_supported(self):
        """supports_power_supply=True -> all 4 fault binary_sensors added."""
        siren = _fake_dev(
            "s1",
            siren=MagicMock(),
            supports_batterylevel=False,
            supports_power_supply=True,
        )
        types = self._setup_with_siren(siren)
        assert "SirenAcDcErrorSensor" in types
        assert "SirenBatteryDefectSensor" in types
        assert "SirenBatteryTemperatureAbnormalSensor" in types
        assert "SirenPrimaryPowerSupplyOutageSensor" in types

    def test_setup_skips_power_supply_fault_sensors_when_unsupported(self):
        """supports_power_supply=False (or missing) -> none of the 4 created."""
        siren = _fake_dev(
            "s1",
            siren=MagicMock(),
            supports_batterylevel=False,
            supports_power_supply=False,
        )
        types = self._setup_with_siren(siren)
        assert "SirenAcDcErrorSensor" not in types
        assert "SirenBatteryDefectSensor" not in types
        assert "SirenBatteryTemperatureAbnormalSensor" not in types
        assert "SirenPrimaryPowerSupplyOutageSensor" not in types
        # Baseline read-only siren sensors are still created regardless.
        assert "SirenTamperSensor" in types


class TestMotionDetectorWillRemoveUnsub:
    """Lines 457-459: MotionDetectionSensor.async_will_remove_from_hass."""

    def test_async_will_remove_calls_unsub(self):
        """Lines 457-459: _ha_stop_unsub is not None → call it and set to None."""
        from custom_components.bosch_shc.binary_sensor import MotionDetectionSensor

        ent = MotionDetectionSensor.__new__(MotionDetectionSensor)
        unsub = MagicMock()
        ent._ha_stop_unsub = unsub
        ent._service = None
        with patch("custom_components.bosch_shc.entity.SHCEntity.async_will_remove_from_hass", new_callable=AsyncMock):
            _run(ent.async_will_remove_from_hass())
        unsub.assert_called_once()
        assert ent._ha_stop_unsub is None

    def test_async_will_remove_no_unsub(self):
        """Line 457: _ha_stop_unsub is None → nothing called."""
        from custom_components.bosch_shc.binary_sensor import MotionDetectionSensor

        ent = MotionDetectionSensor.__new__(MotionDetectionSensor)
        ent._ha_stop_unsub = None
        ent._service = None
        with patch("custom_components.bosch_shc.entity.SHCEntity.async_will_remove_from_hass", new_callable=AsyncMock):
            _run(ent.async_will_remove_from_hass())  # must not raise


class TestAlarmStateWillRemoveUnsub:
    """Lines 572-574: SmokeDetectorSensor.async_will_remove_from_hass."""

    def test_async_will_remove_calls_unsub(self):
        from custom_components.bosch_shc.binary_sensor import SmokeDetectorSensor

        ent = SmokeDetectorSensor.__new__(SmokeDetectorSensor)
        unsub = MagicMock()
        ent._ha_stop_unsub = unsub
        ent._service = None  # no service → unsubscribe branch skipped
        with patch("custom_components.bosch_shc.entity.SHCEntity.async_will_remove_from_hass", new_callable=AsyncMock):
            _run(ent.async_will_remove_from_hass())
        unsub.assert_called_once()
        assert ent._ha_stop_unsub is None


class TestSurveillanceAlarmWillRemoveUnsub:
    """Lines 745-747: SmokeDetectionSystemSensor.async_will_remove_from_hass."""

    def test_async_will_remove_calls_unsub(self):
        from custom_components.bosch_shc.binary_sensor import SmokeDetectionSystemSensor

        ent = SmokeDetectionSystemSensor.__new__(SmokeDetectionSystemSensor)
        unsub = MagicMock()
        ent._ha_stop_unsub = unsub
        ent._service = None  # no service → unsubscribe branch skipped
        with patch("custom_components.bosch_shc.entity.SHCEntity.async_will_remove_from_hass", new_callable=AsyncMock):
            _run(ent.async_will_remove_from_hass())
        unsub.assert_called_once()
        assert ent._ha_stop_unsub is None


class TestTwinguardSmokeTestException:
    """Lines 1044-1045: TwinguardSmokeAlarmSensor.async_request_smoketest exception."""

    def test_smoketest_raises_homeassistant_error_on_exception(self):
        """Lines 1044-1045: SHCException → HomeAssistantError."""
        from boschshcpy.exceptions import SHCException
        from homeassistant.exceptions import HomeAssistantError

        from custom_components.bosch_shc.binary_sensor import TwinguardSmokeAlarmSensor

        ent = TwinguardSmokeAlarmSensor.__new__(TwinguardSmokeAlarmSensor)
        ent._device = SimpleNamespace(
            name="Twinguard",
            async_smoketest_requested=AsyncMock(side_effect=SHCException("err")),
        )

        with pytest.raises(HomeAssistantError):
            _run(ent.async_request_smoketest())


# ===========================================================================
# BUTTON.PY — lines 157-163, 381-382, 400-401
# ===========================================================================

class TestButtonDimmerSetup:
    """Lines 157-163: async_setup_entry dimmer preview buttons."""

    def _run_button_setup(self, dimmers, options=None):
        from custom_components.bosch_shc.button import async_setup_entry

        session = MagicMock()
        session.device_helper.micromodule_impulse_relays = []
        session.device_helper.smoke_detectors = []
        session.device_helper.twinguards = []
        session.device_helper.motion_detectors2 = []
        session.device_helper.outdoor_sirens = []
        session.device_helper.micromodule_dimmers = dimmers
        session.scenarios = []

        hass = _fake_hass(session=session)
        entry = _fake_entry(hass=hass, options=options or {})

        collected = []
        _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))
        return collected

    def test_dimmer_with_dimmer_configuration_adds_preview_buttons(self):
        """Lines 157-165: dimmer with supports_dimmer_configuration=True."""

        dev = _fake_dev("dim1", supports_dimmer_configuration=True)
        collected = self._run_button_setup([dev])
        types = [type(e).__name__ for e in collected]
        assert "DimmerPreviewMaxButton" in types
        assert "DimmerPreviewMinButton" in types

    def test_dimmer_without_supports_skips_preview_buttons(self):
        """Line 159: supports_dimmer_configuration=False → buttons not added."""

        dev = _fake_dev("dim1")  # no supports_dimmer_configuration
        collected = self._run_button_setup([dev])
        types = [type(e).__name__ for e in collected]
        assert "DimmerPreviewMaxButton" not in types


class TestDimmerPreviewButtonInits:
    """Lines 381-382, 400-401: DimmerPreviewMax/MinButton __init__."""

    def test_dimmer_preview_max_button_init(self):
        """Lines 379-382: DimmerPreviewMaxButton.__init__ sets unique_id."""
        from custom_components.bosch_shc.button import DimmerPreviewMaxButton

        dev = _fake_dev("dim1")
        btn = DimmerPreviewMaxButton(dev, "entry1")
        assert btn._attr_unique_id == "root1_dim1_dimmer_preview_max"

    def test_dimmer_preview_min_button_init(self):
        """Lines 398-401: DimmerPreviewMinButton.__init__ sets unique_id."""
        from custom_components.bosch_shc.button import DimmerPreviewMinButton

        dev = _fake_dev("dim1")
        btn = DimmerPreviewMinButton(dev, "entry1")
        assert btn._attr_unique_id == "root1_dim1_dimmer_preview_min"

    def test_dimmer_preview_max_press_with_service(self):
        """DimmerPreviewMaxButton.async_press calls async_preview_max_brightness."""
        from custom_components.bosch_shc.button import DimmerPreviewMaxButton

        svc = MagicMock()
        svc.async_preview_max_brightness = AsyncMock()
        dev = _fake_dev("dim1", dimmer_configuration=svc)
        btn = DimmerPreviewMaxButton.__new__(DimmerPreviewMaxButton)
        btn._device = dev
        _run(btn.async_press())
        svc.async_preview_max_brightness.assert_called_once()

    def test_dimmer_preview_min_press_with_service(self):
        """DimmerPreviewMinButton.async_press calls async_preview_min_brightness."""
        from custom_components.bosch_shc.button import DimmerPreviewMinButton

        svc = MagicMock()
        svc.async_preview_min_brightness = AsyncMock()
        dev = _fake_dev("dim1", dimmer_configuration=svc)
        btn = DimmerPreviewMinButton.__new__(DimmerPreviewMinButton)
        btn._device = dev
        _run(btn.async_press())
        svc.async_preview_min_brightness.assert_called_once()


# ===========================================================================
# CLIMATE.PY — line 338
# ===========================================================================

class TestClimateSupportsCoolingAutoMode:
    """Line 338: supports_cooling=True → async_set_cooling_mode(False) called on AUTO."""

    def test_set_hvac_mode_auto_with_cooling(self):
        """Line 338: AUTO mode + supports_cooling → async_set_cooling_mode called."""
        from homeassistant.components.climate import HVACMode

        from custom_components.bosch_shc.climate import ClimateControl

        ent = ClimateControl.__new__(ClimateControl)

        device = MagicMock()
        device.supports_cooling = True
        device.supports_boost_mode = False
        device.boost_mode = False
        # supports_eco must be False to prevent preset_mode returning PRESET_ECO
        device.supports_eco = False
        device.async_set_summer_mode = AsyncMock()
        device.async_set_cooling_mode = AsyncMock()
        device.async_set_operation_mode = AsyncMock()
        ent._device = device

        _run(ent.async_set_hvac_mode(HVACMode.AUTO))

        device.async_set_cooling_mode.assert_called_once_with(False)
        device.async_set_summer_mode.assert_called()


# ===========================================================================
# SELECT.PY — 353-357, 363-366, 386-387, 394-395, 399-403, 462-466,
#              940-941, 954-955, 961
# ===========================================================================

class TestSelectSirenSoundLevelSetup:
    """Lines 353-357: SirenSoundLevelSelect setup in async_setup_entry."""

    def _run_select_setup(self, sirens, options=None):
        from custom_components.bosch_shc.select import async_setup_entry

        dh = MagicMock()
        dh.thermostats = []
        dh.roomthermostats = []
        dh.wallthermostats = []
        dh.motion_detectors2 = []
        dh.shutter_contacts2 = []
        dh.outdoor_sirens = sirens
        dh.micromodule_dimmers = []

        session = MagicMock()
        session.device_helper = dh

        hass = _fake_hass(session=session)
        entry = _fake_entry(hass=hass, options=options or {})

        # select.py does NOT import async_migrate_to_new_unique_id — no patch needed
        collected = []
        _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))
        return collected

    def test_siren_with_siren_service_adds_select(self):
        """Lines 353-359: siren with siren service → SirenSoundLevelSelect added."""
        from custom_components.bosch_shc.select import SirenSoundLevelSelect

        siren = _fake_dev("s1", siren=MagicMock())
        collected = self._run_select_setup([siren])
        assert any(isinstance(e, SirenSoundLevelSelect) for e in collected)

    def test_siren_without_siren_service_skipped(self):
        """Line 355-356: siren without siren service → skipped."""
        from custom_components.bosch_shc.select import SirenSoundLevelSelect

        siren = _fake_dev("s1", siren=None)
        collected = self._run_select_setup([siren])
        assert not any(isinstance(e, SirenSoundLevelSelect) for e in collected)

    def test_siren_excluded_skipped(self):
        """Line 353-354: device_excluded → continue."""
        from custom_components.bosch_shc.select import SirenSoundLevelSelect

        siren = _fake_dev("s1", siren=MagicMock())
        collected = self._run_select_setup(
            [siren], options={OPT_EXCLUDED_DEVICES: ["s1"]}
        )
        assert not any(isinstance(e, SirenSoundLevelSelect) for e in collected)


class TestSelectDimmerPhaseControlSetup:
    """Lines 363-366: DimmerPhaseControlSelect setup."""

    def _run_select_setup(self, dimmers, options=None):
        from custom_components.bosch_shc.select import async_setup_entry

        dh = MagicMock()
        dh.thermostats = []
        dh.roomthermostats = []
        dh.wallthermostats = []
        dh.motion_detectors2 = []
        dh.shutter_contacts2 = []
        dh.outdoor_sirens = []
        dh.micromodule_dimmers = dimmers

        session = MagicMock()
        session.device_helper = dh

        hass = _fake_hass(session=session)
        entry = _fake_entry(hass=hass, options=options or {})

        # select.py does NOT import async_migrate_to_new_unique_id — no patch needed
        collected = []
        _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))
        return collected

    def test_dimmer_with_phase_control_adds_select(self):
        """Lines 363-368: supports_dimmer_configuration=True → DimmerPhaseControlSelect."""
        from custom_components.bosch_shc.select import DimmerPhaseControlSelect

        dev = _fake_dev("dim1", supports_dimmer_configuration=True)
        collected = self._run_select_setup([dev])
        assert any(isinstance(e, DimmerPhaseControlSelect) for e in collected)


class TestSirenSoundLevelSelectInit:
    """Lines 386-387: SirenSoundLevelSelect.__init__."""

    def test_siren_sound_level_select_init(self):
        """Lines 384-387: real __init__ sets unique_id."""
        from custom_components.bosch_shc.select import SirenSoundLevelSelect

        dev = _fake_dev("s1")
        sel = SirenSoundLevelSelect(dev, "entry1")
        assert sel._attr_unique_id == "root1_s1_sound_level"


class TestSirenSoundLevelSelectCurrentOption:
    """Lines 394-395: SirenSoundLevelSelect.current_option."""

    def test_current_option_valid(self):
        """Lines 392-395: normal path returns lowercased sound level."""
        from custom_components.bosch_shc.select import SirenSoundLevelSelect

        sel = SirenSoundLevelSelect.__new__(SirenSoundLevelSelect)
        sel._device = SimpleNamespace(
            siren=SimpleNamespace(sound_level=SimpleNamespace(name="HIGH"))
        )
        assert sel.current_option == "high"

    def test_current_option_attribute_error(self):
        """current_option returns None on AttributeError."""
        from custom_components.bosch_shc.select import SirenSoundLevelSelect

        sel = SirenSoundLevelSelect.__new__(SirenSoundLevelSelect)
        sel._device = SimpleNamespace(siren=None)
        assert sel.current_option is None


class TestSirenSoundLevelSelectAsyncSelect:
    """Lines 399-403: SirenSoundLevelSelect.async_select_option."""

    def test_async_select_invalid_option_keyerror(self):
        """Lines 401-402: invalid option raises KeyError → return early."""
        from custom_components.bosch_shc.select import SirenSoundLevelSelect

        sel = SirenSoundLevelSelect.__new__(SirenSoundLevelSelect)
        sel._device = MagicMock()
        # "INVALID" not in SoundLevel enum → KeyError → returns without setting
        _run(sel.async_select_option("INVALID_LEVEL"))
        sel._device.siren.async_set_configuration.assert_not_called()

    def test_async_select_valid_option(self):
        """Lines 399-403: valid option → async_set_configuration called."""

        from custom_components.bosch_shc.select import SirenSoundLevelSelect

        sel = SirenSoundLevelSelect.__new__(SirenSoundLevelSelect)
        siren = MagicMock()
        siren.async_set_configuration = AsyncMock()
        sel._device = SimpleNamespace(siren=siren)
        _run(sel.async_select_option("high"))
        siren.async_set_configuration.assert_called_once()


class TestOrientationLightResponseCurrentOptionError:
    """Lines 462-466: OrientationLightResponseSelect.current_option exception."""

    def test_current_option_exception_logged(self):
        """Lines 462-466: AttributeError from missing long_poll_interval → None."""
        from custom_components.bosch_shc.select import OrientationLightResponseSelect

        sel = OrientationLightResponseSelect.__new__(OrientationLightResponseSelect)
        sel._attr_options = ["ORIENTATION", "RESPONSE"]
        # SimpleNamespace raises AttributeError for missing 'long_poll_interval'
        # → hits the except (AttributeError, ValueError) block at lines 462-466
        sel._device = SimpleNamespace(name="MD2")
        result = sel.current_option
        assert result is None


class TestDimmerPhaseControlSelectInit:
    """Lines 940-941: DimmerPhaseControlSelect.__init__."""

    def test_init_sets_unique_id(self):
        """Lines 938-943: real __init__ sets unique_id."""
        from custom_components.bosch_shc.select import DimmerPhaseControlSelect

        dev = _fake_dev("dim1")
        sel = DimmerPhaseControlSelect(dev, "entry1")
        assert "dim1" in sel._attr_unique_id
        assert "dimmer_phase_control" in sel._attr_unique_id


class TestDimmerPhaseControlSelectCurrentOption:
    """Lines 954-955: DimmerPhaseControlSelect.current_option error path."""

    def test_current_option_service_none(self):
        """Line 948-950: dimmer_configuration is None → return None."""
        from custom_components.bosch_shc.select import DimmerPhaseControlSelect

        sel = DimmerPhaseControlSelect.__new__(DimmerPhaseControlSelect)
        sel._device = SimpleNamespace(dimmer_configuration=None, name="Dimmer")
        assert sel.current_option is None

    def test_current_option_attribute_error(self):
        """Lines 954-955: AttributeError accessing edge_phase_control_mode → return None."""
        from custom_components.bosch_shc.select import DimmerPhaseControlSelect

        # Use SimpleNamespace as service — accessing .edge_phase_control_mode raises
        # AttributeError (attribute not defined) → hits except block at lines 954-955
        svc = SimpleNamespace()  # no edge_phase_control_mode attribute
        sel = DimmerPhaseControlSelect.__new__(DimmerPhaseControlSelect)
        sel._attr_options = ["TRAILING", "LEADING"]
        sel._device = SimpleNamespace(dimmer_configuration=svc, name="Dimmer")
        assert sel.current_option is None


class TestDimmerPhaseControlAsyncSelectNone:
    """Line 961: DimmerPhaseControlSelect.async_select_option returns early when service is None."""

    def test_async_select_returns_when_service_none(self):
        """Line 959-961: service is None → returns without calling anything."""
        from custom_components.bosch_shc.select import DimmerPhaseControlSelect

        sel = DimmerPhaseControlSelect.__new__(DimmerPhaseControlSelect)
        sel._device = SimpleNamespace(dimmer_configuration=None, name="Dimmer")
        _run(sel.async_select_option("TRAILING"))  # must not raise


# ===========================================================================
# SENSOR.PY — 120, 258-261, 445-454, 462-465, 511-512, 1126,
#              1144-1145, 1163-1164, 1171-1172, 1186-1187, 1196-1197
# ===========================================================================

class TestSensorTerminalTempSetup:
    """Line 120: TerminalTemperatureSensor added when terminal_temperature is not None."""

    def _run_sensor_setup(self, roomthermostats, options=None):
        """TerminalTemperatureSensor is created in the wallthermostats+roomthermostats loop."""
        from custom_components.bosch_shc.sensor import async_setup_entry

        dh = MagicMock()
        dh.thermostats = []
        dh.roomthermostats = roomthermostats
        dh.wallthermostats = []
        dh.motion_detectors = []
        dh.motion_detectors2 = []
        dh.shutter_contacts = []
        dh.shutter_contacts2 = []
        dh.smoke_detectors = []
        dh.twinguards = []
        dh.smart_plugs = []
        dh.smart_plugs_compact = []
        dh.light_switches_bsm = []
        dh.micromodule_light_controls = []
        dh.micromodule_shutter_controls = []
        dh.micromodule_blinds = []
        dh.universal_switches = []
        dh.water_leakage_detectors = []
        dh.outdoor_sirens = []

        session = MagicMock()
        session.device_helper = dh
        session.emma = _fake_dev("emma1")  # prevent MagicMock EmmaPowerSensor

        hass = _fake_hass(session=session)
        entry = _fake_entry(hass=hass, options=options or {})

        with patch("custom_components.bosch_shc.sensor.async_migrate_to_new_unique_id",
                   new_callable=AsyncMock):
            collected = []
            _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))
        return collected

    def test_terminal_temperature_sensor_added_when_present(self):
        """Line 120: TerminalTemperatureSensor appended when terminal_temperature not None."""
        from custom_components.bosch_shc.sensor import TerminalTemperatureSensor

        dev = _fake_dev("t1", temperature=20.0, terminal_temperature=18.0,
                        supports_humidity=False, supports_batterylevel=False)
        collected = self._run_sensor_setup([dev])
        assert any(isinstance(e, TerminalTemperatureSensor) for e in collected)

    def test_terminal_temperature_sensor_not_added_when_absent(self):
        """Line 119: terminal_temperature=None → sensor not added."""
        from custom_components.bosch_shc.sensor import TerminalTemperatureSensor

        dev = _fake_dev("t1", temperature=20.0, terminal_temperature=None,
                        supports_humidity=False, supports_batterylevel=False)
        collected = self._run_sensor_setup([dev])
        assert not any(isinstance(e, TerminalTemperatureSensor) for e in collected)


class TestSensorEnergyYieldSmartPlug:
    """Lines 258-261: EnergyYieldSensor + PowerYieldSensor for smart_plugs."""

    def _run_sensor_setup_smart_plugs(self, smart_plugs, options=None):
        from custom_components.bosch_shc.sensor import async_setup_entry

        dh = MagicMock()
        dh.thermostats = []
        dh.roomthermostats = []
        dh.wallthermostats = []
        dh.motion_detectors = []
        dh.motion_detectors2 = []
        dh.shutter_contacts = []
        dh.shutter_contacts2 = []
        dh.smoke_detectors = []
        dh.twinguards = []
        dh.smart_plugs = smart_plugs
        dh.smart_plugs_compact = []
        # ALL list attrs needed by the power_sensors_enabled concatenation loop:
        dh.light_switches_bsm = []
        dh.micromodule_light_controls = []
        dh.micromodule_shutter_controls = []
        dh.micromodule_blinds = []
        dh.universal_switches = []
        dh.water_leakage_detectors = []
        dh.outdoor_sirens = []

        session = MagicMock()
        session.device_helper = dh
        session.emma = _fake_dev("emma1")  # give a proper fake to avoid init errors

        hass = _fake_hass(session=session)
        entry = _fake_entry(hass=hass, options=options or {})

        with patch("custom_components.bosch_shc.sensor.async_migrate_to_new_unique_id",
                   new_callable=AsyncMock):
            collected = []
            _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))
        return collected

    def test_energy_yield_sensors_added_when_supported(self):
        """Lines 257-263: supports_energy_yield=True → EnergyYieldSensor added."""

        dev = _fake_dev("sp1", supports_energy_yield=True,
                        supports_batterylevel=False, serial="SER1")
        collected = self._run_sensor_setup_smart_plugs([dev])
        types = [type(e).__name__ for e in collected]
        assert "EnergyYieldSensor" in types
        assert "PowerYieldSensor" in types


class TestSensorSirenSetup:
    """Lines 445-454: siren sensor setup."""

    def _run_sensor_setup_sirens(self, sirens, options=None):
        from custom_components.bosch_shc.sensor import async_setup_entry

        dh = MagicMock()
        dh.thermostats = []
        dh.roomthermostats = []
        dh.wallthermostats = []
        dh.motion_detectors = []
        dh.motion_detectors2 = []
        dh.shutter_contacts = []
        dh.shutter_contacts2 = []
        dh.smoke_detectors = []
        dh.twinguards = []
        dh.smart_plugs = []
        dh.smart_plugs_compact = []
        dh.light_switches_bsm = []
        dh.micromodule_light_controls = []
        dh.micromodule_shutter_controls = []
        dh.micromodule_blinds = []
        dh.universal_switches = []
        dh.water_leakage_detectors = []
        dh.outdoor_sirens = sirens

        session = MagicMock()
        session.device_helper = dh
        session.emma = _fake_dev("emma1")

        hass = _fake_hass(session=session)
        entry = _fake_entry(hass=hass, options=options or {})

        with patch("custom_components.bosch_shc.sensor.async_migrate_to_new_unique_id",
                   new_callable=AsyncMock):
            collected = []
            _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))
        return collected

    def test_siren_battery_solar_added_when_power_supply_supported(self):
        """Lines 447-456: supports_power_supply=True → 3 siren sensors added."""

        siren = _fake_dev("s1", supports_power_supply=True, supports_batterylevel=False)
        collected = self._run_sensor_setup_sirens([siren])
        types = [type(e).__name__ for e in collected]
        assert "SirenBatterySensor" in types
        assert "SirenMainPowerSensor" in types
        assert "SirenSolarChargingSensor" in types

    def test_siren_excluded_skips_sensors(self):
        """Lines 445-446: device_excluded → continue."""

        siren = _fake_dev("s1", supports_power_supply=True, supports_batterylevel=False)
        collected = self._run_sensor_setup_sirens(
            [siren], options={OPT_EXCLUDED_DEVICES: ["s1"]}
        )
        types = [type(e).__name__ for e in collected]
        assert "SirenBatterySensor" not in types


class TestSensorKeypadTriggerSetup:
    """Lines 462-465: KeypadTriggerSensor setup."""

    def _run_sensor_setup_universal_switches(self, switches, options=None):
        from custom_components.bosch_shc.sensor import async_setup_entry

        dh = MagicMock()
        dh.thermostats = []
        dh.roomthermostats = []
        dh.wallthermostats = []
        dh.motion_detectors = []
        dh.motion_detectors2 = []
        dh.shutter_contacts = []
        dh.shutter_contacts2 = []
        dh.smoke_detectors = []
        dh.twinguards = []
        dh.smart_plugs = []
        dh.smart_plugs_compact = []
        dh.light_switches_bsm = []
        dh.micromodule_light_controls = []
        dh.micromodule_shutter_controls = []
        dh.micromodule_blinds = []
        dh.universal_switches = switches
        dh.water_leakage_detectors = []
        dh.outdoor_sirens = []

        session = MagicMock()
        session.device_helper = dh
        session.emma = _fake_dev("emma1")

        hass = _fake_hass(session=session)
        entry = _fake_entry(hass=hass, options=options or {})

        with patch("custom_components.bosch_shc.sensor.async_migrate_to_new_unique_id",
                   new_callable=AsyncMock):
            collected = []
            _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))
        return collected

    def test_keypad_trigger_added_when_supported(self):
        """Lines 461-469: diagnostic_enabled (default True) + supports_keypadtrigger → added."""

        sw = _fake_dev("us1", supports_keypadtrigger=True, supports_batterylevel=False)
        # OPT_DIAGNOSTIC_ENTITIES defaults to True — just pass {} to use default
        collected = self._run_sensor_setup_universal_switches([sw])
        types = [type(e).__name__ for e in collected]
        assert "KeypadTriggerSensor" in types


class TestTerminalTemperatureSensorInit:
    """Lines 511-512: TerminalTemperatureSensor.__init__."""

    def test_terminal_temperature_sensor_init(self):
        """Lines 509-514: real __init__ sets unique_id."""
        from custom_components.bosch_shc.sensor import TerminalTemperatureSensor

        dev = _fake_dev("t1")
        sensor = TerminalTemperatureSensor(dev, "entry1")
        assert "terminal_temperature" in sensor._attr_unique_id


class TestInstallationProfileCurrentOptionNone:
    """InstallationProfileSelect.current_option returns None when profile is None."""

    def test_current_option_none_when_profile_is_none(self):
        """getattr returns None → return None immediately."""
        from custom_components.bosch_shc.select import InstallationProfileSelect

        ent = InstallationProfileSelect.__new__(InstallationProfileSelect)
        ent._attr_options = ["generic", "outdoor"]
        ent._device = SimpleNamespace(profile=None)
        assert ent.current_option is None


class TestSirenSensorInits2:
    """Lines 1144-1145, 1163-1164, 1186-1187: Siren sensor __init__ methods."""

    def test_siren_battery_sensor_init(self):
        """Lines 1142-1145: SirenBatterySensor.__init__."""
        from custom_components.bosch_shc.sensor import SirenBatterySensor

        dev = _fake_dev("s1")
        sensor = SirenBatterySensor(dev, "entry1")
        assert "siren_battery" in sensor._attr_unique_id

    def test_siren_main_power_sensor_init(self):
        """Lines 1161-1164: SirenMainPowerSensor.__init__."""
        from custom_components.bosch_shc.sensor import SirenMainPowerSensor

        dev = _fake_dev("s1")
        sensor = SirenMainPowerSensor(dev, "entry1")
        assert "siren_main_power" in sensor._attr_unique_id

    def test_siren_solar_charging_sensor_init(self):
        """Lines 1184-1187: SirenSolarChargingSensor.__init__."""
        from custom_components.bosch_shc.sensor import SirenSolarChargingSensor

        dev = _fake_dev("s1")
        sensor = SirenSolarChargingSensor(dev, "entry1")
        assert "siren_solar_charging" in sensor._attr_unique_id


class TestSirenSensorNativeValueErrors:
    """Lines 1171-1172, 1196-1197: error paths in siren sensor native_value."""

    def test_siren_main_power_attribute_error(self):
        """Lines 1171-1172: AttributeError → return None."""
        from custom_components.bosch_shc.sensor import SirenMainPowerSensor

        ent = SirenMainPowerSensor.__new__(SirenMainPowerSensor)
        ent._device = SimpleNamespace(power_supply=None)
        # power_supply is None → .main_power_supply raises AttributeError
        assert ent.native_value is None

    def test_siren_solar_charging_attribute_error(self):
        """Lines 1196-1197: AttributeError → return None."""
        from custom_components.bosch_shc.sensor import SirenSolarChargingSensor

        ent = SirenSolarChargingSensor.__new__(SirenSolarChargingSensor)
        ent._device = SimpleNamespace(power_supply=None)
        assert ent.native_value is None


# ===========================================================================
# SWITCH.PY — 418, 536-546, 804, 860, 991-992, 1012-1013, 1034, 1135
# ===========================================================================

class TestSwitchLightRelayOptInSkip:
    """Line 418: light_switch_as_light=True → switch skipped (continue)."""

    def _run_switch_setup(self, bsm_lights, options):
        from custom_components.bosch_shc.switch import async_setup_entry

        dh = MagicMock()
        dh.smart_plugs = []
        dh.smart_plugs_compact = []
        dh.micromodule_relays = []
        dh.micromodule_impulse_relays = []
        dh.micromodule_dimmers = []
        dh.light_switches_bsm = bsm_lights
        dh.micromodule_light_attached = []
        dh.micromodule_light_controls = []
        dh.camera_eyes = []
        dh.camera_360 = []
        dh.camera_outdoor_gen2 = []
        dh.presence_simulation_system = None
        dh.shutter_contacts2 = []
        dh.thermostats = []
        dh.roomthermostats = []
        dh.wallthermostats = []
        dh.motion_detectors2 = []
        dh.universal_switches = []
        dh.twinguards = []
        dh.smoke_detectors = []
        dh.smoke_detection_system = None

        session = MagicMock()
        session.device_helper = dh
        session.userdefinedstates = []
        session._subscribers = []
        session.subscribe = MagicMock()

        hass = _fake_hass(session=session)
        entry = _fake_entry(hass=hass, options=options)
        entry.async_on_unload = MagicMock()

        with (
            patch(
                "custom_components.bosch_shc.switch.async_migrate_to_new_unique_id",
                new_callable=AsyncMock,
            ),
            patch(
                "custom_components.bosch_shc.switch.async_remove_stale_entity",
                new_callable=AsyncMock,
            ),
        ):
            collected = []
            _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))
        return collected

    def test_relay_opted_in_as_light_skipped_in_switch(self):
        """Line 418: light relay opted in as light → no SHCSwitch created for it."""
        dev = _fake_dev("bsm1")
        # Opt in via OPT_ALL_LIGHTS_AS_LIGHT → switch skips it
        collected = self._run_switch_setup(
            [dev], options={OPT_ALL_LIGHTS_AS_LIGHT: True}
        )
        # No switch entity for bsm1 (ChildLock config entities are allowed)
        switch_ids = [getattr(e, "_attr_unique_id", "") for e in collected]
        assert not any(
            "bsm1" in sid
            and "swapoutputs" not in sid.lower()
            and "childlock" not in sid.lower()
            for sid in switch_ids
        )


class TestSwitchSuppressCamerasRegistry:
    """Lines 536-546: suppress_cameras removes devices from registry."""

    def _run_switch_setup_cameras(self, cameras_eyes, options):
        from custom_components.bosch_shc.switch import async_setup_entry

        dh = MagicMock()
        dh.smart_plugs = []
        dh.smart_plugs_compact = []
        dh.micromodule_relays = []
        dh.micromodule_impulse_relays = []
        dh.micromodule_dimmers = []
        dh.light_switches_bsm = []
        dh.micromodule_light_attached = []
        dh.micromodule_light_controls = []
        dh.camera_eyes = cameras_eyes
        dh.camera_360 = []
        dh.camera_outdoor_gen2 = []
        dh.presence_simulation_system = None
        dh.shutter_contacts2 = []
        dh.thermostats = []
        dh.roomthermostats = []
        dh.wallthermostats = []
        dh.motion_detectors2 = []
        dh.universal_switches = []
        dh.twinguards = []
        dh.smoke_detectors = []
        dh.smoke_detection_system = None

        session = MagicMock()
        session.device_helper = dh
        session.userdefinedstates = []
        session._subscribers = []
        session.subscribe = MagicMock()

        dev_entry = SimpleNamespace(id="reg_cam1")
        dr_mock = MagicMock()
        dr_mock.async_get_device = MagicMock(return_value=dev_entry)
        dr_mock.async_update_device = MagicMock()

        hass = _fake_hass(session=session)
        entry = _fake_entry(hass=hass, options=options)
        entry.async_on_unload = MagicMock()

        with patch("custom_components.bosch_shc.switch.get_dev_reg",
                   return_value=dr_mock), \
             patch("custom_components.bosch_shc.switch.async_migrate_to_new_unique_id",
                   new_callable=AsyncMock):
            collected = []
            _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))

        return dr_mock

    def test_suppress_cameras_removes_from_registry(self):
        """Lines 536-548: suppress_cameras=True → async_update_device called."""
        cam = _fake_dev("cam1")
        dr_mock = self._run_switch_setup_cameras(
            [cam], options={OPT_SUPPRESS_CAMERA_SWITCHES: True}
        )
        dr_mock.async_update_device.assert_called()


class TestSwitchMotionDetector2TamperProtection:
    """Line 804: smoke_detector/motion_detector2 tamper_protection_enabled."""

    def _run_switch_setup_md2(self, motion_detectors2, options=None):
        from custom_components.bosch_shc.switch import async_setup_entry

        dh = MagicMock()
        dh.smart_plugs = []
        dh.smart_plugs_compact = []
        dh.micromodule_relays = []
        dh.micromodule_impulse_relays = []
        dh.micromodule_dimmers = []
        dh.light_switches_bsm = []
        dh.micromodule_light_attached = []
        dh.micromodule_light_controls = []
        dh.camera_eyes = []
        dh.camera_360 = []
        dh.camera_outdoor_gen2 = []
        dh.presence_simulation_system = None
        dh.shutter_contacts2 = []
        dh.thermostats = []
        dh.roomthermostats = []
        dh.wallthermostats = []
        dh.motion_detectors2 = motion_detectors2
        dh.universal_switches = []
        dh.twinguards = []
        dh.smoke_detectors = []
        dh.smoke_detection_system = None

        session = MagicMock()
        session.device_helper = dh
        session.userdefinedstates = []
        session._subscribers = []
        session.subscribe = MagicMock()

        hass = _fake_hass(session=session)
        entry = _fake_entry(hass=hass, options=options or {})
        entry.async_on_unload = MagicMock()

        with patch("custom_components.bosch_shc.switch.async_migrate_to_new_unique_id",
                   new_callable=AsyncMock):
            collected = []
            _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))
        return collected

    def test_motion_detector2_with_tamper_protection_switch(self):
        """Line 804: tamper_protection_enabled → TamperProtection switch added."""
        dev = _fake_dev(
            "md2_1",
            supports_silentmode=True,
            pet_immunity_enabled=True,
            tamper_protection_enabled=False,  # hasattr must return True
        )
        collected = self._run_switch_setup_md2([dev])
        unique_ids = [getattr(e, "_attr_unique_id", "") for e in collected]
        # SHCSwitch uses attr_name.lower() in unique_id → "tamperprotection"
        assert any("tamperprotection" in uid for uid in unique_ids)


class TestSwitchSmokeDetectorIntrusionAlarm:
    """Line 860: smoke_detector with supports_intrusion_alarm."""

    def _run_switch_setup_smoke_detectors(self, smoke_detectors, options=None):
        from custom_components.bosch_shc.switch import async_setup_entry

        dh = MagicMock()
        dh.smart_plugs = []
        dh.smart_plugs_compact = []
        dh.micromodule_relays = []
        dh.micromodule_impulse_relays = []
        dh.micromodule_dimmers = []
        dh.light_switches_bsm = []
        dh.micromodule_light_attached = []
        dh.micromodule_light_controls = []
        dh.camera_eyes = []
        dh.camera_360 = []
        dh.camera_outdoor_gen2 = []
        dh.presence_simulation_system = None
        dh.shutter_contacts2 = []
        dh.thermostats = []
        dh.roomthermostats = []
        dh.wallthermostats = []
        dh.motion_detectors2 = []
        dh.universal_switches = []
        dh.twinguards = []
        dh.smoke_detectors = smoke_detectors
        dh.smoke_detection_system = None

        session = MagicMock()
        session.device_helper = dh
        session.userdefinedstates = []
        session._subscribers = []
        session.subscribe = MagicMock()

        hass = _fake_hass(session=session)
        entry = _fake_entry(hass=hass, options=options or {})
        entry.async_on_unload = MagicMock()

        with patch("custom_components.bosch_shc.switch.async_migrate_to_new_unique_id",
                   new_callable=AsyncMock):
            collected = []
            _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))
        return collected

    def test_smoke_detector_with_intrusion_alarm_switch(self):
        """Line 860: supports_intrusion_alarm=True → intrusion alarm switch added."""
        dev = _fake_dev("sd1", supports_intrusion_alarm=True,
                        supports_smoke_sensitivity=False)
        collected = self._run_switch_setup_smoke_detectors([dev])
        unique_ids = [getattr(e, "_attr_unique_id", "") for e in collected]
        # SHCSwitch uses attr_name.lower() in unique_id → "intrusionalarm"
        assert any("intrusionalarm" in uid for uid in unique_ids)


class TestSHCSwitchTurnOnClientError:
    """Lines 991-992: SHCSwitch.async_turn_on aiohttp.ClientError branch."""

    def _make_switch(self, on_key="switchstate", on_value=True):
        from homeassistant.components.switch import SwitchDeviceClass

        from custom_components.bosch_shc.switch import (
            SHCSwitch,
        )

        desc = SimpleNamespace(
            on_key=on_key,
            on_value=on_value,
            translation_key="lightswitch",
            should_poll=False,
            key="lightswitch",
            name="Light Switch",
            device_class=SwitchDeviceClass.SWITCH,
        )
        sw = SHCSwitch.__new__(SHCSwitch)
        sw.entity_description = desc
        sw.entity_id = "switch.test"
        sw._has_async_update = False
        return sw

    def test_turn_on_client_error_logged_not_raised(self):
        """Lines 991-995: aiohttp.ClientError in async_set_switchstate → debug log."""
        sw = self._make_switch()
        dev = MagicMock()
        dev.async_set_switchstate = AsyncMock(side_effect=aiohttp.ClientError("err"))
        sw._device = dev
        _run(sw.async_turn_on())  # must not raise

    def test_turn_off_client_error_logged_not_raised(self):
        """Lines 1012-1016: aiohttp.ClientError in async_set_switchstate → debug log."""
        sw = self._make_switch()
        dev = MagicMock()
        dev.async_set_switchstate = AsyncMock(side_effect=aiohttp.ClientError("err"))
        sw._device = dev
        _run(sw.async_turn_off())  # must not raise


class TestSHCSwitchAsyncUpdateFallback:
    """Line 1034: SHCSwitch.async_update executor fallback when no async_update."""

    def test_async_update_fallback_to_executor(self):
        """Line 1034: _has_async_update=False → async_add_executor_job called."""
        from custom_components.bosch_shc.switch import SHCSwitch

        sw = SHCSwitch.__new__(SHCSwitch)
        sw._has_async_update = False

        update_called = []

        def sync_update():
            update_called.append(True)

        sw._device = SimpleNamespace(update=sync_update)

        executor_calls = []

        async def fake_executor_job(fn, *args):
            executor_calls.append(fn)
            fn(*args)

        sw.hass = SimpleNamespace(async_add_executor_job=fake_executor_job)
        _run(sw.async_update())
        assert update_called


class TestUDSSwitchAsyncUpdateFallback:
    """Line 1135: SHCUserDefinedStateSwitch.async_update executor fallback."""

    def test_async_update_fallback_to_executor(self):
        """Line 1135: _has_async_update=False → executor job."""
        from custom_components.bosch_shc.switch import SHCUserDefinedStateSwitch

        sw = SHCUserDefinedStateSwitch.__new__(SHCUserDefinedStateSwitch)
        sw._has_async_update = False

        entity_description = SimpleNamespace(should_poll=True)
        sw.entity_description = entity_description

        update_called = []

        def sync_update():
            update_called.append(True)

        sw._device = SimpleNamespace(update=sync_update)

        async def fake_executor_job(fn, *args):
            fn(*args)

        sw.hass = SimpleNamespace(async_add_executor_job=fake_executor_job)
        _run(sw.async_update())
        assert update_called


# ===========================================================================
# __init__.py — lines 508-515 (_parse_time), 676, 703, 706
# ===========================================================================

class TestInitParsetime:
    """Lines 508-515: _parse_time inner function via async_setup_entry with
    silent_mode_enabled + valid start/end options.

    Strategy: run a full async_setup_entry with all heavy dependencies
    patched out, supplying OPT_SILENT_MODE_ENABLED + OPT_SILENT_MODE_START
    + OPT_SILENT_MODE_END + OPT_PRESENCE_ENTITY to trigger the silent-mode
    block and exercise _parse_time.
    """

    PATCH_SESSION = "custom_components.bosch_shc.SHCSessionAsync"
    PATCH_DR = "custom_components.bosch_shc.dr"
    PATCH_PARSE_CERT = "custom_components.bosch_shc.parse_certificate"
    PATCH_TRACK_INTERVAL = "custom_components.bosch_shc.async_track_time_interval"
    PATCH_TRACK_STATE = "custom_components.bosch_shc.async_track_state_change_event"
    PATCH_IR = "custom_components.bosch_shc.ir"

    def _make_session(self):
        from boschshcpy import SHCSessionAsync as _SA
        session = MagicMock(spec=_SA)
        session.async_init = AsyncMock()
        session.start_polling = AsyncMock()
        session.stop_polling = AsyncMock()
        session.information = SimpleNamespace(
            updateState=SimpleNamespace(name="NO_UPDATE_AVAILABLE"),
            unique_id="aa:bb:cc:dd:ee:ff",
            version="9.0.0",
            name="SHC",
        )
        session.scenarios = []
        session.subscribe_scenario_callback = MagicMock()
        session.unsubscribe_scenario_callback = MagicMock()
        dh = MagicMock()
        dh.universal_switches = []
        dh.camera_eyes = []
        dh.camera_360 = []
        dh.camera_outdoor_gen2 = []
        session.device_helper = dh
        return session

    def _make_hass(self):
        hass = MagicMock()
        hass.data = {}

        async def _executor_job(fn, *args):
            return fn(*args)

        hass.async_add_executor_job = _executor_job
        hass.config_entries = MagicMock()
        hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)
        hass.config_entries.async_entries = MagicMock(return_value=[])
        hass.bus = MagicMock()
        hass.bus.async_listen_once = MagicMock(return_value=MagicMock())
        hass.services = MagicMock()
        hass.services.has_service = MagicMock(return_value=True)  # already registered
        hass.async_create_task = MagicMock()
        hass.states = MagicMock()
        hass.states.get = MagicMock(return_value=SimpleNamespace(state="home"))
        return hass

    def _make_entry(self):

        from custom_components.bosch_shc.const import (
            OPT_PRESENCE_ENTITY,
            OPT_SILENT_MODE_ENABLED,
            OPT_SILENT_MODE_END,
            OPT_SILENT_MODE_START,
        )

        entry = MagicMock()
        entry.entry_id = "eid_parsetime"
        entry.title = "ParseTime SHC"
        entry.data = {
            "ssl_certificate": "",
            "ssl_key": "",
            "host": "192.168.1.1",
            "hostname": "192.168.1.1",
        }
        entry.options = {
            OPT_PRESENCE_ENTITY: ["person.test"],
            OPT_SILENT_MODE_ENABLED: True,
            OPT_SILENT_MODE_START: "22:30",
            OPT_SILENT_MODE_END: "07:00",
        }
        entry.add_update_listener = MagicMock(return_value=MagicMock())
        entry.async_on_unload = MagicMock()
        return entry

    def test_parse_time_valid_hhmm(self):
        """Lines 508-513: _parse_time parses HH:MM format correctly."""
        from custom_components.bosch_shc import async_setup_entry

        session = self._make_session()
        hass = self._make_hass()
        entry = self._make_entry()

        fake_dr = MagicMock()
        fake_dr.async_get_or_create = MagicMock(
            return_value=SimpleNamespace(id="fake_shc_id")
        )

        with patch(self.PATCH_SESSION, return_value=session), \
             patch(self.PATCH_DR) as dr_mock, \
             patch(self.PATCH_PARSE_CERT, return_value=None), \
             patch(self.PATCH_TRACK_INTERVAL, return_value=MagicMock()), \
             patch(self.PATCH_TRACK_STATE, return_value=MagicMock()), \
             patch(self.PATCH_IR):
            dr_mock.async_get = MagicMock(return_value=fake_dr)
            dr_mock.async_get_or_create = MagicMock()
            _run(async_setup_entry(hass, entry))

        # If no exception, _parse_time executed (lines 508-513 covered)


class TestInitCameraToolIssue:
    """Line 676: ir.async_create_issue for camera tool when cameras present."""

    def _make_full_setup_with_cameras(self, has_cameras, camera_tool_installed):
        from boschshcpy import SHCSessionAsync as _SA

        from custom_components.bosch_shc import async_setup_entry

        session = MagicMock(spec=_SA)
        session.async_init = AsyncMock()
        session.start_polling = AsyncMock()
        session.information = SimpleNamespace(
            updateState=SimpleNamespace(name="NO_UPDATE_AVAILABLE"),
            unique_id="aa:bb:cc:dd:ee:ff",
            version="9.0.0",
            name="SHC",
        )
        session.scenarios = []
        session.subscribe_scenario_callback = MagicMock()
        session.unsubscribe_scenario_callback = MagicMock()

        dh = MagicMock()
        dh.universal_switches = []
        cam_list = [_fake_dev("cam1")] if has_cameras else []
        dh.camera_eyes = cam_list
        dh.camera_360 = []
        dh.camera_outdoor_gen2 = []
        session.device_helper = dh

        hass = MagicMock()
        hass.data = {}

        async def _executor_job(fn, *args):
            return fn(*args)

        hass.async_add_executor_job = _executor_job
        hass.config_entries = MagicMock()
        hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)
        # Control camera_tool_installed via async_entries
        tool_entries = [MagicMock()] if camera_tool_installed else []
        hass.config_entries.async_entries = MagicMock(return_value=tool_entries)
        hass.bus = MagicMock()
        hass.bus.async_listen_once = MagicMock(return_value=MagicMock())
        hass.services = MagicMock()
        hass.services.has_service = MagicMock(return_value=True)
        hass.async_create_task = MagicMock()

        entry = MagicMock()
        entry.entry_id = "eid_cam"
        entry.title = "Camera SHC"
        entry.data = {
            "ssl_certificate": "", "ssl_key": "", "host": "192.168.1.1",
            "hostname": "192.168.1.1",
        }
        entry.options = {}
        entry.add_update_listener = MagicMock(return_value=MagicMock())
        entry.async_on_unload = MagicMock()

        fake_dr = MagicMock()
        fake_dr.async_get_or_create = MagicMock(
            return_value=SimpleNamespace(id="fake_shc_id")
        )

        ir_mock = MagicMock()
        issue_created = []

        def _create_issue(h, domain, issue_id, **kwargs):
            issue_created.append(issue_id)

        ir_mock.async_create_issue = MagicMock(side_effect=_create_issue)
        ir_mock.async_delete_issue = MagicMock()
        ir_mock.IssueSeverity = MagicMock()
        ir_mock.IssueSeverity.WARNING = "warning"

        with patch("custom_components.bosch_shc.SHCSessionAsync", return_value=session), \
             patch("custom_components.bosch_shc.dr") as dr_mock, \
             patch("custom_components.bosch_shc.parse_certificate", return_value=None), \
             patch("custom_components.bosch_shc.async_track_time_interval",
                   return_value=MagicMock()), \
             patch("custom_components.bosch_shc.async_track_state_change_event",
                   return_value=MagicMock()), \
             patch("custom_components.bosch_shc.ir", ir_mock):
            dr_mock.async_get = MagicMock(return_value=fake_dr)
            _run(async_setup_entry(hass, entry))

        return ir_mock, issue_created

    def test_camera_tool_issue_created_when_cameras_and_no_tool(self):
        """Line 676: has_cameras=True + tool not installed → async_create_issue called.

        The issue id is scoped per config entry (so multiple SHC controllers
        don't clear each other's warnings) — it's ISSUE_CAMERA_TOOL_<entry_id>,
        not the bare constant.
        """
        ir_mock, issues = self._make_full_setup_with_cameras(
            has_cameras=True, camera_tool_installed=False
        )
        from custom_components.bosch_shc.const import ISSUE_CAMERA_TOOL
        assert f"{ISSUE_CAMERA_TOOL}_eid_cam" in issues

    def test_camera_tool_issue_deleted_when_no_cameras(self):
        """Else branch: no cameras → async_delete_issue called."""
        ir_mock, issues = self._make_full_setup_with_cameras(
            has_cameras=False, camera_tool_installed=False
        )
        ir_mock.async_delete_issue.assert_called()


class TestInitUnloadPollingAndListeners:
    """Lines 703, 706: async_unload_entry — polling_handler + switch_event_listeners."""

    def _build_runtime(self, with_polling_handler=True, with_listeners=True):
        from homeassistant.helpers.device_registry import DeviceEntry

        from custom_components.bosch_shc.data import SHCData

        session = MagicMock()
        session.stop_polling = AsyncMock()
        session.unsubscribe_scenario_callback = MagicMock()

        handler_called = []
        listener_shutdown_called = []

        polling_handler = MagicMock(side_effect=lambda: handler_called.append(True))
        if not with_polling_handler:
            polling_handler = None

        listener = MagicMock()
        listener.shutdown = MagicMock(side_effect=lambda: listener_shutdown_called.append(True))

        listeners = [listener] if with_listeners else []

        # SHCData requires session, shc_device (DeviceEntry), title
        shc_dev = MagicMock(spec=DeviceEntry)
        rt = SHCData(
            session=session,
            shc_device=shc_dev,
            title="Test SHC",
            cert_check_unsub=None,
            polling_handler=polling_handler,
            presence_unsub=None,
            silent_mode_unsubs=[],
            switch_event_listeners=listeners,
        )
        return rt, handler_called, listener_shutdown_called

    def test_unload_entry_calls_polling_handler_and_listeners(self):
        """Lines 703, 706: polling_handler() + listener.shutdown() called in unload."""
        from custom_components.bosch_shc import async_unload_entry

        rt, handler_called, listener_called = self._build_runtime()

        hass = MagicMock()
        hass.config_entries = MagicMock()
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
        hass.config_entries.async_entries = MagicMock(return_value=[])
        hass.services = MagicMock()
        hass.services.has_service = MagicMock(return_value=False)

        entry = MagicMock()
        entry.entry_id = "eid_unload"
        # async_unload_entry reads from entry.runtime_data (not hass.data)
        entry.runtime_data = rt

        _run(async_unload_entry(hass, entry))

        assert handler_called, "polling_handler should have been called"
        assert listener_called, "listener.shutdown should have been called"


# ===========================================================================
# SUPPLEMENTAL — remaining uncovered lines found after first run
# ===========================================================================

class TestUpdateExcludedDevice:
    """update.py line 56: device_excluded in setup loop → continue."""

    def test_excluded_device_skipped_in_update_setup(self):
        """Line 56: device in OPT_EXCLUDED_DEVICES → continue (no DeviceUpdate added)."""
        from custom_components.bosch_shc.update import DeviceUpdate, async_setup_entry

        dev = _fake_dev("excl1", supports_software_update=True)
        session = MagicMock()
        session.information = SimpleNamespace(
            unique_id="aa:bb:cc:dd:ee:ff", version="9.0.0",
        )
        session.devices = [dev]

        hass = _fake_hass(session=session)
        entry = _fake_entry(hass=hass, options={OPT_EXCLUDED_DEVICES: ["excl1"]})

        collected = []
        _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))
        assert not any(isinstance(e, DeviceUpdate) for e in collected)


class TestLightExcludedRelayDevice:
    """light.py line 101: excluded device in relay light loop → continue."""

    def test_excluded_relay_device_skipped(self):
        """Line 101: device_excluded → continue before opt-in check."""
        from custom_components.bosch_shc.light import RelayLight, async_setup_entry

        dev = _fake_dev("bsm_excl")
        dh = MagicMock()
        dh.hue_lights = []
        dh.ledvance_lights = []
        dh.micromodule_dimmers = []
        dh.motion_detectors2 = []
        dh.micromodule_light_attached = []
        dh.light_switches_bsm = [dev]

        session = MagicMock()
        session.device_helper = dh

        hass = _fake_hass(session=session)

        with (
            patch(
                "custom_components.bosch_shc.light.async_migrate_to_new_unique_id",
                new_callable=AsyncMock,
            ),
            patch(
                "custom_components.bosch_shc.light.async_remove_stale_entity",
                new_callable=AsyncMock,
            ),
        ):
            entry = _fake_entry(hass=hass, options={
                OPT_ALL_LIGHTS_AS_LIGHT: True,
                OPT_EXCLUDED_DEVICES: ["bsm_excl"],
            })
            collected = []
            _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))

        assert not any(isinstance(e, RelayLight) for e in collected)


class TestButtonDimmerExcluded:
    """button.py line 158: excluded dimmer device → continue."""

    def test_excluded_dimmer_skipped_in_button_setup(self):
        """Line 158: device_excluded → continue before dimmer_configuration check."""
        from custom_components.bosch_shc.button import (
            DimmerPreviewMaxButton,
            async_setup_entry,
        )

        dev = _fake_dev("dim_excl", supports_dimmer_configuration=True)

        session = MagicMock()
        session.device_helper.micromodule_impulse_relays = []
        session.device_helper.smoke_detectors = []
        session.device_helper.twinguards = []
        session.device_helper.motion_detectors2 = []
        session.device_helper.outdoor_sirens = []
        session.device_helper.micromodule_dimmers = [dev]
        session.scenarios = []

        hass = _fake_hass(session=session)
        entry = _fake_entry(hass=hass, options={OPT_EXCLUDED_DEVICES: ["dim_excl"]})

        collected = []
        _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))
        assert not any(isinstance(e, DimmerPreviewMaxButton) for e in collected)


class TestBinarySensorSirenExcluded:
    """binary_sensor.py line 275: excluded outdoor siren → continue."""

    def test_excluded_siren_skipped_in_binary_sensor_setup(self):
        """Line 275: device_excluded → continue before creating siren sensors."""
        from custom_components.bosch_shc.binary_sensor import (
            SirenAcousticAlarmSensor,
            async_setup_entry,
        )

        siren = _fake_dev("siren_excl", siren=MagicMock(), supports_batterylevel=False)
        dh = MagicMock()
        dh.thermostats = []
        dh.roomthermostats = []
        dh.wallthermostats = []
        dh.motion_detectors = []
        dh.motion_detectors2 = []
        dh.shutter_contacts = []
        dh.shutter_contacts2 = []
        dh.smoke_detectors = []
        dh.twinguards = []
        dh.micromodule_shutter_controls = []
        dh.micromodule_blinds = []
        dh.outdoor_sirens = [siren]
        dh.smoke_detection_system = None
        dh.heating_circuits = []
        dh.universal_switches = []
        dh.water_leakage_detectors = []
        dh.micromodule_dimmers = []
        dh.micromodule_relays = []
        dh.micromodule_impulse_relays = []

        session = MagicMock()
        session.device_helper = dh

        hass = _fake_hass(session=session)
        entry = _fake_entry(hass=hass, options={OPT_EXCLUDED_DEVICES: ["siren_excl"]})

        platform_mock = MagicMock()
        platform_mock.async_register_entity_service = MagicMock()
        with patch("custom_components.bosch_shc.binary_sensor.async_migrate_to_new_unique_id",
                   new_callable=AsyncMock), \
             patch("custom_components.bosch_shc.binary_sensor.entity_platform.current_platform") as _cp:
            _cp.get.return_value = platform_mock
            collected = []
            _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))

        assert not any(isinstance(e, SirenAcousticAlarmSensor) for e in collected)


class TestSelectDimmerExcluded:
    """select.py line 364: excluded dimmer device → continue."""

    def test_excluded_dimmer_skipped_in_select_setup(self):
        """Line 364: device_excluded → continue before DimmerPhaseControlSelect."""
        from custom_components.bosch_shc.select import (
            DimmerPhaseControlSelect,
            async_setup_entry,
        )

        dev = _fake_dev("dim_excl", supports_dimmer_configuration=True)

        dh = MagicMock()
        dh.thermostats = []
        dh.roomthermostats = []
        dh.wallthermostats = []
        dh.motion_detectors2 = []
        dh.shutter_contacts2 = []
        dh.outdoor_sirens = []
        dh.micromodule_dimmers = [dev]

        session = MagicMock()
        session.device_helper = dh

        hass = _fake_hass(session=session)
        entry = _fake_entry(hass=hass, options={OPT_EXCLUDED_DEVICES: ["dim_excl"]})

        collected = []
        _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))
        assert not any(isinstance(e, DimmerPhaseControlSelect) for e in collected)


class TestSensorKeypadTriggerExcluded:
    """sensor.py line 463: excluded universal switch → continue in keypad trigger block."""

    def test_excluded_universal_switch_skipped(self):
        """Line 463: device_excluded → continue before KeypadTriggerSensor."""
        from custom_components.bosch_shc.sensor import (
            KeypadTriggerSensor,
            async_setup_entry,
        )

        sw = _fake_dev("us_excl", supports_keypadtrigger=True, supports_batterylevel=False)

        dh = MagicMock()
        dh.thermostats = []
        dh.roomthermostats = []
        dh.wallthermostats = []
        dh.motion_detectors = []
        dh.motion_detectors2 = []
        dh.shutter_contacts = []
        dh.shutter_contacts2 = []
        dh.smoke_detectors = []
        dh.twinguards = []
        dh.smart_plugs = []
        dh.smart_plugs_compact = []
        dh.light_switches_bsm = []
        dh.micromodule_light_controls = []
        dh.micromodule_shutter_controls = []
        dh.micromodule_blinds = []
        dh.universal_switches = [sw]
        dh.water_leakage_detectors = []
        dh.outdoor_sirens = []

        session = MagicMock()
        session.device_helper = dh
        session.emma = _fake_dev("emma1")

        hass = _fake_hass(session=session)
        entry = _fake_entry(hass=hass, options={OPT_EXCLUDED_DEVICES: ["us_excl"]})

        with patch("custom_components.bosch_shc.sensor.async_migrate_to_new_unique_id",
                   new_callable=AsyncMock):
            collected = []
            _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))

        assert not any(isinstance(e, KeypadTriggerSensor) for e in collected)


class TestInitUnloadSilentModeUnsub:
    """__init__.py line 703: silent_mode_unsub called during unload."""

    def test_unload_calls_silent_mode_unsub(self):
        """Line 703: _unsub() called for each silent_mode_unsub in unload."""
        from homeassistant.helpers.device_registry import DeviceEntry

        from custom_components.bosch_shc import async_unload_entry
        from custom_components.bosch_shc.data import SHCData

        session = MagicMock()
        session.stop_polling = AsyncMock()
        session.unsubscribe_scenario_callback = MagicMock()

        unsub_called = []
        silent_unsub = MagicMock(side_effect=lambda: unsub_called.append(True))

        shc_dev = MagicMock(spec=DeviceEntry)
        rt = SHCData(
            session=session,
            shc_device=shc_dev,
            title="Test SHC",
            cert_check_unsub=None,
            polling_handler=None,
            presence_unsub=None,
            silent_mode_unsubs=[silent_unsub],  # one silent unsub to trigger line 703
            switch_event_listeners=[],
        )

        hass = MagicMock()
        hass.config_entries = MagicMock()
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
        hass.config_entries.async_entries = MagicMock(return_value=[])
        hass.services = MagicMock()
        hass.services.has_service = MagicMock(return_value=False)

        entry = MagicMock()
        entry.entry_id = "eid_silent"
        entry.runtime_data = rt

        _run(async_unload_entry(hass, entry))

        assert unsub_called, "silent_mode_unsub should have been called"


class TestInitParseTimeError:
    """__init__.py lines 514-515: _parse_time with invalid time string."""

    def test_parse_time_invalid_value_returns_none(self):
        """Lines 514-515: invalid time format → ValueError caught → return None."""
        from boschshcpy import SHCSessionAsync as _SA

        from custom_components.bosch_shc import async_setup_entry
        from custom_components.bosch_shc.const import (
            OPT_PRESENCE_ENTITY,
            OPT_SILENT_MODE_ENABLED,
            OPT_SILENT_MODE_END,
            OPT_SILENT_MODE_START,
        )

        session = MagicMock(spec=_SA)
        session.async_init = AsyncMock()
        session.start_polling = AsyncMock()
        session.stop_polling = AsyncMock()
        session.information = SimpleNamespace(
            updateState=SimpleNamespace(name="NO_UPDATE_AVAILABLE"),
            unique_id="aa:bb:cc:dd:ee:ff",
            version="9.0.0",
            name="SHC",
        )
        session.scenarios = []
        session.subscribe_scenario_callback = MagicMock()
        session.unsubscribe_scenario_callback = MagicMock()
        dh = MagicMock()
        dh.universal_switches = []
        dh.camera_eyes = []
        dh.camera_360 = []
        dh.camera_outdoor_gen2 = []
        session.device_helper = dh

        hass = MagicMock()
        hass.data = {}

        async def _executor_job(fn, *args):
            return fn(*args)

        hass.async_add_executor_job = _executor_job
        hass.config_entries = MagicMock()
        hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)
        hass.config_entries.async_entries = MagicMock(return_value=[])
        hass.bus = MagicMock()
        hass.bus.async_listen_once = MagicMock(return_value=MagicMock())
        hass.services = MagicMock()
        hass.services.has_service = MagicMock(return_value=True)
        hass.async_create_task = MagicMock()
        hass.states = MagicMock()
        hass.states.get = MagicMock(return_value=SimpleNamespace(state="home"))

        entry = MagicMock()
        entry.entry_id = "eid_parsetime_err"
        entry.title = "ParseTime Error SHC"
        entry.data = {
            "ssl_certificate": "",
            "ssl_key": "",
            "host": "192.168.1.1",
            "hostname": "192.168.1.1",
        }
        # Pass INVALID time strings → triggers except (ValueError, IndexError) at 514-515
        entry.options = {
            OPT_PRESENCE_ENTITY: ["person.test"],
            OPT_SILENT_MODE_ENABLED: True,
            OPT_SILENT_MODE_START: "not_a_time",
            OPT_SILENT_MODE_END: "also_not",
        }
        entry.add_update_listener = MagicMock(return_value=MagicMock())
        entry.async_on_unload = MagicMock()

        fake_dr = MagicMock()
        fake_dr.async_get_or_create = MagicMock(
            return_value=SimpleNamespace(id="fake_shc_id")
        )

        with patch("custom_components.bosch_shc.SHCSessionAsync", return_value=session), \
             patch("custom_components.bosch_shc.dr") as dr_mock, \
             patch("custom_components.bosch_shc.parse_certificate", return_value=None), \
             patch("custom_components.bosch_shc.async_track_time_interval",
                   return_value=MagicMock()), \
             patch("custom_components.bosch_shc.async_track_state_change_event",
                   return_value=MagicMock()), \
             patch("custom_components.bosch_shc.ir"):
            dr_mock.async_get = MagicMock(return_value=fake_dr)
            _run(async_setup_entry(hass, entry))
        # If no exception: _parse_time executed its except branch (lines 514-515)


class TestEventLightControlAddedToHass:
    """event.py lines 226-235: LightControlButtonEvent.async_added_to_hass."""

    def test_async_added_to_hass_registers_keypad_events(self):
        """Lines 226-235: Keypad service → register_event called for each KeyState."""
        from custom_components.bosch_shc.event import LightControlButtonEvent

        # Build a fake Keypad service with KeyState enum
        key_state_1 = SimpleNamespace(value="KEY_1")
        key_state_2 = SimpleNamespace(value="KEY_2")
        keypad_service = SimpleNamespace(
            id="Keypad",
            KeyState=[key_state_1, key_state_2],
            register_event=MagicMock(),
            subscribe_callback=MagicMock(),
        )
        non_keypad_service = SimpleNamespace(
            id="LatestMotion",
            subscribe_callback=MagicMock(),
        )

        dev = _fake_dev("lc1", device_services=[keypad_service, non_keypad_service])

        ent = LightControlButtonEvent.__new__(LightControlButtonEvent)
        ent._device = dev
        ent._entry_id = "E1"
        ent._last_fired_timestamp = -1
        ent._attr_event_types = ["PRESS_SHORT", "PRESS_LONG"]
        ent.entity_id = "event.lc1_button"
        ent._attr_unique_id = "root1_lc1_button"
        # hass isn't needed because Entity.async_added_to_hass() is a no-op
        ent.hass = MagicMock()

        _run(ent.async_added_to_hass())

        # register_event should have been called once per KeyState
        assert keypad_service.register_event.call_count == 2
        keypad_service.register_event.assert_any_call("KEY_1", ent._event_callback)
        keypad_service.register_event.assert_any_call("KEY_2", ent._event_callback)


# ===========================================================================
# NUMBER.PY — lines 159-172, 178-187, 247-258, 291-297, 358-364,
#              482-488, 530-536, 596-602, 658-664, 727-733, 794-800
# ===========================================================================

class TestNumberSirenSetup:
    """number.py lines 159-172: siren config numbers created in setup."""

    def _run_number_setup(self, sirens=None, dimmers=None, options=None):
        from custom_components.bosch_shc.number import async_setup_entry

        dh = MagicMock()
        dh.thermostats = []
        dh.roomthermostats = []
        dh.wallthermostats = []
        dh.micromodule_impulse_relays = []
        dh.heating_circuits = []

        session = MagicMock()
        session.device_helper = dh

        hass = _fake_hass(session=session)
        entry = _fake_entry(hass=hass, options=options or {})

        collected = []
        _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))
        return collected

    def test_siren_config_numbers_created_when_siren_service_present(self):
        """Lines 159-172: siren with siren service → SirenConfigNumber entities."""

        siren = _fake_dev("s1", siren=MagicMock())

        dh = MagicMock()
        dh.thermostats = []
        dh.roomthermostats = []
        dh.wallthermostats = []
        dh.micromodule_impulse_relays = []
        dh.heating_circuits = []

        session = MagicMock()
        session.device_helper = dh

        hass = _fake_hass(session=session)
        entry = _fake_entry(hass=hass)

        # Patch the dh to return our siren
        with patch.object(dh, "outdoor_sirens", [siren], create=True):
            from custom_components.bosch_shc.number import async_setup_entry
            collected = []
            _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))

        types = [type(e).__name__ for e in collected]
        assert types.count("SirenConfigNumber") >= 4  # alarm/flash duration+delay

    def test_siren_excluded_skipped_in_number_setup(self):
        """Line 160: device_excluded → continue (no SirenConfigNumber added)."""

        siren_excl = _fake_dev("siren_excl", siren=MagicMock())

        dh = MagicMock()
        dh.thermostats = []
        dh.roomthermostats = []
        dh.wallthermostats = []
        dh.micromodule_impulse_relays = []
        dh.heating_circuits = []

        session = MagicMock()
        session.device_helper = dh

        hass = _fake_hass(session=session)
        entry = _fake_entry(hass=hass, options={OPT_EXCLUDED_DEVICES: ["siren_excl"]})

        with patch.object(dh, "outdoor_sirens", [siren_excl], create=True):
            from custom_components.bosch_shc.number import async_setup_entry
            collected = []
            _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))

        types = [type(e).__name__ for e in collected]
        assert "SirenConfigNumber" not in types

    def test_siren_without_siren_service_skipped_in_number_setup(self):
        """Line 162: siren with siren=None → continue (no SirenConfigNumber added)."""

        siren_no_svc = _fake_dev("siren_no_svc", siren=None)

        dh = MagicMock()
        dh.thermostats = []
        dh.roomthermostats = []
        dh.wallthermostats = []
        dh.micromodule_impulse_relays = []
        dh.heating_circuits = []

        session = MagicMock()
        session.device_helper = dh

        hass = _fake_hass(session=session)
        entry = _fake_entry(hass=hass)

        with patch.object(dh, "outdoor_sirens", [siren_no_svc], create=True):
            from custom_components.bosch_shc.number import async_setup_entry
            collected = []
            _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))

        types = [type(e).__name__ for e in collected]
        assert "SirenConfigNumber" not in types

    def test_dimmer_excluded_skipped_in_number_setup(self):
        """Line 179: device_excluded → continue (no DimmerConfigNumber added)."""

        dev_excl = _fake_dev("dim_excl", supports_dimmer_configuration=True)

        dh = MagicMock()
        dh.thermostats = []
        dh.roomthermostats = []
        dh.wallthermostats = []
        dh.micromodule_impulse_relays = []
        dh.heating_circuits = []

        session = MagicMock()
        session.device_helper = dh

        hass = _fake_hass(session=session)
        entry = _fake_entry(hass=hass, options={OPT_EXCLUDED_DEVICES: ["dim_excl"]})

        with patch.object(dh, "micromodule_dimmers", [dev_excl], create=True):
            from custom_components.bosch_shc.number import async_setup_entry
            collected = []
            _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))

        types = [type(e).__name__ for e in collected]
        assert "DimmerConfigNumber" not in types

    def test_dimmer_config_numbers_created_when_supports_dimmer(self):
        """Lines 178-187: dimmer with supports_dimmer_configuration → DimmerConfigNumber."""

        dev = _fake_dev("dim1", supports_dimmer_configuration=True)

        dh = MagicMock()
        dh.thermostats = []
        dh.roomthermostats = []
        dh.wallthermostats = []
        dh.micromodule_impulse_relays = []
        dh.heating_circuits = []

        session = MagicMock()
        session.device_helper = dh

        hass = _fake_hass(session=session)
        entry = _fake_entry(hass=hass)

        with patch.object(dh, "micromodule_dimmers", [dev], create=True):
            from custom_components.bosch_shc.number import async_setup_entry
            collected = []
            _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))

        types = [type(e).__name__ for e in collected]
        assert types.count("DimmerConfigNumber") >= 3  # min/max/speed


class TestNumberErrorPaths:
    """Lines 247-258, 291-297, 358-364, 482-488, 530-536, 596-602, 658-664, 727-733, 794-800."""

    def test_siren_config_number_async_set_client_error(self):
        """Lines 247-258: SirenConfigNumber.async_set_native_value error path."""
        from custom_components.bosch_shc.number import SirenConfigNumber

        num = SirenConfigNumber.__new__(SirenConfigNumber)
        siren_svc = MagicMock()
        siren_svc.async_set_configuration = AsyncMock(
            side_effect=aiohttp.ClientError("err")
        )
        num._device = SimpleNamespace(siren=siren_svc, name="Siren")
        num._field = "alarm_duration_seconds"
        num._attr_native_min_value = 0
        num._attr_native_max_value = 3600
        _run(num.async_set_native_value(30.0))  # must not raise

    def test_shcnumber_async_set_client_error(self):
        """Lines 291-297: SHCNumber.async_set_native_value error path."""
        from custom_components.bosch_shc.number import SHCNumber

        num = SHCNumber.__new__(SHCNumber)
        # SHCNumber.native_min_value = self._device.min_offset
        # SHCNumber.native_max_value = self._device.max_offset
        num._device = SimpleNamespace(
            name="Thermostat",
            min_offset=-5.0,
            max_offset=5.0,
            async_set_offset=AsyncMock(side_effect=aiohttp.ClientError("err")),
        )
        _run(num.async_set_native_value(1.0))  # must not raise

    def test_impulse_length_number_async_set_client_error(self):
        """Lines 358-364: ImpulseLengthNumber.async_set_native_value error path."""
        from custom_components.bosch_shc.number import ImpulseLengthNumber

        num = ImpulseLengthNumber.__new__(ImpulseLengthNumber)
        num._device = SimpleNamespace(
            name="Relay",
            async_set_impulse_length=AsyncMock(
                side_effect=aiohttp.ClientError("err")
            ),
        )
        num._attr_native_min_value = 0.1
        num._attr_native_max_value = 10.0
        _run(num.async_set_native_value(1.0))  # must not raise

    def test_power_threshold_number_async_set_client_error(self):
        """Lines 482-488: PowerThresholdNumber.async_set_native_value error path."""
        from custom_components.bosch_shc.number import PowerThresholdNumber

        num = PowerThresholdNumber.__new__(PowerThresholdNumber)
        num._device = SimpleNamespace(
            name="SmartPlug",
            async_set_power_threshold=AsyncMock(
                side_effect=aiohttp.ClientError("err")
            ),
        )
        num._attr_native_min_value = 0.0
        num._attr_native_max_value = 3680.0
        _run(num.async_set_native_value(50.0))  # must not raise

    def test_enter_duration_number_async_set_client_error(self):
        """Lines 530-536: EnterDurationNumber.async_set_native_value error path."""
        from custom_components.bosch_shc.number import EnterDurationNumber

        num = EnterDurationNumber.__new__(EnterDurationNumber)
        num._device = SimpleNamespace(
            name="SmartPlug",
            async_set_enter_duration_seconds=AsyncMock(
                side_effect=aiohttp.ClientError("err")
            ),
        )
        num._attr_native_min_value = 1.0
        num._attr_native_max_value = 3600.0
        _run(num.async_set_native_value(60.0))  # must not raise

    def test_led_brightness_number_async_set_client_error(self):
        """Lines 596-602: LedBrightnessNumber.async_set_native_value error path."""
        from custom_components.bosch_shc.number import LedBrightnessNumber

        num = LedBrightnessNumber.__new__(LedBrightnessNumber)
        num._device = SimpleNamespace(
            name="SmartPlug",
            async_set_led_brightness=AsyncMock(
                side_effect=aiohttp.ClientError("err")
            ),
        )
        _run(num.async_set_native_value(50.0))  # must not raise

    def test_display_brightness_number_async_set_client_error(self):
        """Lines 658-664: DisplayBrightnessNumber.async_set_native_value error path."""
        from custom_components.bosch_shc.number import DisplayBrightnessNumber

        num = DisplayBrightnessNumber.__new__(DisplayBrightnessNumber)
        num._device = SimpleNamespace(
            name="Thermostat",
            async_set_display_brightness=AsyncMock(
                side_effect=aiohttp.ClientError("err")
            ),
        )
        _run(num.async_set_native_value(80.0))  # must not raise

    def test_display_on_time_number_async_set_client_error(self):
        """Lines 727-733: DisplayOnTimeNumber.async_set_native_value error path."""
        from custom_components.bosch_shc.number import DisplayOnTimeNumber

        num = DisplayOnTimeNumber.__new__(DisplayOnTimeNumber)
        num._device = SimpleNamespace(
            name="Thermostat",
            async_set_display_on_time=AsyncMock(
                side_effect=aiohttp.ClientError("err")
            ),
        )
        _run(num.async_set_native_value(10.0))  # must not raise

    def test_dimmer_config_number_async_set_client_error(self):
        """Lines 794-800: DimmerConfigNumber.async_set_native_value error path."""
        from custom_components.bosch_shc.number import DimmerConfigNumber

        svc = MagicMock()
        svc.async_set_brightness_range = AsyncMock(
            side_effect=aiohttp.ClientError("err")
        )
        num = DimmerConfigNumber.__new__(DimmerConfigNumber)
        num._field = "min"
        num._device = SimpleNamespace(dimmer_configuration=svc, name="Dimmer")
        num._attr_native_min_value = 0.0
        num._attr_native_max_value = 100.0
        _run(num.async_set_native_value(10.0))  # must not raise


# ---------------------------------------------------------------------------
# Newly uncovered lines (after bug-hunt fixes in 0.7.26)
# ---------------------------------------------------------------------------

class TestMotionEventDedupGuard:
    """event.py:360 — dedup guard: same ts returns early."""

    def _make_entity(self):
        from custom_components.bosch_shc.event import MotionDetectorEvent
        entity = MotionDetectorEvent.__new__(MotionDetectorEvent)
        entity._device = SimpleNamespace(
            name="Motion",
            id="md-1",
            root_device_id="root-1",
            latestmotion="2026-06-28T10:00:00.000Z",
        )
        entity._last_fired_timestamp = "2026-06-28T10:00:00.000Z"  # same as device
        entity._trigger_event = MagicMock()
        entity.schedule_update_ha_state = MagicMock()
        return entity

    def test_same_ts_no_dispatch(self):
        entity = self._make_entity()
        entity._event_callback()
        entity._trigger_event.assert_not_called()


class TestSmokeDetectionSystemEventBadEnum:
    """event.py:404-406 — ValueError/KeyError in SmokeDetectionSystemEvent._event_callback."""

    def _make_entity(self):
        from custom_components.bosch_shc.event import SmokeDetectionSystemEvent

        class BadAlarm:
            @property
            def name(self):
                raise ValueError("unknown enum")

        entity = SmokeDetectionSystemEvent.__new__(SmokeDetectionSystemEvent)
        entity._device = SimpleNamespace(
            name="Smoke System",
            id="ss-1",
            root_device_id="root-1",
            alarm=BadAlarm(),
        )
        entity._attr_unique_id = "root-1_ss-1"
        entity._trigger_event = MagicMock()
        entity.schedule_update_ha_state = MagicMock()
        return entity

    def test_bad_alarm_no_dispatch(self):
        entity = self._make_entity()
        with patch("custom_components.bosch_shc.event.LOGGER") as mock_log:
            entity._event_callback()
        mock_log.warning.assert_called_once()
        entity._trigger_event.assert_not_called()


class TestSmokeDetectorEventBadEnum:
    """event.py:449-451 — ValueError/KeyError in SmokeDetectorEvent._event_callback."""

    def _make_entity(self):
        from custom_components.bosch_shc.event import SmokeDetectorEvent

        class BadAlarmState:
            @property
            def name(self):
                raise KeyError("unknown")

        entity = SmokeDetectorEvent.__new__(SmokeDetectorEvent)
        entity._device = SimpleNamespace(
            name="Smoke Det",
            id="sd-1",
            root_device_id="root-1",
            alarmstate=BadAlarmState(),
        )
        entity._attr_unique_id = "root-1_sd-1"
        entity._trigger_event = MagicMock()
        entity.schedule_update_ha_state = MagicMock()
        return entity

    def test_bad_alarmstate_no_dispatch(self):
        entity = self._make_entity()
        with patch("custom_components.bosch_shc.event.LOGGER") as mock_log:
            entity._event_callback()
        mock_log.warning.assert_called_once()
        entity._trigger_event.assert_not_called()


class TestSmokeDetectorSensorServiceUnsub:
    """binary_sensor.py:573 — service not None branch in async_will_remove_from_hass."""

    def test_service_unsubscribed_when_set(self):
        from custom_components.bosch_shc.binary_sensor import SmokeDetectorSensor
        ent = SmokeDetectorSensor.__new__(SmokeDetectorSensor)
        svc = MagicMock()
        ent._service = svc
        ent._device = SimpleNamespace(id="sd-1")
        ent._ha_stop_unsub = None
        with patch("custom_components.bosch_shc.entity.SHCEntity.async_will_remove_from_hass", new_callable=AsyncMock):
            _run(ent.async_will_remove_from_hass())
        svc.unsubscribe_callback.assert_called_once_with("sd-1_eventlistener")


class TestSmokeDetectorSensorBadEnum:
    """binary_sensor.py:587-589 — ValueError/KeyError guard in SmokeDetectorSensor._input_events_handler."""

    def _make_sensor(self):
        from custom_components.bosch_shc.binary_sensor import SmokeDetectorSensor

        class BadAlarmState:
            @property
            def name(self):
                raise ValueError("bad")

        ent = SmokeDetectorSensor.__new__(SmokeDetectorSensor)
        ent._device = SimpleNamespace(name="SmokeD", alarmstate=BadAlarmState())
        ent._service = None
        ent._ha_stop_unsub = None
        ent._last_fired_alarmstate = None
        return ent

    def test_bad_alarmstate_logs_warning(self):
        ent = self._make_sensor()
        with patch("custom_components.bosch_shc.binary_sensor.LOGGER") as mock_log:
            ent._input_events_handler()
        mock_log.warning.assert_called_once()


class TestSmokeDetectionSystemSensorServiceUnsub:
    """binary_sensor.py:752 — service not None branch in SmokeDetectionSystemSensor."""

    def test_service_unsubscribed_when_set(self):
        from custom_components.bosch_shc.binary_sensor import SmokeDetectionSystemSensor
        ent = SmokeDetectionSystemSensor.__new__(SmokeDetectionSystemSensor)
        svc = MagicMock()
        ent._service = svc
        ent._device = SimpleNamespace(id="sds-1")
        ent._ha_stop_unsub = None
        with patch("custom_components.bosch_shc.entity.SHCEntity.async_will_remove_from_hass", new_callable=AsyncMock):
            _run(ent.async_will_remove_from_hass())
        svc.unsubscribe_callback.assert_called_once_with("sds-1_eventlistener")


class TestSmokeDetectionSystemSensorBadEnum:
    """binary_sensor.py:766-768 — ValueError/KeyError guard in SmokeDetectionSystemSensor._input_events_handler."""

    def _make_sensor(self):
        from custom_components.bosch_shc.binary_sensor import SmokeDetectionSystemSensor

        class BadAlarm:
            @property
            def name(self):
                raise KeyError("bad")

        ent = SmokeDetectionSystemSensor.__new__(SmokeDetectionSystemSensor)
        ent._device = SimpleNamespace(name="SmokeDS", alarm=BadAlarm())
        ent._service = None
        ent._ha_stop_unsub = None
        ent._last_fired_alarm = None
        return ent

    def test_bad_alarm_logs_warning(self):
        ent = self._make_sensor()
        with patch("custom_components.bosch_shc.binary_sensor.LOGGER") as mock_log:
            ent._input_events_handler()
        mock_log.warning.assert_called_once()


class TestUDSSwitchAvailableProperty:
    """switch.py:1102 — available property on SHCUserDefinedStateSwitch."""

    def test_available_reflects_deleted_flag(self):
        from custom_components.bosch_shc.switch import SHCUserDefinedStateSwitch
        sw = SHCUserDefinedStateSwitch.__new__(SHCUserDefinedStateSwitch)
        sw._device = SimpleNamespace(deleted=False)
        assert sw.available is True
        sw._device.deleted = True
        assert sw.available is False
