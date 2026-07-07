"""Isolation-safe unit tests for button.py — all button entity classes.

Pattern: Cls.__new__(Cls) bypasses SHCEntity.__init__ (which needs hass /
device-registry).  We only set the attributes the class under test actually
reads.  async_press tests drive the coroutine with asyncio.run() — no event
loop, no HA harness, no network.

async_setup_entry tests build a minimal fake session / hass / config_entry and
call the coroutine directly (same pattern as test_platforms_setup.py).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from boschshcpy.exceptions import SHCException
from homeassistant.exceptions import HomeAssistantError

from custom_components.bosch_shc.button import (
    ResetEnergySummationButton,
    SHCDetectionTestButton,
    SHCDetectionTestStopButton,
    SHCRelayButton,
    SHCScenarioButton,
    SHCSirenTestAlarmButton,
    SHCSmokeTestButton,
    SHCTamperResetButton,
    SHCWalkTestButton,
    SHCWalkTestStopButton,
    ShutterRecalibrateButton,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_device(
    name: str = "Test Device",
    device_id: str = "hdm:HomeMaticIP:abc123",
    root_device_id: str = "64:da:a0:00:00:01",
    room_id=None,
) -> SimpleNamespace:
    """Minimal fake SHCDevice for button entities."""
    return SimpleNamespace(
        name=name,
        id=device_id,
        root_device_id=root_device_id,
        room_id=room_id,
        serial=f"serial-{device_id}",
        device_services=[],
        manufacturer="Bosch",
        device_model="TEST",
        status="AVAILABLE",
        deleted=False,
    )


def _make_hass() -> SimpleNamespace:
    """Minimal fake hass (unused by button.async_setup_entry, kept for parity)."""
    return SimpleNamespace()


def _make_config_entry(
    session: object, options=None, unique_id="test-uid", shc_device=None
) -> SimpleNamespace:
    """Minimal fake ConfigEntry with runtime_data (session/shc_device)."""
    entry = SimpleNamespace(options=options or {}, entry_id="E1", unique_id=unique_id)
    entry.runtime_data = SimpleNamespace(
        session=session, shc_device=shc_device, title="Test SHC"
    )
    return entry


def _collect():
    """Return (collected_list, async_add_entities callable)."""
    collected: list = []

    def add(entities: list) -> None:
        collected.extend(entities)

    return collected, add


def _run_setup(
    session: object, options=None, unique_id="test-uid", shc_device=None
) -> list:
    """Drive button.async_setup_entry with fake hass/entry/session."""
    from custom_components.bosch_shc.button import async_setup_entry

    hass = _make_hass()
    entry = _make_config_entry(
        session, options=options, unique_id=unique_id, shc_device=shc_device
    )
    collected, add = _collect()
    asyncio.run(async_setup_entry(hass, entry, add))  # type: ignore[arg-type]
    return collected


# ---------------------------------------------------------------------------
# SHCRelayButton
# ---------------------------------------------------------------------------


class TestSHCRelayButton:
    """Unit tests for SHCRelayButton (impulse relay)."""

    def _make(
        self,
        attr_name=None,
        root="64:da:a0:00:00:01",
        device_id="hdm:HomeMaticIP:relay1",
    ) -> SHCRelayButton:
        btn = SHCRelayButton.__new__(SHCRelayButton)
        dev = _make_device(device_id=device_id, root_device_id=root)
        btn._device = dev
        btn._attr_name = None if attr_name is None else attr_name
        btn._attr_unique_id = (
            f"{root}_{device_id}"
            if attr_name is None
            else f"{root}_{device_id}_{attr_name.lower()}"
        )
        return btn

    def test_name_none_by_default(self):
        btn = self._make()
        assert btn._attr_name is None

    def test_unique_id_without_attr_name(self):
        btn = self._make(root="64:da:a0:00:00:01", device_id="hdm:HomeMaticIP:r1")
        assert btn._attr_unique_id == "64:da:a0:00:00:01_hdm:HomeMaticIP:r1"

    def test_unique_id_with_attr_name_lowercased(self):
        btn = self._make(
            attr_name="Channel A",
            root="root1",
            device_id="dev1",
        )
        assert btn._attr_unique_id == "root1_dev1_channel a"

    def test_press_calls_async_trigger_impulse_state(self):
        btn = self._make()
        called = []

        async def _trig():
            called.append(True)

        btn._device.async_trigger_impulse_state = _trig
        asyncio.run(btn.async_press())
        assert called == [True]

    def test_press_can_be_called_multiple_times(self):
        btn = self._make()
        count = []

        async def _trig():
            count.append(1)

        btn._device.async_trigger_impulse_state = _trig
        asyncio.run(btn.async_press())
        asyncio.run(btn.async_press())
        assert len(count) == 2

    def test_is_button_entity(self):
        from homeassistant.components.button import ButtonEntity

        assert issubclass(SHCRelayButton, ButtonEntity)

    def test_is_shc_entity(self):
        from custom_components.bosch_shc.entity import SHCEntity

        assert issubclass(SHCRelayButton, SHCEntity)

    def test_press_wraps_shc_exception_in_home_assistant_error(self):
        btn = self._make()

        async def _fail():
            raise SHCException("relay busy")

        btn._device.async_trigger_impulse_state = _fail
        with pytest.raises(HomeAssistantError) as exc_info:
            asyncio.run(btn.async_press())
        assert exc_info.value.translation_key == "button_press_failed"

    # async_setup_entry integration

    def test_setup_impulse_relay_creates_relay_button(self):
        dev = _make_device()
        session = SimpleNamespace(
            device_helper=SimpleNamespace(micromodule_impulse_relays=[dev])
        )
        result = _run_setup(session)
        assert len(result) == 1
        assert isinstance(result[0], SHCRelayButton)

    def test_setup_no_relays_yields_nothing(self):
        session = SimpleNamespace(
            device_helper=SimpleNamespace(micromodule_impulse_relays=[])
        )
        result = _run_setup(session)
        assert result == []

    def test_setup_multiple_relays(self):
        session = SimpleNamespace(
            device_helper=SimpleNamespace(
                micromodule_impulse_relays=[_make_device(), _make_device()]
            )
        )
        result = _run_setup(session)
        assert len(result) == 2
        assert all(isinstance(e, SHCRelayButton) for e in result)

    def test_setup_entry_id_stored(self):
        dev = _make_device()
        session = SimpleNamespace(
            device_helper=SimpleNamespace(micromodule_impulse_relays=[dev])
        )
        result = _run_setup(session)
        assert result[0]._entry_id == "E1"

    def test_setup_excluded_device_skipped(self):
        dev = _make_device(device_id="hdm:excluded")
        session = SimpleNamespace(
            device_helper=SimpleNamespace(micromodule_impulse_relays=[dev])
        )
        result = _run_setup(session, options={"excluded_devices": ["hdm:excluded"]})
        assert result == []


# ---------------------------------------------------------------------------
# SHCSmokeTestButton
# ---------------------------------------------------------------------------


class TestSHCSmokeTestButton:
    """Unit tests for SHCSmokeTestButton (smoke detector + twinguard)."""

    def _make(
        self, root="64:da:a0:00:00:02", device_id="hdm:ZigBee:smoke1"
    ) -> SHCSmokeTestButton:
        btn = SHCSmokeTestButton.__new__(SHCSmokeTestButton)
        dev = _make_device(device_id=device_id, root_device_id=root)
        btn._device = dev
        btn._attr_name = "Smoke Test"
        btn._attr_unique_id = f"{root}_{device_id}_smoke_test"
        return btn

    def test_attr_name(self):
        btn = self._make()
        assert btn._attr_name == "Smoke Test"

    def test_unique_id_ends_smoke_test(self):
        btn = self._make(root="r1", device_id="d1")
        assert btn._attr_unique_id == "r1_d1_smoke_test"

    def test_translation_key(self):
        # Icon now comes from icons.json keyed by translation_key, not a
        # hardcoded _attr_icon (icon-translations quality-scale rule).
        btn = self._make()
        assert btn._attr_translation_key == "smoke_test"

    def test_press_calls_async_smoketest_requested(self):
        btn = self._make()
        called = []

        async def _req():
            called.append(True)

        btn._device.async_smoketest_requested = _req
        asyncio.run(btn.async_press())
        assert called == [True]

    def test_press_wraps_shc_exception_reuses_smoke_test_failed_key(self):
        """Smoke-test button reuses the existing smoke_test_failed key (not the
        generic button_press_failed one) to match binary_sensor.py's convention."""
        btn = self._make()

        async def _fail():
            raise SHCException("comms failure")

        btn._device.async_smoketest_requested = _fail
        with pytest.raises(HomeAssistantError) as exc_info:
            asyncio.run(btn.async_press())
        assert exc_info.value.translation_key == "smoke_test_failed"

    def test_is_button_entity(self):
        from homeassistant.components.button import ButtonEntity

        assert issubclass(SHCSmokeTestButton, ButtonEntity)

    # async_setup_entry integration

    def test_setup_smoke_detector_creates_smoke_test_button(self):
        dev = _make_device()
        session = SimpleNamespace(
            device_helper=SimpleNamespace(
                micromodule_impulse_relays=[],
                smoke_detectors=[dev],
                twinguards=[],
            )
        )
        result = _run_setup(session)
        assert len(result) == 1
        assert isinstance(result[0], SHCSmokeTestButton)

    def test_setup_twinguard_creates_smoke_test_button(self):
        dev = _make_device()
        session = SimpleNamespace(
            device_helper=SimpleNamespace(
                micromodule_impulse_relays=[],
                smoke_detectors=[],
                twinguards=[dev],
            )
        )
        result = _run_setup(session)
        assert len(result) == 1
        assert isinstance(result[0], SHCSmokeTestButton)

    def test_setup_both_smoke_and_twinguard(self):
        session = SimpleNamespace(
            device_helper=SimpleNamespace(
                micromodule_impulse_relays=[],
                smoke_detectors=[_make_device(device_id="s1")],
                twinguards=[_make_device(device_id="t1")],
            )
        )
        result = _run_setup(session)
        assert len(result) == 2
        assert all(isinstance(e, SHCSmokeTestButton) for e in result)

    def test_setup_unique_id_includes_smoke_test_suffix(self):
        dev = _make_device(root_device_id="root1", device_id="dev1")
        session = SimpleNamespace(
            device_helper=SimpleNamespace(
                micromodule_impulse_relays=[],
                smoke_detectors=[dev],
                twinguards=[],
            )
        )
        result = _run_setup(session)
        assert result[0]._attr_unique_id == "root1_dev1_smoke_test"


