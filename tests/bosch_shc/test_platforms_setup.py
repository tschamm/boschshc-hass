"""Tests covering async_setup_entry for cover, light, number, valve, button.

Each platform's async_setup_entry is the ONLY gap left after the existing
*_unit.py tests.  We drive each coroutine with a fake hass / config_entry /
session via asyncio.run — NO HA harness, NO tests.common, NO network.

Pattern
-------
- hass   = SimpleNamespace(data={DOMAIN: {"E1": {DATA_SESSION: fake_session}}})
- config_entry = SimpleNamespace(entry_id="E1")
- async_add_entities collects the created entities into a list
- async_migrate_to_new_unique_id (cover + light) is patched to a no-op coroutine
  because it calls entity_registry.async_get(hass) which needs a real HA instance.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from boschshcpy import SHCShutterControl

from custom_components.bosch_shc.button import SHCRelayButton
from custom_components.bosch_shc.const import DATA_SESSION, DOMAIN
from custom_components.bosch_shc.cover import BlindsControlCover, ShutterControlCover
from custom_components.bosch_shc.light import LightSwitch
from custom_components.bosch_shc.number import SHCNumber
from custom_components.bosch_shc.valve import SHCValve

STOPPED = SHCShutterControl.ShutterControlService.State.STOPPED

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_hass(session: object) -> SimpleNamespace:
    return SimpleNamespace(data={DOMAIN: {"E1": {DATA_SESSION: session}}})


def _make_config_entry() -> SimpleNamespace:
    return SimpleNamespace(entry_id="E1")


def _collect() -> tuple[list, callable]:
    """Return (collected_list, async_add_entities callable)."""
    collected: list = []

    def add(entities: list) -> None:
        collected.extend(entities)

    return collected, add


# ---------------------------------------------------------------------------
# Fake devices
# ---------------------------------------------------------------------------

def _cover_device(
    device_model: str = "BBL",
    level: float = 0.5,
    operation_state=STOPPED,
) -> SimpleNamespace:
    """Minimal device for ShutterControlCover.__init__ + _update_attr."""
    return SimpleNamespace(
        name="Test Cover",
        id="hdm:HomeMaticIP:cover1",
        root_device_id="aa:bb:cc:00:00:01",
        serial="serial-cover1",
        device_model=device_model,
        level=level,
        operation_state=operation_state,
        device_services=[],
        manufacturer="Bosch",
        status="AVAILABLE",
        deleted=False,
    )


def _blinds_device(
    blinds_level: float = 0.5,
    level: float = 0.5,
    current_angle: float = 0.3,
) -> SimpleNamespace:
    """Minimal device for BlindsControlCover.__init__ + _update_attr."""
    return SimpleNamespace(
        name="Test Blinds",
        id="hdm:HomeMaticIP:blind1",
        root_device_id="aa:bb:cc:00:00:02",
        serial="serial-blind1",
        device_model="MICROMODULE_BLINDS",
        level=level,
        blinds_level=blinds_level,
        current_angle=current_angle,
        operation_state=STOPPED,
        device_services=[],
        manufacturer="Bosch",
        status="AVAILABLE",
        deleted=False,
    )


def _light_device(
    supports_color_hsb: bool = False,
    supports_color_temp: bool = False,
    supports_brightness: bool = True,
    min_color_temperature: int = 153,
    max_color_temperature: int = 500,
) -> SimpleNamespace:
    """Minimal device for LightSwitch.__init__."""
    return SimpleNamespace(
        name="Test Light",
        id="hdm:HomeMaticIP:light1",
        root_device_id="aa:bb:cc:00:00:03",
        serial="serial-light1",
        supports_color_hsb=supports_color_hsb,
        supports_color_temp=supports_color_temp,
        supports_brightness=supports_brightness,
        min_color_temperature=min_color_temperature,
        max_color_temperature=max_color_temperature,
        device_services=[],
        manufacturer="Bosch",
        device_model="LD",
        status="AVAILABLE",
        deleted=False,
    )


def _number_device() -> SimpleNamespace:
    """Minimal device for SHCNumber.__init__."""
    return SimpleNamespace(
        name="Test Thermostat",
        id="hdm:HomeMaticIP:thermo1",
        root_device_id="aa:bb:cc:00:00:04",
        serial="serial-thermo1",
        offset=0.0,
        min_offset=-5.0,
        max_offset=5.0,
        step_size=0.5,
        device_services=[],
        manufacturer="Bosch",
        device_model="TRV",
        status="AVAILABLE",
        deleted=False,
    )


def _valve_device() -> SimpleNamespace:
    """Minimal device for SHCValve.__init__."""
    return SimpleNamespace(
        name="Test Valve",
        id="hdm:HomeMaticIP:valve1",
        root_device_id="aa:bb:cc:00:00:05",
        serial="serial-valve1",
        position=50,
        device_services=[],
        manufacturer="Bosch",
        device_model="TRV",
        status="AVAILABLE",
        deleted=False,
    )


def _button_device() -> SimpleNamespace:
    """Minimal device for SHCRelayButton.__init__."""
    return SimpleNamespace(
        name="Test Button",
        id="hdm:HomeMaticIP:relay1",
        root_device_id="aa:bb:cc:00:00:06",
        serial="serial-relay1",
        device_services=[],
        manufacturer="Bosch",
        device_model="MR",
        status="AVAILABLE",
        deleted=False,
    )


# ---------------------------------------------------------------------------
# cover.py — async_setup_entry  (lines 34–59)
# ---------------------------------------------------------------------------

class TestCoverSetupEntry:
    """Cover async_setup_entry with ShutterControlCover and BlindsControlCover."""

    def _run(self, session: object) -> list:
        from custom_components.bosch_shc.cover import async_setup_entry

        hass = _make_hass(session)
        entry = _make_config_entry()
        collected, add = _collect()

        async def _run_inner() -> None:
            await async_setup_entry(hass, entry, add)  # type: ignore[arg-type]

        with patch(
            "custom_components.bosch_shc.cover.async_migrate_to_new_unique_id",
            new_callable=AsyncMock,
        ):
            asyncio.run(_run_inner())

        return collected

    def test_shutter_controls_produce_shutter_cover_entities(self) -> None:
        """shutter_controls → ShutterControlCover, one per device."""
        dev = _cover_device()
        session = SimpleNamespace(
            device_helper=SimpleNamespace(
                shutter_controls=[dev],
                micromodule_shutter_controls=[],
                micromodule_blinds=[],
            )
        )
        result = self._run(session)
        assert len(result) == 1
        assert isinstance(result[0], ShutterControlCover)

    def test_micromodule_shutter_controls_produce_shutter_cover_entities(self) -> None:
        """micromodule_shutter_controls → ShutterControlCover."""
        dev = _cover_device(device_model="MICROMODULE_SHUTTER", level=0.0)
        session = SimpleNamespace(
            device_helper=SimpleNamespace(
                shutter_controls=[],
                micromodule_shutter_controls=[dev],
                micromodule_blinds=[],
            )
        )
        result = self._run(session)
        assert len(result) == 1
        assert isinstance(result[0], ShutterControlCover)

    def test_micromodule_blinds_produce_blinds_cover_entities(self) -> None:
        """micromodule_blinds → BlindsControlCover."""
        dev = _blinds_device()
        session = SimpleNamespace(
            device_helper=SimpleNamespace(
                shutter_controls=[],
                micromodule_shutter_controls=[],
                micromodule_blinds=[dev],
            )
        )
        result = self._run(session)
        assert len(result) == 1
        assert isinstance(result[0], BlindsControlCover)

    def test_mixed_devices_all_collected(self) -> None:
        """shutter + micromodule_shutter + blinds → 3 entities total."""
        session = SimpleNamespace(
            device_helper=SimpleNamespace(
                shutter_controls=[_cover_device()],
                micromodule_shutter_controls=[_cover_device(device_model="MICROMODULE_SHUTTER")],
                micromodule_blinds=[_blinds_device()],
            )
        )
        result = self._run(session)
        assert len(result) == 3
        assert isinstance(result[0], ShutterControlCover)
        assert isinstance(result[1], ShutterControlCover)
        assert isinstance(result[2], BlindsControlCover)

    def test_no_devices_adds_nothing(self) -> None:
        """Empty lists → async_add_entities never called → 0 collected."""
        session = SimpleNamespace(
            device_helper=SimpleNamespace(
                shutter_controls=[],
                micromodule_shutter_controls=[],
                micromodule_blinds=[],
            )
        )
        result = self._run(session)
        assert result == []

    def test_entry_id_set_on_entities(self) -> None:
        """Entities get the config_entry entry_id stored as _entry_id."""
        dev = _cover_device()
        session = SimpleNamespace(
            device_helper=SimpleNamespace(
                shutter_controls=[dev],
                micromodule_shutter_controls=[],
                micromodule_blinds=[],
            )
        )
        result = self._run(session)
        assert result[0]._entry_id == "E1"

    def test_multiple_shutter_controls(self) -> None:
        """Two shutter_controls → two ShutterControlCover entities."""
        session = SimpleNamespace(
            device_helper=SimpleNamespace(
                shutter_controls=[_cover_device(), _cover_device()],
                micromodule_shutter_controls=[],
                micromodule_blinds=[],
            )
        )
        result = self._run(session)
        assert len(result) == 2
        assert all(isinstance(e, ShutterControlCover) for e in result)


# ---------------------------------------------------------------------------
# light.py — async_setup_entry  (lines 20–36)
# ---------------------------------------------------------------------------

class TestLightSetupEntry:
    """Light async_setup_entry with LightSwitch (BRIGHTNESS mode)."""

    def _run(self, session: object) -> list:
        from custom_components.bosch_shc.light import async_setup_entry

        hass = _make_hass(session)
        entry = _make_config_entry()
        collected, add = _collect()

        async def _run_inner() -> None:
            await async_setup_entry(hass, entry, add)  # type: ignore[arg-type]

        with patch(
            "custom_components.bosch_shc.light.async_migrate_to_new_unique_id",
            new_callable=AsyncMock,
        ):
            asyncio.run(_run_inner())

        return collected

    def test_ledvance_lights_produce_light_switch_entities(self) -> None:
        """ledvance_lights → LightSwitch."""
        dev = _light_device()
        session = SimpleNamespace(
            device_helper=SimpleNamespace(
                ledvance_lights=[dev],
                micromodule_dimmers=[],
            )
        )
        result = self._run(session)
        assert len(result) == 1
        assert isinstance(result[0], LightSwitch)

    def test_micromodule_dimmers_produce_light_switch_entities(self) -> None:
        """micromodule_dimmers → LightSwitch."""
        dev = _light_device()
        session = SimpleNamespace(
            device_helper=SimpleNamespace(
                ledvance_lights=[],
                micromodule_dimmers=[dev],
            )
        )
        result = self._run(session)
        assert len(result) == 1
        assert isinstance(result[0], LightSwitch)

    def test_mixed_light_devices_all_collected(self) -> None:
        """ledvance + micromodule_dimmer → 2 entities."""
        session = SimpleNamespace(
            device_helper=SimpleNamespace(
                ledvance_lights=[_light_device()],
                micromodule_dimmers=[_light_device()],
            )
        )
        result = self._run(session)
        assert len(result) == 2
        assert all(isinstance(e, LightSwitch) for e in result)

    def test_no_lights_adds_nothing(self) -> None:
        """Empty lists → 0 entities."""
        session = SimpleNamespace(
            device_helper=SimpleNamespace(
                ledvance_lights=[],
                micromodule_dimmers=[],
            )
        )
        result = self._run(session)
        assert result == []

    def test_entry_id_set_on_light_entity(self) -> None:
        """LightSwitch gets the entry_id stored."""
        dev = _light_device()
        session = SimpleNamespace(
            device_helper=SimpleNamespace(
                ledvance_lights=[dev],
                micromodule_dimmers=[],
            )
        )
        result = self._run(session)
        assert result[0]._entry_id == "E1"

    def test_color_temp_only_device(self) -> None:
        """A device with only color-temp support → LightSwitch in COLOR_TEMP mode."""
        from homeassistant.components.light import ColorMode

        dev = _light_device(
            supports_color_hsb=False,
            supports_color_temp=True,
            supports_brightness=False,
        )
        session = SimpleNamespace(
            device_helper=SimpleNamespace(
                ledvance_lights=[dev],
                micromodule_dimmers=[],
            )
        )
        result = self._run(session)
        assert len(result) == 1
        assert result[0]._attr_color_mode == ColorMode.COLOR_TEMP

    def test_onoff_only_device(self) -> None:
        """A device with no color/brightness support → LightSwitch in ONOFF mode."""
        from homeassistant.components.light import ColorMode

        dev = _light_device(
            supports_color_hsb=False,
            supports_color_temp=False,
            supports_brightness=False,
        )
        session = SimpleNamespace(
            device_helper=SimpleNamespace(
                ledvance_lights=[dev],
                micromodule_dimmers=[],
            )
        )
        result = self._run(session)
        assert len(result) == 1
        assert result[0]._attr_color_mode == ColorMode.ONOFF


# ---------------------------------------------------------------------------
# number.py — async_setup_entry  (lines 28–43)
# ---------------------------------------------------------------------------

class TestNumberSetupEntry:
    """Number async_setup_entry: thermostats + roomthermostats → SHCNumber."""

    def _run(self, session: object) -> list:
        from custom_components.bosch_shc.number import async_setup_entry

        hass = _make_hass(session)
        entry = _make_config_entry()
        collected, add = _collect()

        asyncio.run(async_setup_entry(hass, entry, add))  # type: ignore[arg-type]
        return collected

    def test_thermostats_produce_shc_number_entities(self) -> None:
        """session.device_helper.thermostats → SHCNumber."""
        dev = _number_device()
        session = SimpleNamespace(
            device_helper=SimpleNamespace(
                thermostats=[dev],
                roomthermostats=[],
            )
        )
        result = self._run(session)
        assert len(result) == 1
        assert isinstance(result[0], SHCNumber)

    def test_roomthermostats_produce_shc_number_entities(self) -> None:
        """session.device_helper.roomthermostats → SHCNumber."""
        dev = _number_device()
        session = SimpleNamespace(
            device_helper=SimpleNamespace(
                thermostats=[],
                roomthermostats=[dev],
            )
        )
        result = self._run(session)
        assert len(result) == 1
        assert isinstance(result[0], SHCNumber)

    def test_mixed_thermostats_collected(self) -> None:
        """thermostat + roomthermostat → 2 SHCNumber entities."""
        session = SimpleNamespace(
            device_helper=SimpleNamespace(
                thermostats=[_number_device()],
                roomthermostats=[_number_device()],
            )
        )
        result = self._run(session)
        assert len(result) == 2
        assert all(isinstance(e, SHCNumber) for e in result)

    def test_no_thermostats_adds_nothing(self) -> None:
        """No thermostats → nothing added."""
        session = SimpleNamespace(
            device_helper=SimpleNamespace(
                thermostats=[],
                roomthermostats=[],
            )
        )
        result = self._run(session)
        assert result == []

    def test_attr_name_offset_applied(self) -> None:
        """async_setup_entry always passes attr_name='Offset'."""
        dev = _number_device()
        session = SimpleNamespace(
            device_helper=SimpleNamespace(
                thermostats=[dev],
                roomthermostats=[],
            )
        )
        result = self._run(session)
        assert result[0]._attr_name == "Test Thermostat Offset"

    def test_unique_id_includes_offset_suffix(self) -> None:
        """unique_id for 'Offset' attr_name ends in '_offset'."""
        dev = _number_device()
        session = SimpleNamespace(
            device_helper=SimpleNamespace(
                thermostats=[dev],
                roomthermostats=[],
            )
        )
        result = self._run(session)
        assert result[0]._attr_unique_id.endswith("_offset")

    def test_entry_id_stored(self) -> None:
        dev = _number_device()
        session = SimpleNamespace(
            device_helper=SimpleNamespace(
                thermostats=[dev],
                roomthermostats=[],
            )
        )
        result = self._run(session)
        assert result[0]._entry_id == "E1"


# ---------------------------------------------------------------------------
# valve.py — async_setup_entry  (lines 27–40)
# ---------------------------------------------------------------------------

class TestValveSetupEntry:
    """Valve async_setup_entry: thermostats → SHCValve."""

    def _run(self, session: object) -> list:
        from custom_components.bosch_shc.valve import async_setup_entry

        hass = _make_hass(session)
        entry = _make_config_entry()
        collected, add = _collect()

        asyncio.run(async_setup_entry(hass, entry, add))  # type: ignore[arg-type]
        return collected

    def test_thermostats_produce_shc_valve_entities(self) -> None:
        """session.device_helper.thermostats → SHCValve."""
        dev = _valve_device()
        session = SimpleNamespace(
            device_helper=SimpleNamespace(thermostats=[dev])
        )
        result = self._run(session)
        assert len(result) == 1
        assert isinstance(result[0], SHCValve)

    def test_no_thermostats_adds_nothing(self) -> None:
        """No thermostats → nothing added."""
        session = SimpleNamespace(
            device_helper=SimpleNamespace(thermostats=[])
        )
        result = self._run(session)
        assert result == []

    def test_attr_name_valve_applied(self) -> None:
        """async_setup_entry always passes attr_name='Valve'."""
        dev = _valve_device()
        session = SimpleNamespace(
            device_helper=SimpleNamespace(thermostats=[dev])
        )
        result = self._run(session)
        assert result[0]._attr_name == "Test Valve Valve"

    def test_unique_id_includes_valve_suffix(self) -> None:
        """unique_id ends in '_valve'."""
        dev = _valve_device()
        session = SimpleNamespace(
            device_helper=SimpleNamespace(thermostats=[dev])
        )
        result = self._run(session)
        assert result[0]._attr_unique_id.endswith("_valve")

    def test_multiple_thermostats_all_collected(self) -> None:
        """Two thermostats → two SHCValve entities."""
        session = SimpleNamespace(
            device_helper=SimpleNamespace(
                thermostats=[_valve_device(), _valve_device()]
            )
        )
        result = self._run(session)
        assert len(result) == 2
        assert all(isinstance(e, SHCValve) for e in result)

    def test_entry_id_stored(self) -> None:
        dev = _valve_device()
        session = SimpleNamespace(
            device_helper=SimpleNamespace(thermostats=[dev])
        )
        result = self._run(session)
        assert result[0]._entry_id == "E1"


# ---------------------------------------------------------------------------
# button.py — async_setup_entry  (lines 43–55)
# ---------------------------------------------------------------------------

class TestButtonSetupEntry:
    """Button async_setup_entry: micromodule_impulse_relays → SHCRelayButton."""

    def _run(self, session: object) -> list:
        from custom_components.bosch_shc.button import async_setup_entry

        hass = _make_hass(session)
        entry = _make_config_entry()
        collected, add = _collect()

        asyncio.run(async_setup_entry(hass, entry, add))  # type: ignore[arg-type]
        return collected

    def test_impulse_relays_produce_relay_button_entities(self) -> None:
        """micromodule_impulse_relays → SHCRelayButton."""
        dev = _button_device()
        session = SimpleNamespace(
            device_helper=SimpleNamespace(micromodule_impulse_relays=[dev])
        )
        result = self._run(session)
        assert len(result) == 1
        assert isinstance(result[0], SHCRelayButton)

    def test_no_relays_adds_nothing(self) -> None:
        """No relays → nothing added."""
        session = SimpleNamespace(
            device_helper=SimpleNamespace(micromodule_impulse_relays=[])
        )
        result = self._run(session)
        assert result == []

    def test_entity_name_from_device(self) -> None:
        """SHCRelayButton._attr_name == device.name (no attr_name passed)."""
        dev = _button_device()
        session = SimpleNamespace(
            device_helper=SimpleNamespace(micromodule_impulse_relays=[dev])
        )
        result = self._run(session)
        assert result[0]._attr_name == "Test Button"

    def test_unique_id_from_root_and_device_id(self) -> None:
        """unique_id = root_device_id + '_' + device_id."""
        dev = _button_device()
        session = SimpleNamespace(
            device_helper=SimpleNamespace(micromodule_impulse_relays=[dev])
        )
        result = self._run(session)
        assert result[0]._attr_unique_id == "aa:bb:cc:00:00:06_hdm:HomeMaticIP:relay1"

    def test_multiple_relays_all_collected(self) -> None:
        """Two relays → two SHCRelayButton entities."""
        session = SimpleNamespace(
            device_helper=SimpleNamespace(
                micromodule_impulse_relays=[_button_device(), _button_device()]
            )
        )
        result = self._run(session)
        assert len(result) == 2
        assert all(isinstance(e, SHCRelayButton) for e in result)

    def test_entry_id_stored(self) -> None:
        dev = _button_device()
        session = SimpleNamespace(
            device_helper=SimpleNamespace(micromodule_impulse_relays=[dev])
        )
        result = self._run(session)
        assert result[0]._entry_id == "E1"
