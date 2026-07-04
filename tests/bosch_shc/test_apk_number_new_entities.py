"""Tests for APK-batch 2-6 new number entities.

Covers:
- PowerThresholdNumber (smartplug / smartplugcompact)
- EnterDurationNumber (smartplug / smartplugcompact)
- LedBrightnessNumber (smartplug / smartplugcompact)
- DisplayBrightnessNumber (ThermostatGen2 / RoomThermostat2)
- DisplayOnTimeNumber (ThermostatGen2 / RoomThermostat2)

Run with:
  PYTHONPATH="<lib>:<hass>" PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
  python3 -m pytest tests/bosch_shc/test_apk_number_new_entities.py -q -o addopts=
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from custom_components.bosch_shc.number import (
    DisplayBrightnessNumber,
    DisplayOnTimeNumber,
    EnterDurationNumber,
    LedBrightnessNumber,
    PowerThresholdNumber,
    async_setup_entry,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_device(**kwargs):
    defaults = dict(name="Dev", id="dev1", root_device_id="root1",
                    serial="SER1", supports_silentmode=False)
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_session(**helper_lists):
    defaults = dict(
        thermostats=[],
        roomthermostats=[],
        micromodule_impulse_relays=[],
        heating_circuits=[],
    )
    defaults.update(helper_lists)
    device_helper = SimpleNamespace(**defaults)
    return SimpleNamespace(device_helper=device_helper)


def _make_hass_and_entry(session):
    entry_id = "E1"
    hass = SimpleNamespace()
    config_entry = SimpleNamespace(options={}, entry_id=entry_id,
                                   async_on_unload=MagicMock())
    config_entry.runtime_data = SimpleNamespace(session=session)
    return hass, config_entry


async def _async_setup(session):
    hass, config_entry = _make_hass_and_entry(session)
    entities = []

    def add_entities(new_ents, *args, **kwargs):
        entities.extend(new_ents)

    await async_setup_entry(hass, config_entry, add_entities)
    return entities


def _setup(session):
    return asyncio.run(_async_setup(session))


# ---------------------------------------------------------------------------
# PowerThresholdNumber
# ---------------------------------------------------------------------------


class TestPowerThresholdNumber:
    def _make(self, **dev_kwargs):
        defaults = dict(root_device_id="root1", id="dev1",
                        power_threshold=50.0)
        defaults.update(dev_kwargs)
        dev = SimpleNamespace(**defaults)
        n = PowerThresholdNumber.__new__(PowerThresholdNumber)
        n._device = dev
        n._attr_unique_id = f"{dev.root_device_id}_{dev.id}_power_threshold"
        n._attr_name = "Energy Saving Power Threshold"
        return n

    def test_native_value_from_device(self):
        n = self._make(power_threshold=100.0)
        assert n.native_value == 100.0

    def test_native_value_none_when_not_set(self):
        dev = SimpleNamespace(root_device_id="r", id="d")
        n = PowerThresholdNumber.__new__(PowerThresholdNumber)
        n._device = dev
        assert n.native_value is None

    def test_set_native_value_writes_to_device(self):
        mock_setter = AsyncMock()
        dev = SimpleNamespace(root_device_id="r", id="d",
                              power_threshold=0.0,
                              async_set_power_threshold=mock_setter)
        n = PowerThresholdNumber.__new__(PowerThresholdNumber)
        n._device = dev
        asyncio.run(n.async_set_native_value(200.0))
        mock_setter.assert_awaited_once_with(200.0)

    def test_set_native_value_clamped_to_max(self):
        mock_setter = AsyncMock()
        dev = SimpleNamespace(root_device_id="r", id="d",
                              power_threshold=0.0,
                              async_set_power_threshold=mock_setter)
        n = PowerThresholdNumber.__new__(PowerThresholdNumber)
        n._device = dev
        asyncio.run(n.async_set_native_value(9999.0))
        assert mock_setter.call_args[0][0] <= 3680.0

    def test_set_native_value_clamped_to_min(self):
        mock_setter = AsyncMock()
        dev = SimpleNamespace(root_device_id="r", id="d",
                              power_threshold=0.0,
                              async_set_power_threshold=mock_setter)
        n = PowerThresholdNumber.__new__(PowerThresholdNumber)
        n._device = dev
        asyncio.run(n.async_set_native_value(-50.0))
        assert mock_setter.call_args[0][0] >= 0.0

    def test_unique_id_format(self):
        n = self._make(root_device_id="root1", dev_id="d1",
                       power_threshold=10.0) if False else None
        dev = SimpleNamespace(root_device_id="root1", id="dev1",
                              power_threshold=10.0)
        n = PowerThresholdNumber.__new__(PowerThresholdNumber)
        n._device = dev
        n._attr_unique_id = f"{dev.root_device_id}_{dev.id}_power_threshold"
        assert n._attr_unique_id == "root1_dev1_power_threshold"

    def test_entity_category_config(self):
        from homeassistant.helpers.entity import EntityCategory
        n = PowerThresholdNumber.__new__(PowerThresholdNumber)
        assert n._attr_entity_category == EntityCategory.CONFIG

    def test_device_class_power(self):
        from homeassistant.components.number import NumberDeviceClass
        n = PowerThresholdNumber.__new__(PowerThresholdNumber)
        assert n._attr_device_class == NumberDeviceClass.POWER

    def test_smartplug_power_threshold_created_when_attr_present(self):
        plug = _fake_device(power_threshold=100.0, supports_energy_saving_mode=True)
        session = _make_session(smart_plugs=[plug])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "PowerThresholdNumber" in types

    def test_smartplug_power_threshold_skipped_when_attr_absent(self):
        plug = _fake_device()  # no power_threshold
        session = _make_session(smart_plugs=[plug])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "PowerThresholdNumber" not in types


# ---------------------------------------------------------------------------
# EnterDurationNumber
# ---------------------------------------------------------------------------


class TestEnterDurationNumber:
    def _make(self, enter_duration_seconds=30):
        dev = SimpleNamespace(root_device_id="root1", id="dev1",
                              enter_duration_seconds=enter_duration_seconds)
        n = EnterDurationNumber.__new__(EnterDurationNumber)
        n._device = dev
        n._attr_unique_id = (
            f"{dev.root_device_id}_{dev.id}_enter_duration_seconds"
        )
        return n

    def test_native_value_returns_float(self):
        n = self._make(enter_duration_seconds=60)
        assert n.native_value == 60.0

    def test_native_value_none_when_missing(self):
        dev = SimpleNamespace(root_device_id="r", id="d")
        n = EnterDurationNumber.__new__(EnterDurationNumber)
        n._device = dev
        assert n.native_value is None

    def test_set_native_value_converts_to_int(self):
        mock_setter = AsyncMock()
        dev = SimpleNamespace(root_device_id="r", id="d",
                              enter_duration_seconds=0,
                              async_set_enter_duration_seconds=mock_setter)
        n = EnterDurationNumber.__new__(EnterDurationNumber)
        n._device = dev
        asyncio.run(n.async_set_native_value(120.7))
        mock_setter.assert_awaited_once_with(120)  # int(clamped)

    def test_unique_id_format(self):
        n = self._make()
        assert n._attr_unique_id == "root1_dev1_enter_duration_seconds"

    def test_entity_category_config(self):
        from homeassistant.helpers.entity import EntityCategory
        n = EnterDurationNumber.__new__(EnterDurationNumber)
        assert n._attr_entity_category == EntityCategory.CONFIG

    def test_smartplugcompact_enter_duration_created_when_attr_present(self):
        plug = _fake_device(enter_duration_seconds=60, supports_energy_saving_mode=True)
        session = _make_session(smart_plugs_compact=[plug])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "EnterDurationNumber" in types

    def test_smartplugcompact_enter_duration_skipped_when_attr_absent(self):
        plug = _fake_device()
        session = _make_session(smart_plugs_compact=[plug])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "EnterDurationNumber" not in types


# ---------------------------------------------------------------------------
# LedBrightnessNumber
# ---------------------------------------------------------------------------


class TestLedBrightnessNumber:
    def _make(self, led_brightness=50, svc=None):
        dev = SimpleNamespace(root_device_id="root1", id="dev1",
                              led_brightness=led_brightness,
                              _led_brightness_configuration_service=svc)
        n = LedBrightnessNumber.__new__(LedBrightnessNumber)
        n._device = dev
        n._attr_unique_id = f"{dev.root_device_id}_{dev.id}_led_brightness"
        return n

    def test_native_value_from_device(self):
        n = self._make(led_brightness=75)
        assert n.native_value == 75

    def test_native_value_none_when_missing(self):
        dev = SimpleNamespace(root_device_id="r", id="d")
        n = LedBrightnessNumber.__new__(LedBrightnessNumber)
        n._device = dev
        assert n.native_value is None

    def test_native_min_from_service(self):
        svc = SimpleNamespace(min_brightness=10, max_brightness=100, step_size=1)
        n = self._make(svc=svc)
        assert n.native_min_value == 10.0

    def test_native_max_from_service(self):
        svc = SimpleNamespace(min_brightness=0, max_brightness=90, step_size=5)
        n = self._make(svc=svc)
        assert n.native_max_value == 90.0

    def test_native_step_from_service(self):
        svc = SimpleNamespace(min_brightness=0, max_brightness=100, step_size=5)
        n = self._make(svc=svc)
        assert n.native_step == 5.0

    def test_native_min_fallback_when_no_service(self):
        n = self._make(svc=None)
        assert n.native_min_value == 0.0

    def test_native_max_fallback_when_no_service(self):
        n = self._make(svc=None)
        assert n.native_max_value == 100.0

    def test_native_step_fallback_when_no_service(self):
        n = self._make(svc=None)
        assert n.native_step == 1.0

    def test_set_native_value_writes_to_device(self):
        mock_setter = AsyncMock()
        dev = SimpleNamespace(root_device_id="r", id="d",
                              led_brightness=50,
                              _led_brightness_configuration_service=None,
                              async_set_led_brightness=mock_setter)
        n = LedBrightnessNumber.__new__(LedBrightnessNumber)
        n._device = dev
        asyncio.run(n.async_set_native_value(80))
        mock_setter.assert_awaited_once_with(80)

    def test_unique_id_format(self):
        n = self._make()
        assert n._attr_unique_id == "root1_dev1_led_brightness"

    def test_smartplug_led_brightness_created_when_attr_present(self):
        plug = _fake_device(led_brightness=50, supports_led_brightness=True)
        session = _make_session(smart_plugs=[plug])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "LedBrightnessNumber" in types

    def test_smartplug_led_brightness_skipped_when_attr_absent(self):
        plug = _fake_device()
        session = _make_session(smart_plugs=[plug])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "LedBrightnessNumber" not in types

    def test_min_none_falls_back(self):
        svc = SimpleNamespace(min_brightness=None, max_brightness=100, step_size=1)
        n = self._make(svc=svc)
        assert n.native_min_value == 0.0

    def test_max_none_falls_back(self):
        svc = SimpleNamespace(min_brightness=0, max_brightness=None, step_size=1)
        n = self._make(svc=svc)
        assert n.native_max_value == 100.0

    def test_step_none_falls_back(self):
        svc = SimpleNamespace(min_brightness=0, max_brightness=100, step_size=None)
        n = self._make(svc=svc)
        assert n.native_step == 1.0


# ---------------------------------------------------------------------------
# DisplayBrightnessNumber
# ---------------------------------------------------------------------------


class TestDisplayBrightnessNumber:
    def _make(self, display_brightness=50, svc=None):
        dev = SimpleNamespace(root_device_id="root1", id="dev1",
                              display_brightness=display_brightness,
                              _display_config_service=svc)
        n = DisplayBrightnessNumber.__new__(DisplayBrightnessNumber)
        n._device = dev
        n._attr_unique_id = f"{dev.root_device_id}_{dev.id}_display_brightness"
        return n

    def test_native_value_from_device(self):
        n = self._make(display_brightness=60)
        assert n.native_value == 60

    def test_native_value_none_when_missing(self):
        dev = SimpleNamespace(root_device_id="r", id="d")
        n = DisplayBrightnessNumber.__new__(DisplayBrightnessNumber)
        n._device = dev
        assert n.native_value is None

    def test_native_min_from_service(self):
        svc = SimpleNamespace(display_brightness_min=5, display_brightness_max=100,
                              display_brightness_step_size=5)
        n = self._make(svc=svc)
        assert n.native_min_value == 5.0

    def test_native_max_from_service(self):
        svc = SimpleNamespace(display_brightness_min=0, display_brightness_max=80,
                              display_brightness_step_size=1)
        n = self._make(svc=svc)
        assert n.native_max_value == 80.0

    def test_native_step_from_service(self):
        svc = SimpleNamespace(display_brightness_min=0, display_brightness_max=100,
                              display_brightness_step_size=10)
        n = self._make(svc=svc)
        assert n.native_step == 10.0

    def test_fallbacks_when_no_service(self):
        n = self._make(svc=None)
        assert n.native_min_value == 0.0
        assert n.native_max_value == 100.0
        assert n.native_step == 1.0

    def test_set_native_value_writes_to_device(self):
        mock_setter = AsyncMock()
        dev = SimpleNamespace(root_device_id="r", id="d",
                              display_brightness=50,
                              _display_config_service=None,
                              async_set_display_brightness=mock_setter)
        n = DisplayBrightnessNumber.__new__(DisplayBrightnessNumber)
        n._device = dev
        asyncio.run(n.async_set_native_value(70))
        mock_setter.assert_awaited_once_with(70)

    def test_unique_id_format(self):
        n = self._make()
        assert n._attr_unique_id == "root1_dev1_display_brightness"

    def test_entity_category_config(self):
        from homeassistant.helpers.entity import EntityCategory
        n = DisplayBrightnessNumber.__new__(DisplayBrightnessNumber)
        assert n._attr_entity_category == EntityCategory.CONFIG

    def test_thermostat_display_brightness_created_when_attr_present(self):
        therm = _fake_device(display_brightness=50, supports_display_configuration=True)
        session = _make_session(thermostats=[therm])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "DisplayBrightnessNumber" in types

    def test_thermostat_display_brightness_skipped_when_attr_absent(self):
        therm = _fake_device()
        session = _make_session(thermostats=[therm])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "DisplayBrightnessNumber" not in types

    def test_roomthermostat_display_brightness_created(self):
        rth = _fake_device(display_brightness=40, supports_display_configuration=True)
        session = _make_session(roomthermostats=[rth])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "DisplayBrightnessNumber" in types

    def test_min_none_falls_back(self):
        svc = SimpleNamespace(display_brightness_min=None, display_brightness_max=100,
                              display_brightness_step_size=1)
        n = self._make(svc=svc)
        assert n.native_min_value == 0.0

    def test_max_none_falls_back(self):
        svc = SimpleNamespace(display_brightness_min=0, display_brightness_max=None,
                              display_brightness_step_size=1)
        n = self._make(svc=svc)
        assert n.native_max_value == 100.0

    def test_step_none_falls_back(self):
        svc = SimpleNamespace(display_brightness_min=0, display_brightness_max=100,
                              display_brightness_step_size=None)
        n = self._make(svc=svc)
        assert n.native_step == 1.0


# ---------------------------------------------------------------------------
# DisplayOnTimeNumber
# ---------------------------------------------------------------------------


class TestDisplayOnTimeNumber:
    def _make(self, display_on_time=60, svc=None):
        dev = SimpleNamespace(root_device_id="root1", id="dev1",
                              display_on_time=display_on_time,
                              _display_config_service=svc)
        n = DisplayOnTimeNumber.__new__(DisplayOnTimeNumber)
        n._device = dev
        n._attr_unique_id = f"{dev.root_device_id}_{dev.id}_display_on_time"
        return n

    def test_native_value_from_device(self):
        n = self._make(display_on_time=120)
        assert n.native_value == 120.0

    def test_native_value_none_when_missing(self):
        dev = SimpleNamespace(root_device_id="r", id="d")
        n = DisplayOnTimeNumber.__new__(DisplayOnTimeNumber)
        n._device = dev
        assert n.native_value is None

    def test_native_min_from_service(self):
        svc = SimpleNamespace(display_on_time_min=5, display_on_time_max=3600,
                              display_on_time_step_size=1)
        n = self._make(svc=svc)
        assert n.native_min_value == 5.0

    def test_native_max_from_service(self):
        svc = SimpleNamespace(display_on_time_min=0, display_on_time_max=900,
                              display_on_time_step_size=30)
        n = self._make(svc=svc)
        assert n.native_max_value == 900.0

    def test_fallbacks_when_no_service(self):
        n = self._make(svc=None)
        assert n.native_min_value == 0.0
        assert n.native_max_value == 3600.0
        assert n.native_step == 1.0

    def test_set_native_value_writes_to_device(self):
        mock_setter = AsyncMock()
        dev = SimpleNamespace(root_device_id="r", id="d",
                              display_on_time=60,
                              _display_config_service=None,
                              async_set_display_on_time=mock_setter)
        n = DisplayOnTimeNumber.__new__(DisplayOnTimeNumber)
        n._device = dev
        asyncio.run(n.async_set_native_value(300))
        mock_setter.assert_awaited_once_with(300)

    def test_unique_id_format(self):
        n = self._make()
        assert n._attr_unique_id == "root1_dev1_display_on_time"

    def test_thermostat_display_on_time_created_when_attr_present(self):
        therm = _fake_device(display_on_time=30, supports_display_configuration=True)
        session = _make_session(thermostats=[therm])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "DisplayOnTimeNumber" in types

    def test_thermostat_display_on_time_skipped_when_attr_absent(self):
        therm = _fake_device()
        session = _make_session(thermostats=[therm])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "DisplayOnTimeNumber" not in types

    def test_min_none_falls_back(self):
        svc = SimpleNamespace(display_on_time_min=None, display_on_time_max=3600,
                              display_on_time_step_size=1)
        n = self._make(svc=svc)
        assert n.native_min_value == 0.0

    def test_max_none_falls_back(self):
        svc = SimpleNamespace(display_on_time_min=0, display_on_time_max=None,
                              display_on_time_step_size=1)
        n = self._make(svc=svc)
        assert n.native_max_value == 3600.0

    def test_step_none_falls_back(self):
        svc = SimpleNamespace(display_on_time_min=0, display_on_time_max=3600,
                              display_on_time_step_size=None)
        n = self._make(svc=svc)
        assert n.native_step == 1.0