# ---------------------------------------------------------------------------
# SHCWalkTestButton + SHCWalkTestStopButton
# ---------------------------------------------------------------------------


class TestSHCWalkTestButtons:
    """Unit tests for SHCWalkTestButton and SHCWalkTestStopButton."""

    def _make_walk(self, cls, root="root-md2", device_id="hdm:MD2:w1"):
        btn = cls.__new__(cls)
        dev = _make_device(device_id=device_id, root_device_id=root)
        btn._device = dev
        suffix = "walk_test" if cls is SHCWalkTestButton else "walk_test_stop"
        btn._attr_name = "Walk Test" if cls is SHCWalkTestButton else "Walk Test Stop"
        btn._attr_unique_id = f"{root}_{device_id}_{suffix}"
        return btn

    # --- SHCWalkTestButton ---

    def test_walk_start_name(self):
        btn = self._make_walk(SHCWalkTestButton)
        assert btn._attr_name == "Walk Test"

    def test_walk_start_unique_id(self):
        btn = self._make_walk(SHCWalkTestButton, root="r1", device_id="d1")
        assert btn._attr_unique_id == "r1_d1_walk_test"

    def test_walk_start_translation_key(self):
        # Icon now comes from icons.json keyed by translation_key, not a
        # hardcoded _attr_icon (icon-translations quality-scale rule).
        btn = self._make_walk(SHCWalkTestButton)
        assert btn._attr_translation_key == "walk_test"

    def test_walk_start_press_sends_start_request(self):
        from boschshcpy.services_impl import WalkTestService

        btn = self._make_walk(SHCWalkTestButton)
        received = []

        async def _set(req):
            received.append(req)

        btn._device.async_set_walk_state_request = _set
        asyncio.run(btn.async_press())
        assert received == [WalkTestService.WalkStateRequest.WALK_STATE_START]

    # --- SHCWalkTestStopButton ---

    def test_walk_stop_name(self):
        btn = self._make_walk(SHCWalkTestStopButton)
        assert btn._attr_name == "Walk Test Stop"

    def test_walk_stop_unique_id(self):
        btn = self._make_walk(SHCWalkTestStopButton, root="r1", device_id="d1")
        assert btn._attr_unique_id == "r1_d1_walk_test_stop"

    def test_walk_stop_translation_key(self):
        # Icon now comes from icons.json keyed by translation_key, not a
        # hardcoded _attr_icon (icon-translations quality-scale rule).
        btn = self._make_walk(SHCWalkTestStopButton)
        assert btn._attr_translation_key == "walk_test_stop"

    def test_walk_stop_press_sends_stop_request(self):
        from boschshcpy.services_impl import WalkTestService

        btn = self._make_walk(SHCWalkTestStopButton)
        received = []

        async def _set(req):
            received.append(req)

        btn._device.async_set_walk_state_request = _set
        asyncio.run(btn.async_press())
        assert received == [WalkTestService.WalkStateRequest.WALK_STATE_STOP]

    # --- async_setup_entry integration ---

    def _md2_device(
        self,
        supports_walk=True,
        walk_state="STOPPED",
        supports_detection=False,
        has_tamper=True,
    ):
        dev = _make_device(device_id="hdm:ZigBee:md2-walk")
        dev.supports_walk_test = supports_walk
        dev.walk_state = walk_state
        dev.supports_detection_test = supports_detection
        dev.reset_tampered_state = lambda: None
        dev.supports_tamper_reset = has_tamper
        return dev

    def _session(self, md2_devices):
        return SimpleNamespace(
            device_helper=SimpleNamespace(
                micromodule_impulse_relays=[],
                smoke_detectors=[],
                twinguards=[],
                motion_detectors2=md2_devices,
                outdoor_sirens=[],
            ),
            scenarios=[],
        )

    def test_setup_walk_test_creates_start_and_stop_buttons(self):
        dev = self._md2_device()
        result = _run_setup(self._session([dev]))
        walk_types = [type(e) for e in result]
        assert SHCWalkTestButton in walk_types
        assert SHCWalkTestStopButton in walk_types

    def test_setup_walk_test_buttons_count(self):
        """One MD2 with walk_test → exactly 2 walk-test buttons (+ 1 tamper)."""
        dev = self._md2_device(
            supports_walk=True,
            walk_state="STOPPED",
            supports_detection=False,
            has_tamper=True,
        )
        result = _run_setup(self._session([dev]))
        walk_buttons = [
            e
            for e in result
            if isinstance(e, (SHCWalkTestButton, SHCWalkTestStopButton))
        ]
        assert len(walk_buttons) == 2

    def test_setup_no_walk_test_when_supports_false(self):
        dev = self._md2_device(supports_walk=False)
        result = _run_setup(self._session([dev]))
        assert not any(isinstance(e, SHCWalkTestButton) for e in result)
        assert not any(isinstance(e, SHCWalkTestStopButton) for e in result)

    def test_setup_no_walk_test_when_walk_state_none(self):
        dev = self._md2_device(walk_state=None)
        result = _run_setup(self._session([dev]))
        assert not any(isinstance(e, SHCWalkTestButton) for e in result)

    def test_setup_walk_unique_ids(self):
        dev = _make_device(root_device_id="root1", device_id="dev1")
        dev.supports_walk_test = True
        dev.walk_state = "STOPPED"
        dev.supports_detection_test = False
        dev.reset_tampered_state = lambda: None
        dev.supports_tamper_reset = True
        result = _run_setup(self._session([dev]))
        uids = [e._attr_unique_id for e in result]
        assert "root1_dev1_walk_test" in uids
        assert "root1_dev1_walk_test_stop" in uids


# ---------------------------------------------------------------------------
# SHCDetectionTestButton + SHCDetectionTestStopButton
# ---------------------------------------------------------------------------


class TestSHCDetectionTestButtons:
    """Unit tests for SHCDetectionTestButton and SHCDetectionTestStopButton."""

    def _make_det(self, cls, root="root-det", device_id="hdm:MD2:det1"):
        btn = cls.__new__(cls)
        dev = _make_device(device_id=device_id, root_device_id=root)
        btn._device = dev
        suffix = (
            "detection_test" if cls is SHCDetectionTestButton else "detection_test_stop"
        )
        btn._attr_name = (
            "Detection Test" if cls is SHCDetectionTestButton else "Detection Test Stop"
        )
        btn._attr_unique_id = f"{root}_{device_id}_{suffix}"
        return btn

    def test_det_start_name(self):
        btn = self._make_det(SHCDetectionTestButton)
        assert btn._attr_name == "Detection Test"

    def test_det_start_unique_id(self):
        btn = self._make_det(SHCDetectionTestButton, root="r1", device_id="d1")
        assert btn._attr_unique_id == "r1_d1_detection_test"

    def test_det_start_translation_key(self):
        # Icon now comes from icons.json keyed by translation_key, not a
        # hardcoded _attr_icon (icon-translations quality-scale rule).
        btn = self._make_det(SHCDetectionTestButton)
        assert btn._attr_translation_key == "detection_test"

    def test_det_start_press_sends_start_request(self):
        from boschshcpy.services_impl import DetectionTestService

        btn = self._make_det(SHCDetectionTestButton)
        received = []

        async def _set(req):
            received.append(req)

        btn._device.async_set_detection_state_request = _set
        asyncio.run(btn.async_press())
        assert received == [
            DetectionTestService.DetectionStateRequest.DETECTION_STATE_START
        ]

    def test_det_stop_name(self):
        btn = self._make_det(SHCDetectionTestStopButton)
        assert btn._attr_name == "Detection Test Stop"

    def test_det_stop_unique_id(self):
        btn = self._make_det(SHCDetectionTestStopButton, root="r2", device_id="d2")
        assert btn._attr_unique_id == "r2_d2_detection_test_stop"

    def test_det_stop_translation_key(self):
        # Icon now comes from icons.json keyed by translation_key, not a
        # hardcoded _attr_icon (icon-translations quality-scale rule).
        btn = self._make_det(SHCDetectionTestStopButton)
        assert btn._attr_translation_key == "detection_test_stop"

    def test_det_stop_press_sends_stop_request(self):
        from boschshcpy.services_impl import DetectionTestService

        btn = self._make_det(SHCDetectionTestStopButton)
        received = []

        async def _set(req):
            received.append(req)

        btn._device.async_set_detection_state_request = _set
        asyncio.run(btn.async_press())
        assert received == [
            DetectionTestService.DetectionStateRequest.DETECTION_STATE_STOP
        ]

    # --- async_setup_entry integration ---

    def _md2_device(self, supports_detection=True, has_tamper=True):
        dev = _make_device(device_id="hdm:ZigBee:md2-det")
        dev.supports_walk_test = False
        dev.walk_state = None
        dev.supports_detection_test = supports_detection
        dev.reset_tampered_state = lambda: None
        dev.supports_tamper_reset = has_tamper
        return dev

    def _session(self, md2_devices):
        return SimpleNamespace(
            device_helper=SimpleNamespace(
                micromodule_impulse_relays=[],
                smoke_detectors=[],
                twinguards=[],
                motion_detectors2=md2_devices,
                outdoor_sirens=[],
            ),
            scenarios=[],
        )

    def test_setup_detection_test_creates_start_and_stop(self):
        dev = self._md2_device()
        result = _run_setup(self._session([dev]))
        det_types = [type(e) for e in result]
        assert SHCDetectionTestButton in det_types
        assert SHCDetectionTestStopButton in det_types

    def test_setup_no_detection_when_not_supported(self):
        dev = self._md2_device(supports_detection=False)
        result = _run_setup(self._session([dev]))
        assert not any(isinstance(e, SHCDetectionTestButton) for e in result)

    def test_setup_det_unique_ids(self):
        dev = _make_device(root_device_id="root1", device_id="dev1")
        dev.supports_walk_test = False
        dev.walk_state = None
        dev.supports_detection_test = True
        dev.reset_tampered_state = lambda: None
        dev.supports_tamper_reset = True
        result = _run_setup(self._session([dev]))
        uids = [e._attr_unique_id for e in result]
        assert "root1_dev1_detection_test" in uids
        assert "root1_dev1_detection_test_stop" in uids


# ---------------------------------------------------------------------------
# SHCTamperResetButton
# ---------------------------------------------------------------------------


class TestSHCTamperResetButton:
    """Unit tests for SHCTamperResetButton (LatestTamper service)."""

    def _make(self, root="root-tamper", device_id="hdm:ZigBee:md2-tamper"):
        btn = SHCTamperResetButton.__new__(SHCTamperResetButton)
        dev = _make_device(device_id=device_id, root_device_id=root)
        btn._device = dev
        btn._attr_name = "Reset Tamper"
        btn._attr_unique_id = f"{root}_{device_id}_reset_tamper"
        return btn

    def test_attr_name(self):
        btn = self._make()
        assert btn._attr_name == "Reset Tamper"

    def test_unique_id_ends_reset_tamper(self):
        btn = self._make(root="r1", device_id="d1")
        assert btn._attr_unique_id == "r1_d1_reset_tamper"

    def test_translation_key(self):
        # Icon now comes from icons.json keyed by translation_key, not a
        # hardcoded _attr_icon (icon-translations quality-scale rule).
        btn = self._make()
        assert btn._attr_translation_key == "reset_tamper"

    def test_press_calls_async_reset_tampered_state(self):
        btn = self._make()
        called = []

        async def _reset():
            called.append(True)

        btn._device.async_reset_tampered_state = _reset
        asyncio.run(btn.async_press())
        assert called == [True]

    # --- async_setup_entry integration ---

    def _md2_device_with_tamper(self, has_tamper=True):
        dev = _make_device(device_id="hdm:ZigBee:md2-t1")
        dev.supports_walk_test = False
        dev.walk_state = None
        dev.supports_detection_test = False
        dev.reset_tampered_state = lambda: None
        dev.supports_tamper_reset = has_tamper
        return dev

    def _session(self, md2_devices):
        return SimpleNamespace(
            device_helper=SimpleNamespace(
                micromodule_impulse_relays=[],
                smoke_detectors=[],
                twinguards=[],
                motion_detectors2=md2_devices,
                outdoor_sirens=[],
            ),
            scenarios=[],
        )

    def test_setup_tamper_button_created_when_reset_method_present(self):
        dev = self._md2_device_with_tamper(has_tamper=True)
        result = _run_setup(self._session([dev]))
        assert len(result) == 1
        assert isinstance(result[0], SHCTamperResetButton)

    def test_setup_no_tamper_button_without_reset_method(self):
        dev = self._md2_device_with_tamper(has_tamper=False)
        result = _run_setup(self._session([dev]))
        assert not any(isinstance(e, SHCTamperResetButton) for e in result)

    def test_setup_tamper_unique_id(self):
        dev = _make_device(root_device_id="root1", device_id="dev1")
        dev.supports_walk_test = False
        dev.walk_state = None
        dev.supports_detection_test = False
        dev.reset_tampered_state = lambda: None
        dev.supports_tamper_reset = True
        result = _run_setup(self._session([dev]))
        assert result[0]._attr_unique_id == "root1_dev1_reset_tamper"


# ---------------------------------------------------------------------------
# SHCScenarioButton
# ---------------------------------------------------------------------------


class TestSHCScenarioButton:
    """Unit tests for SHCScenarioButton (does not inherit SHCEntity)."""

    def _make_scenario(self, scenario_id="sc-1", name="Good Night"):
        scenario = SimpleNamespace(id=scenario_id, name=name)
        shc_dev = SimpleNamespace(
            identifiers={("bosch_shc", "test-uid")},
            name="SHC Controller",
            manufacturer="Bosch",
            model="SmartHomeController",
        )
        return SHCScenarioButton(
            scenario=scenario,
            entry_unique_id="test-uid",
            entry_id="E1",
            shc_device=shc_dev,
        )

    def test_unique_id_uses_entry_unique_id_prefix(self):
        btn = self._make_scenario(scenario_id="sc-1")
        assert btn._attr_unique_id == "test-uid_scenario_sc-1"

    def test_unique_id_falls_back_to_entry_id_when_no_unique_id(self):
        scenario = SimpleNamespace(id="sc-2", name="Away")
        btn = SHCScenarioButton(
            scenario=scenario,
            entry_unique_id=None,
            entry_id="E1",
            shc_device=None,
        )
        assert btn._attr_unique_id == "E1_scenario_sc-2"

    def test_attr_name_is_scenario_name(self):
        btn = self._make_scenario(name="Morning Routine")
        assert btn._attr_name == "Morning Routine"

    def test_icon(self):
        btn = self._make_scenario()
        assert btn._attr_icon == "mdi:script-text-play"

    def test_should_poll_false(self):
        btn = self._make_scenario()
        assert btn._attr_should_poll is False

    def test_has_entity_name(self):
        btn = self._make_scenario()
        assert btn._attr_has_entity_name is True

    def test_device_info_contains_identifiers(self):
        btn = self._make_scenario()
        info = btn.device_info
        assert info is not None
        assert ("bosch_shc", "test-uid") in info["identifiers"]

    def test_device_info_none_when_shc_device_none(self):
        scenario = SimpleNamespace(id="sc-3", name="Test")
        btn = SHCScenarioButton(
            scenario=scenario,
            entry_unique_id="uid",
            entry_id="E1",
            shc_device=None,
        )
        assert btn.device_info is None

    def test_press_calls_async_trigger_on_scenario(self):
        scenario = SimpleNamespace(id="sc-1", name="Goodnight")
        called = []

        async def _trig():
            called.append(True)

        scenario.async_trigger = _trig
        btn = SHCScenarioButton(scenario=scenario, entry_unique_id="uid", entry_id="E1")
        asyncio.run(btn.async_press())
        assert called == [True]

    def test_press_wraps_shc_exception_in_home_assistant_error(self):
        """SHCScenarioButton has no self._device — must use self._scenario.name."""
        scenario = SimpleNamespace(id="sc-1", name="Goodnight")

        async def _fail():
            raise SHCException("scenario trigger rejected")

        scenario.async_trigger = _fail
        btn = SHCScenarioButton(scenario=scenario, entry_unique_id="uid", entry_id="E1")
        with pytest.raises(HomeAssistantError) as exc_info:
            asyncio.run(btn.async_press())
        assert exc_info.value.translation_key == "button_press_failed"
        assert "Goodnight" in str(exc_info.value)

    def test_is_button_entity(self):
        from homeassistant.components.button import ButtonEntity

        assert issubclass(SHCScenarioButton, ButtonEntity)

    # --- async_setup_entry integration ---

    def _session_with_scenarios(self, scenarios):
        return SimpleNamespace(
            device_helper=SimpleNamespace(
                micromodule_impulse_relays=[],
                smoke_detectors=[],
                twinguards=[],
                motion_detectors2=[],
                outdoor_sirens=[],
            ),
            scenarios=scenarios,
        )

    def test_setup_scenario_buttons_created_when_option_enabled(self):
        from custom_components.bosch_shc.const import OPT_SCENARIOS_AS_BUTTONS

        shc_dev = SimpleNamespace(
            identifiers={("bosch_shc", "uid")},
            name="SHC",
            manufacturer="Bosch",
            model="SmartHomeController",
        )
        sc = SimpleNamespace(id="sc-1", name="Away")
        session = self._session_with_scenarios([sc])
        result = _run_setup(
            session,
            options={OPT_SCENARIOS_AS_BUTTONS: True},
            unique_id="uid",
            shc_device=shc_dev,
        )
        scenario_buttons = [e for e in result if isinstance(e, SHCScenarioButton)]
        assert len(scenario_buttons) == 1
        assert scenario_buttons[0]._attr_name == "Away"

    def test_setup_no_scenario_buttons_when_option_disabled(self):
        from custom_components.bosch_shc.const import OPT_SCENARIOS_AS_BUTTONS

        sc = SimpleNamespace(id="sc-1", name="Away")
        session = self._session_with_scenarios([sc])
        result = _run_setup(session, options={OPT_SCENARIOS_AS_BUTTONS: False})
        assert not any(isinstance(e, SHCScenarioButton) for e in result)

    def test_setup_multiple_scenario_buttons(self):
        from custom_components.bosch_shc.const import OPT_SCENARIOS_AS_BUTTONS

        shc_dev = SimpleNamespace(
            identifiers={("bosch_shc", "uid")},
            name="SHC",
            manufacturer="Bosch",
            model="SmartHomeController",
        )
        scenarios = [
            SimpleNamespace(id="s1", name="Morning"),
            SimpleNamespace(id="s2", name="Evening"),
        ]
        session = self._session_with_scenarios(scenarios)
        result = _run_setup(
            session,
            options={OPT_SCENARIOS_AS_BUTTONS: True},
            unique_id="uid",
            shc_device=shc_dev,
        )
        sb = [e for e in result if isinstance(e, SHCScenarioButton)]
        assert len(sb) == 2

    def test_setup_bad_scenario_skipped_not_crash(self):
        """A malformed scenario (missing id/name) must be skipped gracefully."""
        from custom_components.bosch_shc.const import OPT_SCENARIOS_AS_BUTTONS

        shc_dev = SimpleNamespace(
            identifiers={("bosch_shc", "uid")},
            name="SHC",
            manufacturer="Bosch",
            model="SmartHomeController",
        )
        # No .id attribute → AttributeError → should be caught and skipped.
        bad_sc = SimpleNamespace(name="No ID")
        # SimpleNamespace doesn't set .id here — accessing it raises AttributeError.
        good_sc = SimpleNamespace(id="ok", name="Good")
        session = self._session_with_scenarios([bad_sc, good_sc])
        # This must not raise; the bad scenario is skipped.
        result = _run_setup(
            session,
            options={OPT_SCENARIOS_AS_BUTTONS: True},
            unique_id="uid",
            shc_device=shc_dev,
        )
        sb = [e for e in result if isinstance(e, SHCScenarioButton)]
        # Only the good scenario makes it through.
        assert len(sb) == 1
        assert sb[0]._attr_name == "Good"


# ---------------------------------------------------------------------------
# SHCSirenTestAlarmButton
# ---------------------------------------------------------------------------


class TestSHCSirenTestAlarmButton:
    """Unit tests for SHCSirenTestAlarmButton (Outdoor Siren #120)."""

    def _make(self, root="root-siren", device_id="hdm:ZigBee:siren1"):
        btn = SHCSirenTestAlarmButton.__new__(SHCSirenTestAlarmButton)
        dev = _make_device(device_id=device_id, root_device_id=root)
        btn._device = dev
        btn._attr_unique_id = f"{root}_{device_id}_test_alarm"
        return btn

    def test_unique_id_ends_test_alarm(self):
        btn = self._make(root="r1", device_id="d1")
        assert btn._attr_unique_id == "r1_d1_test_alarm"

    def test_translation_key(self):
        # Icon now comes from icons.json keyed by translation_key, not a
        # hardcoded _attr_icon (icon-translations quality-scale rule).
        btn = self._make()
        assert btn._attr_translation_key == "siren_test_alarm"

    def test_press_calls_async_trigger_test_alarm(self):
        btn = self._make()
        called = []

        async def _alarm():
            called.append(True)

        btn._device.async_trigger_test_alarm = _alarm
        asyncio.run(btn.async_press())
        assert called == [True]

    def test_is_button_entity(self):
        from homeassistant.components.button import ButtonEntity

        assert issubclass(SHCSirenTestAlarmButton, ButtonEntity)

    # --- async_setup_entry integration ---

    def _session_with_siren(self, sirens):
        return SimpleNamespace(
            device_helper=SimpleNamespace(
                micromodule_impulse_relays=[],
                smoke_detectors=[],
                twinguards=[],
                motion_detectors2=[],
                outdoor_sirens=sirens,
            ),
            scenarios=[],
        )

    def test_setup_siren_creates_test_alarm_button(self):
        dev = _make_device()
        session = self._session_with_siren([dev])
        result = _run_setup(session)
        assert len(result) == 1
        assert isinstance(result[0], SHCSirenTestAlarmButton)

    def test_setup_no_sirens_yields_nothing(self):
        session = self._session_with_siren([])
        result = _run_setup(session)
        assert result == []

    def test_setup_multiple_sirens(self):
        session = self._session_with_siren(
            [
                _make_device(device_id="s1"),
                _make_device(device_id="s2"),
            ]
        )
        result = _run_setup(session)
        assert len(result) == 2
        assert all(isinstance(e, SHCSirenTestAlarmButton) for e in result)

    def test_setup_siren_excluded(self):
        dev = _make_device(device_id="hdm:excluded-siren")
        session = self._session_with_siren([dev])
        result = _run_setup(
            session, options={"excluded_devices": ["hdm:excluded-siren"]}
        )
        assert result == []


# ---------------------------------------------------------------------------
# ResetEnergySummationButton (hass#120 audit)
# ---------------------------------------------------------------------------


class TestResetEnergySummationButton:
    """Unit tests for ResetEnergySummationButton (smart plugs, hass#120)."""

    def _make(self, root="root-plug", device_id="hdm:ZigBee:plug1"):
        btn = ResetEnergySummationButton.__new__(ResetEnergySummationButton)
        dev = _make_device(device_id=device_id, root_device_id=root)
        btn._device = dev
        btn._attr_unique_id = f"{root}_{device_id}_reset_energy_summation"
        return btn

    def test_unique_id_ends_reset_energy_summation(self):
        btn = self._make(root="r1", device_id="d1")
        assert btn._attr_unique_id == "r1_d1_reset_energy_summation"

    def test_translation_key(self):
        btn = self._make()
        assert btn._attr_translation_key == "reset_energy_summation"

    def test_press_calls_async_reset_energy_summation(self):
        btn = self._make()
        called = []

        async def _reset():
            called.append(True)

        btn._device.async_reset_energy_summation = _reset
        asyncio.run(btn.async_press())
        assert called == [True]

    def test_press_shc_exception_raises_home_assistant_error(self):
        btn = self._make()

        async def _reset():
            raise SHCException("rejected")

        btn._device.async_reset_energy_summation = _reset
        with pytest.raises(HomeAssistantError):
            asyncio.run(btn.async_press())

    def test_is_button_entity(self):
        from homeassistant.components.button import ButtonEntity

        assert issubclass(ResetEnergySummationButton, ButtonEntity)

    # --- async_setup_entry integration ---

    def _session_with_plugs(self, smart_plugs=(), smart_plugs_compact=()):
        return SimpleNamespace(
            device_helper=SimpleNamespace(
                micromodule_impulse_relays=[],
                smoke_detectors=[],
                twinguards=[],
                motion_detectors2=[],
                outdoor_sirens=[],
                smart_plugs=list(smart_plugs),
                smart_plugs_compact=list(smart_plugs_compact),
            ),
            scenarios=[],
        )

    def test_setup_smart_plug_creates_button(self):
        dev = _make_device()
        session = self._session_with_plugs(smart_plugs=[dev])
        result = _run_setup(session)
        assert len(result) == 1
        assert isinstance(result[0], ResetEnergySummationButton)

    def test_setup_smart_plug_compact_creates_button(self):
        dev = _make_device()
        session = self._session_with_plugs(smart_plugs_compact=[dev])
        result = _run_setup(session)
        assert len(result) == 1
        assert isinstance(result[0], ResetEnergySummationButton)

    def test_setup_no_plugs_yields_nothing(self):
        session = self._session_with_plugs()
        result = _run_setup(session)
        assert result == []

    def test_setup_plug_excluded(self):
        dev = _make_device(device_id="hdm:excluded-plug")
        session = self._session_with_plugs(smart_plugs=[dev])
        result = _run_setup(
            session, options={"excluded_devices": ["hdm:excluded-plug"]}
        )
        assert result == []


# ---------------------------------------------------------------------------
# ShutterRecalibrateButton (hass audit)
# ---------------------------------------------------------------------------


class TestShutterRecalibrateButton:
    """Unit tests for ShutterRecalibrateButton (Shutter Control II, hass audit)."""

    def _make(self, root="root-shutter", device_id="hdm:ZigBee:shutter1"):
        btn = ShutterRecalibrateButton.__new__(ShutterRecalibrateButton)
        dev = _make_device(device_id=device_id, root_device_id=root)
        btn._device = dev
        btn._attr_unique_id = f"{root}_{device_id}_recalibrate"
        return btn

    def test_unique_id_ends_recalibrate(self):
        btn = self._make(root="r1", device_id="d1")
        assert btn._attr_unique_id == "r1_d1_recalibrate"

    def test_translation_key(self):
        btn = self._make()
        assert btn._attr_translation_key == "shutter_recalibrate"

    def test_press_calls_async_reset_calibration_and_open(self):
        btn = self._make()
        called = []

        async def _recalibrate():
            called.append(True)

        btn._device.async_reset_calibration_and_open = _recalibrate
        asyncio.run(btn.async_press())
        assert called == [True]

    def test_press_shc_exception_raises_home_assistant_error(self):
        btn = self._make()

        async def _recalibrate():
            raise SHCException("rejected")

        btn._device.async_reset_calibration_and_open = _recalibrate
        with pytest.raises(HomeAssistantError):
            asyncio.run(btn.async_press())

    def test_is_button_entity(self):
        from homeassistant.components.button import ButtonEntity

        assert issubclass(ShutterRecalibrateButton, ButtonEntity)

    # --- async_setup_entry integration ---

    def _session_with_shutters(
        self,
        shutter_controls=(),
        micromodule_shutter_controls=(),
        micromodule_blinds=(),
    ):
        return SimpleNamespace(
            device_helper=SimpleNamespace(
                micromodule_impulse_relays=[],
                smoke_detectors=[],
                twinguards=[],
                motion_detectors2=[],
                outdoor_sirens=[],
                shutter_controls=list(shutter_controls),
                micromodule_shutter_controls=list(micromodule_shutter_controls),
                micromodule_blinds=list(micromodule_blinds),
            ),
            scenarios=[],
        )

    def test_setup_shutter_control_creates_button(self):
        dev = _make_device()
        session = self._session_with_shutters(shutter_controls=[dev])
        result = _run_setup(session)
        assert len(result) == 1
        assert isinstance(result[0], ShutterRecalibrateButton)

    def test_setup_micromodule_shutter_control_creates_button(self):
        dev = _make_device()
        session = self._session_with_shutters(micromodule_shutter_controls=[dev])
        result = _run_setup(session)
        assert len(result) == 1
        assert isinstance(result[0], ShutterRecalibrateButton)

    def test_setup_micromodule_blinds_creates_button(self):
        dev = _make_device()
        session = self._session_with_shutters(micromodule_blinds=[dev])
        result = _run_setup(session)
        assert len(result) == 1
        assert isinstance(result[0], ShutterRecalibrateButton)

    def test_setup_no_shutters_yields_nothing(self):
        session = self._session_with_shutters()
        result = _run_setup(session)
        assert result == []

    def test_setup_shutter_excluded(self):
        dev = _make_device(device_id="hdm:excluded-shutter")
        session = self._session_with_shutters(shutter_controls=[dev])
        result = _run_setup(
            session, options={"excluded_devices": ["hdm:excluded-shutter"]}
        )
        assert result == []


# ---------------------------------------------------------------------------
# Mixed async_setup_entry — all device types together
# ---------------------------------------------------------------------------


def test_setup_mixed_all_entity_types():
    """All device buckets populated → one entity per type (plus tamper)."""
    from custom_components.bosch_shc.const import OPT_SCENARIOS_AS_BUTTONS

    relay = _make_device(device_id="relay1")
    smoke = _make_device(device_id="smoke1")
    siren = _make_device(device_id="siren1")

    md2 = _make_device(device_id="md2-1")
    md2.supports_walk_test = True
    md2.walk_state = "STOPPED"
    md2.supports_detection_test = True
    md2.reset_tampered_state = lambda: None
    md2.supports_tamper_reset = True

    shc_dev = SimpleNamespace(
        identifiers={("bosch_shc", "uid")},
        name="SHC",
        manufacturer="Bosch",
        model="SmartHomeController",
    )
    sc = SimpleNamespace(id="sc-1", name="Goodnight")

    session = SimpleNamespace(
        device_helper=SimpleNamespace(
            micromodule_impulse_relays=[relay],
            smoke_detectors=[smoke],
            twinguards=[],
            motion_detectors2=[md2],
            outdoor_sirens=[siren],
        ),
        scenarios=[sc],
    )

    result = _run_setup(
        session,
        options={OPT_SCENARIOS_AS_BUTTONS: True},
        unique_id="uid",
        shc_device=shc_dev,
    )

    types = [type(e) for e in result]
    assert SHCRelayButton in types
    assert SHCSmokeTestButton in types
    assert SHCWalkTestButton in types
    assert SHCWalkTestStopButton in types
    assert SHCDetectionTestButton in types
    assert SHCDetectionTestStopButton in types
    assert SHCTamperResetButton in types
    assert SHCScenarioButton in types
    assert SHCSirenTestAlarmButton in types


def test_setup_empty_all_buckets_yields_nothing():
    """All device buckets empty → nothing added."""
    session = SimpleNamespace(
        device_helper=SimpleNamespace(
            micromodule_impulse_relays=[],
            smoke_detectors=[],
            twinguards=[],
            motion_detectors2=[],
            outdoor_sirens=[],
        ),
        scenarios=[],
    )
    result = _run_setup(session)
    assert result == []
