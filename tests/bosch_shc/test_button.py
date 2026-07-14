"""Unit tests for the button platform (custom_components.bosch_shc.button).

Covers every ButtonEntity subclass: SHCRelayButton (impulse relay),
SHCSmokeTestButton (smoke detector/twinguard), SHCWalkTestButton /
SHCWalkTestStopButton and SHCDetectionTestButton / SHCDetectionTestStopButton
(MD2 walk-test / detection-test self-tests), SHCTamperResetButton,
SHCScenarioButton (scenario-as-button, does not inherit SHCEntity),
SHCSirenTestAlarmButton, ResetEnergySummationButton, ShutterRecalibrateButton,
and the dimmer DimmerPreviewMaxButton / DimmerPreviewMinButton pair — plus
async_setup_entry wiring for every device bucket (including excluded-device
and option-gated branches) and a few quality-scale regression pins
(unique_id format, has_entity_name, device_info) for SHCScenarioButton.

Pattern: `Cls.__new__(Cls)` bypasses SHCEntity.__init__ (no hass / device
registry needed) for most tests; a handful exercise the real `__init__` with
`_update_attr` patched to a no-op so the super().__init__ path is covered.
async_press tests drive the coroutine via asyncio.run() directly, and
async_setup_entry tests drive the shared `mock_config_entry`/`mock_session`
fixtures (see conftest.py) through the platform's real async_setup_entry —
no HA test harness, no network.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.button import ButtonEntity
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory

from boschshcpy.exceptions import SHCException
from custom_components.bosch_shc.button import (
    DimmerPreviewMaxButton,
    DimmerPreviewMinButton,
    ResetEnergySummationButton,
    SHCDetectionTestButton,
    SHCDetectionTestStopButton,
    SHCEnableAllDiagnosticsButton,
    SHCRelayButton,
    SHCScenarioButton,
    SHCSirenTestAlarmButton,
    SHCSmokeTestButton,
    SHCTamperResetButton,
    SHCWalkTestButton,
    SHCWalkTestStopButton,
    ShutterRecalibrateButton,
    async_setup_entry,
)
from custom_components.bosch_shc.const import (
    OPT_EXCLUDED_DEVICES,
    OPT_SCENARIOS_AS_BUTTONS,
)
from custom_components.bosch_shc.entity import SHCEntity

from .conftest import run_setup_entry

# ---------------------------------------------------------------------------
# Shared helpers — general device fixtures (test_button.py)
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


def _without_diagnostics_button(entities: list) -> list:
    """Strip the always-created SHCEnableAllDiagnosticsButton from a result list.

    That button is a single hub-level entity created unconditionally by
    async_setup_entry, orthogonal to every per-device-bucket test in this
    file — those tests assert on device-specific entity creation and were
    written before the button existed, so filter it out here rather than
    editing dozens of individual assertions.
    """
    return [e for e in entities if not isinstance(e, SHCEnableAllDiagnosticsButton)]


def _run_setup(mock_config_entry, mock_session) -> list:
    """Drive button.async_setup_entry via the shared conftest fixtures.

    Replaces this file's old bespoke `_run_setup`/`_run_setup_with_entry`/
    `_run_button_setup`/`_setup_buttons` helpers (all the same shape: wire a
    session onto a config entry, call async_setup_entry, collect entities) —
    and filters out the always-present hub diagnostics button, which every
    device-bucket test in this file predates and ignores.

    button.py's async_setup_entry unconditionally builds a
    SHCEnableAllDiagnosticsButton from config_entry.unique_id and
    config_entry.runtime_data.shc_device — attributes the shared
    mock_config_entry fixture doesn't set by default (unlike session, which
    conftest's own run_setup_entry wires up). Default them here so every
    test in this file doesn't have to; a test that cares about a specific
    value still wins by setting it before calling this helper.
    """
    if not hasattr(mock_config_entry, "unique_id"):
        mock_config_entry.unique_id = None
    if not hasattr(mock_config_entry.runtime_data, "shc_device"):
        mock_config_entry.runtime_data.shc_device = None
    result = asyncio.run(
        run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
    )
    return _without_diagnostics_button(result)


def _button_device() -> SimpleNamespace:
    """Minimal device for SHCRelayButton.__init__ (fixed root/id, from
    test_platforms_setup.py's async_setup_entry-integration fixtures)."""
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


def _smoke_test_device(
    name: str = "Test Rauchmelder",
    device_id: str = "hdm:ZigBee:smoke1",
    root_device_id: str = "aa:bb:cc:00:00:07",
) -> SimpleNamespace:
    """Minimal device for SHCSmokeTestButton.__init__."""
    return SimpleNamespace(
        name=name,
        id=device_id,
        root_device_id=root_device_id,
        serial=f"serial-{device_id}",
        device_services=[],
        manufacturer="Bosch",
        device_model="SMOKE",
        status="AVAILABLE",
        deleted=False,
        room_id=None,
        smoketest_requested=lambda: None,
    )


# ---------------------------------------------------------------------------
# Shared helpers — coverage-gap fixtures (test_button_coverage.py)
# ---------------------------------------------------------------------------


def _fake_shc_device() -> SimpleNamespace:
    """Minimal DeviceEntry-like double for the SHC controller."""
    return SimpleNamespace(
        identifiers={("bosch_shc", "shc-controller-001")},
        name="Smart Home Controller",
        manufacturer="Bosch",
        model="SmartHomeController",
    )


def _good_scenario(sid="sc-001", name="Morning Lights"):
    return SimpleNamespace(id=sid, name=name, trigger=lambda: None)


# ---------------------------------------------------------------------------
# Shared helpers — APK coverage-gap fixtures (test_apk_coverage_gaps.py)
# ---------------------------------------------------------------------------


def _fake_device_kw(**kwargs):
    """Kwargs-flexible device double (distinct from `_make_device` above,
    which takes named params) — kept separate because callers rely on the
    "pass anything" shape (e.g. `walk_state=object()`)."""
    base = dict(
        id="dev1",
        root_device_id="root1",
        name="FakeDev",
        device_services=[],
        serial="SER1",
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _excl(*ids):
    return {OPT_EXCLUDED_DEVICES: list(ids)}


# ---------------------------------------------------------------------------
# Shared helpers — MD2 walk/detection/tamper fixtures
# (test_apk_walktest_and_sensitivity.py + test_md2_detection_tamper_pollcontrol.py
#  define near-identical `_fake_md2`; deduped to one copy here.)
# ---------------------------------------------------------------------------


def _fake_md2(**kwargs):
    defaults = dict(
        name="MD2",
        id="md1",
        root_device_id="root1",
        serial="SER1",
        supports_batterylevel=False,
        supports_silentmode=False,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# Shared helpers — dimmer preview fixtures (test_coverage_gaps.py /
# test_dimmer_config_entities.py)
# ---------------------------------------------------------------------------


def _run(coro):
    return asyncio.run(coro)


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


def _new(cls):
    return cls.__new__(cls)


def _relay_via_init(
    name: str = "Relay 1",
    device_id: str = "hdm:HomeMaticIP:abc123",
    root_device_id: str = "aa:bb:cc:00:00:01",
    attr_name: str | None = None,
) -> SHCRelayButton:
    """Create SHCRelayButton by calling __init__ (exercises the
    super().__init__ path). _update_attr is patched to a no-op so
    SHCEntity.__init__ completes without a real HA instance."""
    dev = _make_device(name=name, device_id=device_id, root_device_id=root_device_id)
    btn = SHCRelayButton.__new__(SHCRelayButton)
    with patch.object(SHCRelayButton, "_update_attr", lambda self: None):
        SHCRelayButton.__init__(btn, dev, "entry_test", attr_name)
    return btn


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
        assert issubclass(SHCRelayButton, ButtonEntity)

    def test_is_shc_entity(self):
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

    def test_setup_impulse_relay_creates_relay_button(
        self, mock_config_entry, mock_session
    ):
        dev = _make_device()
        mock_session.device_helper.micromodule_impulse_relays = [dev]
        result = _run_setup(mock_config_entry, mock_session)
        assert len(result) == 1
        assert isinstance(result[0], SHCRelayButton)

    def test_setup_no_relays_yields_nothing(self, mock_config_entry, mock_session):
        result = _run_setup(mock_config_entry, mock_session)
        assert result == []

    def test_setup_multiple_relays(self, mock_config_entry, mock_session):
        mock_session.device_helper.micromodule_impulse_relays = [
            _make_device(),
            _make_device(),
        ]
        result = _run_setup(mock_config_entry, mock_session)
        assert len(result) == 2
        assert all(isinstance(e, SHCRelayButton) for e in result)

    def test_setup_entry_id_stored(self, mock_config_entry, mock_session):
        dev = _make_device()
        mock_session.device_helper.micromodule_impulse_relays = [dev]
        result = _run_setup(mock_config_entry, mock_session)
        assert result[0]._entry_id == "E1"

    def test_setup_excluded_device_skipped(self, mock_config_entry, mock_session):
        dev = _make_device(device_id="hdm:excluded")
        mock_session.device_helper.micromodule_impulse_relays = [dev]
        mock_config_entry.options = {"excluded_devices": ["hdm:excluded"]}
        result = _run_setup(mock_config_entry, mock_session)
        assert result == []


class TestSHCRelayButtonInit:
    """Exercise the actual __init__ (via patched _update_attr) so
    super().__init__'s lines are covered — TestSHCRelayButton above only
    ever builds instances via __new__ bypass."""

    def test_name_without_attr_name(self):
        # With _attr_has_entity_name=True and _attr_name=None, HA uses the device
        # name as the entity name (primary entity).  _attr_name itself is None.
        btn = _relay_via_init(name="Keller Relay", attr_name=None)
        assert btn._attr_name is None

    def test_name_with_attr_name(self):
        # _attr_name stores only the feature label; HA prepends device name for
        # display (e.g. "Garage CH1").
        btn = _relay_via_init(name="Garage", attr_name="CH1")
        assert btn._attr_name == "CH1"

    def test_unique_id_without_attr_name(self):
        btn = _relay_via_init(
            root_device_id="aa:bb:cc:00:00:01",
            device_id="hdm:HomeMaticIP:abc123",
            attr_name=None,
        )
        assert btn._attr_unique_id == "aa:bb:cc:00:00:01_hdm:HomeMaticIP:abc123"

    def test_unique_id_with_attr_name_lowercased(self):
        btn = _relay_via_init(
            root_device_id="aa:bb:cc:00:00:02",
            device_id="hdm:HomeMaticIP:xyz789",
            attr_name="Channel A",
        )
        assert (
            btn._attr_unique_id == "aa:bb:cc:00:00:02_hdm:HomeMaticIP:xyz789_channel a"
        )

    def test_unique_id_attr_name_already_lowercase(self):
        btn = _relay_via_init(
            root_device_id="root1",
            device_id="dev1",
            attr_name="impulse",
        )
        assert btn._attr_unique_id == "root1_dev1_impulse"

    def test_device_stored(self):
        btn = _relay_via_init(name="Switch A")
        assert btn._device.name == "Switch A"

    def test_entry_id_stored(self):
        btn = _relay_via_init()
        assert btn._entry_id == "entry_test"

    def test_name_attr_name_none_equals_device_name(self):
        # _attr_name=None → primary entity; HA resolves to device name for display.
        btn = _relay_via_init(name="Single Relay", attr_name=None)
        assert btn._attr_name is None

    def test_name_attr_name_provided_appended(self):
        # _attr_name holds only the feature label (no device prefix).
        btn = _relay_via_init(name="Multi Relay", attr_name="Output 2")
        assert btn._attr_name == "Output 2"

    def test_unique_id_attr_name_uppercased_is_lowercased(self):
        btn = _relay_via_init(root_device_id="r1", device_id="d1", attr_name="OUTPUT")
        assert btn._attr_unique_id == "r1_d1_output"


class TestPress:
    """Press behaviour for a relay button built via the real __init__."""

    def test_press_calls_trigger_impulse_state_via_init(self):
        btn = _relay_via_init()
        triggered = []

        async def _trig():
            triggered.append(True)

        btn._device.async_trigger_impulse_state = _trig
        asyncio.run(btn.async_press())
        assert triggered == [True]

    def test_press_called_twice_triggers_twice_via_init(self):
        btn = _relay_via_init()
        count = []

        async def _trig():
            count.append(1)

        btn._device.async_trigger_impulse_state = _trig
        asyncio.run(btn.async_press())
        asyncio.run(btn.async_press())
        assert len(count) == 2

    def test_press_returns_none(self):
        btn = _relay_via_init()

        async def _trig():
            return None

        btn._device.async_trigger_impulse_state = _trig
        assert asyncio.run(btn.async_press()) is None


class TestStructural:
    """Class-level / MRO properties of SHCRelayButton."""

    def test_is_button_entity(self):
        assert issubclass(SHCRelayButton, ButtonEntity)

    def test_is_shc_entity(self):
        assert issubclass(SHCRelayButton, SHCEntity)

    def test_no_device_class_at_class_level(self):
        assert not hasattr(SHCRelayButton, "_attr_device_class") or (
            SHCRelayButton.__dict__.get("_attr_device_class") is None
        )

    def test_mro_shcrelaybutton_before_buttonentity(self):
        """SHCRelayButton must appear before ButtonEntity in the MRO."""
        mro = SHCRelayButton.__mro__
        idx_self = mro.index(SHCRelayButton)
        idx_btn = mro.index(ButtonEntity)
        assert idx_self < idx_btn

    def test_mro_shcentity_in_chain(self):
        assert SHCEntity in SHCRelayButton.__mro__


class TestImpulseRelayExcluded:
    """button.py — micromodule_impulse_relays: excluded device is skipped."""

    def test_excluded_relay_is_not_added(self, mock_config_entry, mock_session):
        """device_excluded returns True → continue → device not in entities."""
        dev = _make_device(device_id="relay-excl")
        mock_session.device_helper.micromodule_impulse_relays = [dev]
        mock_config_entry.options = {OPT_EXCLUDED_DEVICES: ["relay-excl"]}
        result = _run_setup(mock_config_entry, mock_session)
        assert result == []

    def test_non_excluded_relay_is_added(self, mock_config_entry, mock_session):
        """Sanity: a relay NOT excluded IS added (false branch)."""
        dev = _make_device(device_id="relay-ok")
        mock_session.device_helper.micromodule_impulse_relays = [dev]
        result = _run_setup(mock_config_entry, mock_session)
        assert len(result) == 1

    def test_mixed_relays_only_non_excluded_added(self, mock_config_entry, mock_session):
        excl = _make_device(device_id="relay-excl")
        ok = _make_device(device_id="relay-ok")
        mock_session.device_helper.micromodule_impulse_relays = [excl, ok]
        mock_config_entry.options = {OPT_EXCLUDED_DEVICES: ["relay-excl"]}
        result = _run_setup(mock_config_entry, mock_session)
        assert len(result) == 1


class TestButtonSetupEntry:
    """Button async_setup_entry for relays and smoke-test buttons (fixed
    device fixtures, from test_platforms_setup.py)."""

    def test_impulse_relays_produce_relay_button_entities(
        self, mock_config_entry, mock_session
    ) -> None:
        """micromodule_impulse_relays → SHCRelayButton."""
        dev = _button_device()
        mock_session.device_helper.micromodule_impulse_relays = [dev]
        result = _run_setup(mock_config_entry, mock_session)
        assert len(result) == 1
        assert isinstance(result[0], SHCRelayButton)

    def test_no_relays_adds_nothing(self, mock_config_entry, mock_session) -> None:
        """No relays → nothing added."""
        result = _run_setup(mock_config_entry, mock_session)
        assert result == []

    def test_entity_name_from_device(self, mock_config_entry, mock_session) -> None:
        """SHCRelayButton._attr_name is None (no attr_name passed).

        With _attr_has_entity_name=True and _attr_name=None, HA uses the device
        name as the entity name (primary entity pattern).
        """
        dev = _button_device()
        mock_session.device_helper.micromodule_impulse_relays = [dev]
        result = _run_setup(mock_config_entry, mock_session)
        assert result[0]._attr_name is None

    def test_unique_id_from_root_and_device_id(
        self, mock_config_entry, mock_session
    ) -> None:
        """unique_id = root_device_id + '_' + device_id."""
        dev = _button_device()
        mock_session.device_helper.micromodule_impulse_relays = [dev]
        result = _run_setup(mock_config_entry, mock_session)
        assert result[0]._attr_unique_id == "aa:bb:cc:00:00:06_hdm:HomeMaticIP:relay1"

    def test_multiple_relays_all_collected(self, mock_config_entry, mock_session) -> None:
        """Two relays → two SHCRelayButton entities."""
        mock_session.device_helper.micromodule_impulse_relays = [
            _button_device(),
            _button_device(),
        ]
        result = _run_setup(mock_config_entry, mock_session)
        assert len(result) == 2
        assert all(isinstance(e, SHCRelayButton) for e in result)

    def test_entry_id_stored(self, mock_config_entry, mock_session) -> None:
        dev = _button_device()
        mock_session.device_helper.micromodule_impulse_relays = [dev]
        result = _run_setup(mock_config_entry, mock_session)
        assert result[0]._entry_id == "E1"

    def test_smoke_detectors_produce_smoke_test_buttons(
        self, mock_config_entry, mock_session
    ) -> None:
        dev = _smoke_test_device()
        mock_session.device_helper.smoke_detectors = [dev]
        result = _run_setup(mock_config_entry, mock_session)
        assert len(result) == 1
        assert isinstance(result[0], SHCSmokeTestButton)

    def test_twinguards_produce_smoke_test_buttons(
        self, mock_config_entry, mock_session
    ) -> None:
        dev = _smoke_test_device(name="TwinGuard", device_id="hdm:ZigBee:tw1")
        mock_session.device_helper.twinguards = [dev]
        result = _run_setup(mock_config_entry, mock_session)
        assert len(result) == 1
        assert isinstance(result[0], SHCSmokeTestButton)


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
        assert issubclass(SHCSmokeTestButton, ButtonEntity)

    # async_setup_entry integration

    def test_setup_smoke_detector_creates_smoke_test_button(
        self, mock_config_entry, mock_session
    ):
        dev = _make_device()
        mock_session.device_helper.smoke_detectors = [dev]
        result = _run_setup(mock_config_entry, mock_session)
        assert len(result) == 1
        assert isinstance(result[0], SHCSmokeTestButton)

    def test_setup_twinguard_creates_smoke_test_button(
        self, mock_config_entry, mock_session
    ):
        dev = _make_device()
        mock_session.device_helper.twinguards = [dev]
        result = _run_setup(mock_config_entry, mock_session)
        assert len(result) == 1
        assert isinstance(result[0], SHCSmokeTestButton)

    def test_setup_both_smoke_and_twinguard(self, mock_config_entry, mock_session):
        mock_session.device_helper.smoke_detectors = [_make_device(device_id="s1")]
        mock_session.device_helper.twinguards = [_make_device(device_id="t1")]
        result = _run_setup(mock_config_entry, mock_session)
        assert len(result) == 2
        assert all(isinstance(e, SHCSmokeTestButton) for e in result)

    def test_setup_unique_id_includes_smoke_test_suffix(
        self, mock_config_entry, mock_session
    ):
        dev = _make_device(root_device_id="root1", device_id="dev1")
        mock_session.device_helper.smoke_detectors = [dev]
        result = _run_setup(mock_config_entry, mock_session)
        assert result[0]._attr_unique_id == "root1_dev1_smoke_test"


class TestSHCSmokeTestButtonInit:
    """Exercise the real __init__ (via patched _update_attr) — parallel to
    TestSHCRelayButtonInit above, for SHCSmokeTestButton."""

    def _smoke_button_via_init(
        self,
        name: str = "TwinGuard 1",
        device_id: str = "hdm:ZigBee:abc123",
        root_device_id: str = "aa:bb:cc:00:00:99",
    ) -> SHCSmokeTestButton:
        dev = _make_device(
            name=name, device_id=device_id, root_device_id=root_device_id
        )
        dev.smoketest_requested = lambda: None
        btn = SHCSmokeTestButton.__new__(SHCSmokeTestButton)
        with patch.object(SHCSmokeTestButton, "_update_attr", lambda self: None):
            SHCSmokeTestButton.__init__(btn, dev, "entry_test")
        return btn

    def test_attr_name(self):
        btn = self._smoke_button_via_init()
        assert btn.translation_key == "smoke_test"

    def test_unique_id(self):
        btn = self._smoke_button_via_init(
            device_id="hdm:ZigBee:dev1", root_device_id="root1"
        )
        assert btn._attr_unique_id == "root1_hdm:ZigBee:dev1_smoke_test"

    def test_press_calls_smoketest_requested(self):
        calls = []
        btn = self._smoke_button_via_init()

        async def _smoke():
            calls.append(True)

        btn._device.async_smoketest_requested = _smoke
        asyncio.run(btn.async_press())
        assert calls == [True]

    def test_is_button_entity(self):
        assert issubclass(SHCSmokeTestButton, ButtonEntity)

    def test_is_shc_entity(self):
        assert issubclass(SHCSmokeTestButton, SHCEntity)


class TestSmokeDetectorExcluded:
    """button.py — smoke_detectors: excluded device is skipped."""

    def test_excluded_smoke_detector_not_added(self, mock_config_entry, mock_session):
        dev = _make_device(device_id="smoke-excl")
        mock_session.device_helper.smoke_detectors = [dev]
        mock_config_entry.options = {OPT_EXCLUDED_DEVICES: ["smoke-excl"]}
        result = _run_setup(mock_config_entry, mock_session)
        assert result == []

    def test_non_excluded_smoke_detector_is_added(
        self, mock_config_entry, mock_session
    ):
        dev = _make_device(device_id="smoke-ok")
        mock_session.device_helper.smoke_detectors = [dev]
        result = _run_setup(mock_config_entry, mock_session)
        assert len(result) == 1
        assert isinstance(result[0], SHCSmokeTestButton)

    def test_excluded_smoke_detector_yields_smoke_test_button(
        self, mock_config_entry, mock_session
    ):
        """When not excluded, the entity type is SHCSmokeTestButton."""
        ok = _make_device(device_id="smoke-ok2")
        excl = _make_device(device_id="smoke-excl2")
        mock_session.device_helper.smoke_detectors = [excl, ok]
        mock_config_entry.options = {OPT_EXCLUDED_DEVICES: ["smoke-excl2"]}
        result = _run_setup(mock_config_entry, mock_session)
        assert len(result) == 1
        assert isinstance(result[0], SHCSmokeTestButton)


class TestTwinguardExcluded:
    """button.py — twinguards: excluded device is skipped."""

    def test_excluded_twinguard_not_added(self, mock_config_entry, mock_session):
        dev = _make_device(device_id="tg-excl")
        mock_session.device_helper.twinguards = [dev]
        mock_config_entry.options = {OPT_EXCLUDED_DEVICES: ["tg-excl"]}
        result = _run_setup(mock_config_entry, mock_session)
        assert result == []

    def test_non_excluded_twinguard_is_added_as_smoke_test_button(
        self, mock_config_entry, mock_session
    ):
        dev = _make_device(device_id="tg-ok")
        mock_session.device_helper.twinguards = [dev]
        result = _run_setup(mock_config_entry, mock_session)
        assert len(result) == 1
        assert isinstance(result[0], SHCSmokeTestButton)

    def test_mixed_twinguards_only_non_excluded_added(
        self, mock_config_entry, mock_session
    ):
        excl = _make_device(device_id="tg-excl")
        ok = _make_device(device_id="tg-ok")
        mock_session.device_helper.twinguards = [excl, ok]
        mock_config_entry.options = {OPT_EXCLUDED_DEVICES: ["tg-excl"]}
        result = _run_setup(mock_config_entry, mock_session)
        assert len(result) == 1


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
        assert issubclass(SHCSirenTestAlarmButton, ButtonEntity)

    # --- async_setup_entry integration ---

    def test_setup_siren_creates_test_alarm_button(
        self, mock_config_entry, mock_session
    ):
        dev = _make_device()
        mock_session.device_helper.outdoor_sirens = [dev]
        result = _run_setup(mock_config_entry, mock_session)
        assert len(result) == 1
        assert isinstance(result[0], SHCSirenTestAlarmButton)

    def test_setup_no_sirens_yields_nothing(self, mock_config_entry, mock_session):
        result = _run_setup(mock_config_entry, mock_session)
        assert result == []

    def test_setup_multiple_sirens(self, mock_config_entry, mock_session):
        mock_session.device_helper.outdoor_sirens = [
            _make_device(device_id="s1"),
            _make_device(device_id="s2"),
        ]
        result = _run_setup(mock_config_entry, mock_session)
        assert len(result) == 2
        assert all(isinstance(e, SHCSirenTestAlarmButton) for e in result)

    def test_setup_siren_excluded(self, mock_config_entry, mock_session):
        dev = _make_device(device_id="hdm:excluded-siren")
        mock_session.device_helper.outdoor_sirens = [dev]
        mock_config_entry.options = {"excluded_devices": ["hdm:excluded-siren"]}
        result = _run_setup(mock_config_entry, mock_session)
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
        assert issubclass(ResetEnergySummationButton, ButtonEntity)

    # --- async_setup_entry integration ---

    def test_setup_smart_plug_creates_button(self, mock_config_entry, mock_session):
        dev = _make_device()
        mock_session.device_helper.smart_plugs = [dev]
        result = _run_setup(mock_config_entry, mock_session)
        assert len(result) == 1
        assert isinstance(result[0], ResetEnergySummationButton)

    def test_setup_smart_plug_compact_creates_button(
        self, mock_config_entry, mock_session
    ):
        dev = _make_device()
        mock_session.device_helper.smart_plugs_compact = [dev]
        result = _run_setup(mock_config_entry, mock_session)
        assert len(result) == 1
        assert isinstance(result[0], ResetEnergySummationButton)

    def test_setup_no_plugs_yields_nothing(self, mock_config_entry, mock_session):
        result = _run_setup(mock_config_entry, mock_session)
        assert result == []

    def test_setup_plug_excluded(self, mock_config_entry, mock_session):
        dev = _make_device(device_id="hdm:excluded-plug")
        mock_session.device_helper.smart_plugs = [dev]
        mock_config_entry.options = {"excluded_devices": ["hdm:excluded-plug"]}
        result = _run_setup(mock_config_entry, mock_session)
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
        assert issubclass(ShutterRecalibrateButton, ButtonEntity)

    # --- async_setup_entry integration ---

    def test_setup_shutter_control_creates_button(
        self, mock_config_entry, mock_session
    ):
        dev = _make_device()
        mock_session.device_helper.shutter_controls = [dev]
        result = _run_setup(mock_config_entry, mock_session)
        assert len(result) == 1
        assert isinstance(result[0], ShutterRecalibrateButton)

    def test_setup_micromodule_shutter_control_creates_button(
        self, mock_config_entry, mock_session
    ):
        dev = _make_device()
        mock_session.device_helper.micromodule_shutter_controls = [dev]
        result = _run_setup(mock_config_entry, mock_session)
        assert len(result) == 1
        assert isinstance(result[0], ShutterRecalibrateButton)

    def test_setup_micromodule_blinds_creates_button(
        self, mock_config_entry, mock_session
    ):
        dev = _make_device()
        mock_session.device_helper.micromodule_blinds = [dev]
        result = _run_setup(mock_config_entry, mock_session)
        assert len(result) == 1
        assert isinstance(result[0], ShutterRecalibrateButton)

    def test_setup_no_shutters_yields_nothing(self, mock_config_entry, mock_session):
        result = _run_setup(mock_config_entry, mock_session)
        assert result == []

    def test_setup_shutter_excluded(self, mock_config_entry, mock_session):
        dev = _make_device(device_id="hdm:excluded-shutter")
        mock_session.device_helper.shutter_controls = [dev]
        mock_config_entry.options = {"excluded_devices": ["hdm:excluded-shutter"]}
        result = _run_setup(mock_config_entry, mock_session)
        assert result == []


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
        # Icon comes from icons.json via translation_key (not a hardcoded
        # _attr_icon) — same convention as every other button entity here.
        btn = self._make_scenario()
        assert btn._attr_translation_key == "scenario"

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
        assert issubclass(SHCScenarioButton, ButtonEntity)

    # --- async_setup_entry integration ---

    def test_setup_scenario_buttons_created_when_option_enabled(
        self, mock_config_entry, mock_session
    ):
        shc_dev = SimpleNamespace(
            identifiers={("bosch_shc", "uid")},
            name="SHC",
            manufacturer="Bosch",
            model="SmartHomeController",
        )
        sc = SimpleNamespace(id="sc-1", name="Away")
        mock_session.scenarios = [sc]
        mock_config_entry.options = {OPT_SCENARIOS_AS_BUTTONS: True}
        mock_config_entry.unique_id = "uid"
        mock_config_entry.runtime_data.shc_device = shc_dev
        result = _run_setup(mock_config_entry, mock_session)
        scenario_buttons = [e for e in result if isinstance(e, SHCScenarioButton)]
        assert len(scenario_buttons) == 1
        assert scenario_buttons[0]._attr_name == "Away"

    def test_setup_no_scenario_buttons_when_option_disabled(
        self, mock_config_entry, mock_session
    ):
        sc = SimpleNamespace(id="sc-1", name="Away")
        mock_session.scenarios = [sc]
        mock_config_entry.options = {OPT_SCENARIOS_AS_BUTTONS: False}
        result = _run_setup(mock_config_entry, mock_session)
        assert not any(isinstance(e, SHCScenarioButton) for e in result)

    def test_setup_multiple_scenario_buttons(self, mock_config_entry, mock_session):
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
        mock_session.scenarios = scenarios
        mock_config_entry.options = {OPT_SCENARIOS_AS_BUTTONS: True}
        mock_config_entry.unique_id = "uid"
        mock_config_entry.runtime_data.shc_device = shc_dev
        result = _run_setup(mock_config_entry, mock_session)
        sb = [e for e in result if isinstance(e, SHCScenarioButton)]
        assert len(sb) == 2

    def test_setup_bad_scenario_skipped_not_crash(self, mock_config_entry, mock_session):
        """A malformed scenario (missing id/name) must be skipped gracefully."""
        shc_dev = SimpleNamespace(
            identifiers={("bosch_shc", "uid")},
            name="SHC",
            manufacturer="Bosch",
            model="SmartHomeController",
        )
        # No .id attribute → AttributeError → should be caught and skipped.
        bad_sc = SimpleNamespace(name="No ID")
        good_sc = SimpleNamespace(id="ok", name="Good")
        mock_session.scenarios = [bad_sc, good_sc]
        mock_config_entry.options = {OPT_SCENARIOS_AS_BUTTONS: True}
        mock_config_entry.unique_id = "uid"
        mock_config_entry.runtime_data.shc_device = shc_dev
        # This must not raise; the bad scenario is skipped.
        result = _run_setup(mock_config_entry, mock_session)
        sb = [e for e in result if isinstance(e, SHCScenarioButton)]
        # Only the good scenario makes it through.
        assert len(sb) == 1
        assert sb[0]._attr_name == "Good"


class TestScenariosAsButtonsBlock:
    """OPT_SCENARIOS_AS_BUTTONS=True → scenarios become SHCScenarioButton
    entities (coverage-gap tests, incl. KeyError/AttributeError skip path)."""

    def test_scenarios_as_buttons_false_by_default(
        self, mock_config_entry, mock_session
    ):
        """When option is absent / False, no scenario buttons are added."""
        sc = _good_scenario()
        mock_session.scenarios = [sc]
        result = _run_setup(mock_config_entry, mock_session)
        assert result == []

    def test_scenarios_as_buttons_true_adds_button(
        self, mock_config_entry, mock_session
    ):
        sc = _good_scenario(sid="sc-001", name="Morning")
        mock_session.scenarios = [sc]
        mock_config_entry.options = {OPT_SCENARIOS_AS_BUTTONS: True}
        mock_config_entry.unique_id = "uid-001"
        mock_config_entry.runtime_data.shc_device = None
        result = _run_setup(mock_config_entry, mock_session)
        assert len(result) == 1
        assert isinstance(result[0], SHCScenarioButton)

    def test_scenario_unique_id_uses_entry_unique_id(
        self, mock_config_entry, mock_session
    ):
        sc = _good_scenario(sid="sc-abc")
        mock_session.scenarios = [sc]
        mock_config_entry.options = {OPT_SCENARIOS_AS_BUTTONS: True}
        mock_config_entry.unique_id = "uid-xyz"
        mock_config_entry.runtime_data.shc_device = None
        result = _run_setup(mock_config_entry, mock_session)
        assert result[0]._attr_unique_id == "uid-xyz_scenario_sc-abc"

    def test_scenario_name_is_scenario_name(self, mock_config_entry, mock_session):
        sc = _good_scenario(sid="sc-001", name="Evening Scene")
        mock_session.scenarios = [sc]
        mock_config_entry.options = {OPT_SCENARIOS_AS_BUTTONS: True}
        mock_config_entry.unique_id = "uid-001"
        mock_config_entry.runtime_data.shc_device = None
        result = _run_setup(mock_config_entry, mock_session)
        assert result[0]._attr_name == "Evening Scene"

    def test_multiple_scenarios_all_added(self, mock_config_entry, mock_session):
        scenarios = [
            _good_scenario(sid="sc-001", name="Scene A"),
            _good_scenario(sid="sc-002", name="Scene B"),
            _good_scenario(sid="sc-003", name="Scene C"),
        ]
        mock_session.scenarios = scenarios
        mock_config_entry.options = {OPT_SCENARIOS_AS_BUTTONS: True}
        mock_config_entry.unique_id = "uid-001"
        mock_config_entry.runtime_data.shc_device = None
        result = _run_setup(mock_config_entry, mock_session)
        assert len(result) == 3

    def test_keyerror_on_scenario_logs_warning_and_skips(
        self, mock_config_entry, mock_session
    ):
        """A scenario whose attribute access raises KeyError is skipped, not fatal."""

        class _BadScenario:
            @property
            def id(self):
                raise KeyError("id missing")

            @property
            def name(self):
                return "Bad"

        bad = _BadScenario()
        good = _good_scenario(sid="sc-good", name="Good")
        mock_session.scenarios = [bad, good]
        mock_config_entry.options = {OPT_SCENARIOS_AS_BUTTONS: True}
        mock_config_entry.unique_id = "uid-001"
        mock_config_entry.runtime_data.shc_device = None

        with patch("custom_components.bosch_shc.button.LOGGER") as mock_log:
            result = _run_setup(mock_config_entry, mock_session)

        mock_log.warning.assert_called_once()
        assert len(result) == 1
        assert isinstance(result[0], SHCScenarioButton)

    def test_attribute_error_on_scenario_logs_warning_and_skips(
        self, mock_config_entry, mock_session
    ):
        """A scenario whose attribute access raises AttributeError is skipped."""

        class _BadScenario:
            @property
            def id(self):
                raise AttributeError("no id attr")

            name = "Bad"

        bad = _BadScenario()
        good = _good_scenario(sid="sc-good2", name="Good2")
        mock_session.scenarios = [bad, good]
        mock_config_entry.options = {OPT_SCENARIOS_AS_BUTTONS: True}
        mock_config_entry.unique_id = "uid-001"
        mock_config_entry.runtime_data.shc_device = None

        with patch("custom_components.bosch_shc.button.LOGGER") as mock_log:
            result = _run_setup(mock_config_entry, mock_session)

        mock_log.warning.assert_called_once()
        assert len(result) == 1

    def test_all_bad_scenarios_yields_empty(self, mock_config_entry, mock_session):
        """All malformed scenarios → empty entity list (async_add_entities not called)."""

        class _Bad:
            @property
            def id(self):
                raise KeyError("no id")

            name = "Bad"

        mock_session.scenarios = [_Bad(), _Bad()]
        mock_config_entry.options = {OPT_SCENARIOS_AS_BUTTONS: True}
        mock_config_entry.unique_id = "uid-001"
        mock_config_entry.runtime_data.shc_device = None
        result = _run_setup(mock_config_entry, mock_session)
        assert result == []


class TestSHCScenarioButtonInit:
    """SHCScenarioButton.__init__ with entry_unique_id=None (prefix falls
    back to entry_id)."""

    def test_prefix_uses_entry_unique_id_when_set(self):
        """With a real entry_unique_id the unique_id is prefixed by it."""
        sc = _good_scenario(sid="sc-001")
        btn = SHCScenarioButton(
            scenario=sc, entry_unique_id="uid-abc", entry_id="entry-xyz"
        )
        assert btn._attr_unique_id == "uid-abc_scenario_sc-001"

    def test_prefix_falls_back_to_entry_id_when_unique_id_none(self):
        """entry_unique_id=None → prefix is entry_id."""
        sc = _good_scenario(sid="sc-002")
        btn = SHCScenarioButton(
            scenario=sc, entry_unique_id=None, entry_id="fallback-entry"
        )
        assert btn._attr_unique_id == "fallback-entry_scenario_sc-002"

    def test_name_set_from_scenario(self):
        sc = _good_scenario(name="My Scene")
        btn = SHCScenarioButton(scenario=sc, entry_unique_id="uid-1", entry_id="entry-1")
        assert btn._attr_name == "My Scene"

    def test_scenario_stored(self):
        sc = _good_scenario()
        btn = SHCScenarioButton(scenario=sc, entry_unique_id="uid-1", entry_id="entry-1")
        assert btn._scenario is sc

    def test_icon_is_script_play(self):
        # Icon comes from icons.json via translation_key (not a hardcoded
        # _attr_icon) — same convention as every other button entity here.
        sc = _good_scenario()
        btn = SHCScenarioButton(scenario=sc, entry_unique_id=None, entry_id="entry-1")
        assert btn._attr_translation_key == "scenario"

    def test_should_poll_is_false(self):
        sc = _good_scenario()
        btn = SHCScenarioButton(scenario=sc, entry_unique_id=None, entry_id="entry-1")
        assert btn._attr_should_poll is False


class TestSHCScenarioButtonPress:
    """SHCScenarioButton.press() calls scenario.trigger()."""

    def test_press_calls_trigger(self):
        """async_press() must await self._scenario.async_trigger()."""
        calls = []
        sc = _good_scenario()

        async def _trig():
            calls.append(True)

        sc.async_trigger = _trig
        btn = SHCScenarioButton(scenario=sc, entry_unique_id="uid-1", entry_id="entry-1")
        asyncio.run(btn.async_press())
        assert calls == [True]

    def test_press_called_twice_triggers_twice(self):
        calls = []
        sc = _good_scenario()

        async def _trig():
            calls.append(1)

        sc.async_trigger = _trig
        btn = SHCScenarioButton(scenario=sc, entry_unique_id=None, entry_id="entry-1")
        asyncio.run(btn.async_press())
        asyncio.run(btn.async_press())
        assert len(calls) == 2

    def test_press_returns_none(self):
        sc = _good_scenario()

        async def _trig():
            return None

        sc.async_trigger = _trig
        btn = SHCScenarioButton(scenario=sc, entry_unique_id="uid-1", entry_id="entry-1")
        assert asyncio.run(btn.async_press()) is None

    def test_setup_scenario_button_press_via_setup_entry(
        self, mock_config_entry, mock_session
    ):
        """End-to-end: button created via async_setup_entry, then pressed."""
        trigger_calls = []
        sc = _good_scenario(sid="sc-e2e", name="E2E Scene")

        async def _trig():
            trigger_calls.append(True)

        sc.async_trigger = _trig

        mock_session.scenarios = [sc]
        mock_config_entry.options = {OPT_SCENARIOS_AS_BUTTONS: True}
        mock_config_entry.unique_id = "uid-e2e"
        mock_config_entry.runtime_data.shc_device = None
        result = _run_setup(mock_config_entry, mock_session)
        assert len(result) == 1
        asyncio.run(result[0].async_press())
        assert trigger_calls == [True]


class TestSHCScenarioButtonQualityScale:
    """Verify Bronze quality-scale rules for SHCScenarioButton."""

    def test_has_entity_name_true(self):
        """_attr_has_entity_name=True (Bronze: has-entity-name).

        SHCScenarioButton does not inherit a shadowing property from its base,
        so checking the class attribute directly is reliable.
        """
        sc = _good_scenario()
        btn = SHCScenarioButton(scenario=sc, entry_unique_id="u", entry_id="e")
        assert btn._attr_has_entity_name is True

    def test_unique_id_format_unchanged_with_entry_unique_id(self):
        """Regression pin: unique_id = f'{entry_unique_id}_scenario_{scenario.id}'.

        This exact format must never change — changing it orphans existing entities.
        """
        sc = _good_scenario(sid="sc-999")
        btn = SHCScenarioButton(
            scenario=sc, entry_unique_id="uid-fixed", entry_id="entry-fallback"
        )
        assert btn._attr_unique_id == "uid-fixed_scenario_sc-999"

    def test_unique_id_format_unchanged_without_entry_unique_id(self):
        """Regression pin: fallback to entry_id when entry_unique_id is None."""
        sc = _good_scenario(sid="sc-888")
        btn = SHCScenarioButton(
            scenario=sc, entry_unique_id=None, entry_id="entry-id-fallback"
        )
        assert btn._attr_unique_id == "entry-id-fallback_scenario_sc-888"

    def test_device_info_links_to_shc_controller(self):
        """device_info returns a dict with the SHC controller identifiers."""
        shc_dev = _fake_shc_device()
        sc = _good_scenario(sid="sc-di")
        btn = SHCScenarioButton(
            scenario=sc, entry_unique_id="uid-1", entry_id="entry-1", shc_device=shc_dev
        )
        info = btn.device_info
        assert info is not None
        assert info["identifiers"] == shc_dev.identifiers
        assert info["name"] == shc_dev.name
        assert info["manufacturer"] == shc_dev.manufacturer
        assert info["model"] == shc_dev.model

    def test_device_info_none_when_no_shc_device(self):
        """device_info returns None when shc_device is not provided (graceful fallback)."""
        sc = _good_scenario(sid="sc-no-dev")
        btn = SHCScenarioButton(scenario=sc, entry_unique_id="uid-1", entry_id="entry-1")
        assert btn.device_info is None

    def test_setup_entry_passes_shc_device_to_button(
        self, mock_config_entry, mock_session
    ):
        """async_setup_entry populates shc_device so device_info is not None."""
        sc = _good_scenario(sid="sc-wiring", name="Test Wiring")
        mock_session.scenarios = [sc]
        mock_config_entry.options = {OPT_SCENARIOS_AS_BUTTONS: True}
        mock_config_entry.unique_id = "uid-w"
        mock_config_entry.runtime_data.shc_device = _fake_shc_device()
        result = _run_setup(mock_config_entry, mock_session)
        assert len(result) == 1
        btn = result[0]
        assert btn.device_info is not None
        assert btn.device_info["name"] == "Smart Home Controller"


class TestButtonMotionDetectors2DeviceExcluded:
    """button.py — device_excluded continue in the motion_detectors2 loop."""

    def test_excluded_md2_not_added(self, mock_config_entry, mock_session):
        md2 = _fake_device_kw(
            id="md2-excl",
            walk_state=object(),
        )
        mock_session.device_helper.motion_detectors2 = [md2]
        mock_config_entry.options = _excl("md2-excl")
        result = _run_setup(mock_config_entry, mock_session)
        ids = [getattr(e, "_attr_unique_id", "") for e in result]
        assert not any("md2-excl" in uid for uid in ids)


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

    def test_setup_walk_test_creates_start_and_stop_buttons(
        self, mock_config_entry, mock_session
    ):
        dev = self._md2_device()
        mock_session.device_helper.motion_detectors2 = [dev]
        result = _run_setup(mock_config_entry, mock_session)
        walk_types = [type(e) for e in result]
        assert SHCWalkTestButton in walk_types
        assert SHCWalkTestStopButton in walk_types

    def test_setup_walk_test_buttons_count(self, mock_config_entry, mock_session):
        """One MD2 with walk_test → exactly 2 walk-test buttons (+ 1 tamper)."""
        dev = self._md2_device(
            supports_walk=True,
            walk_state="STOPPED",
            supports_detection=False,
            has_tamper=True,
        )
        mock_session.device_helper.motion_detectors2 = [dev]
        result = _run_setup(mock_config_entry, mock_session)
        walk_buttons = [
            e
            for e in result
            if isinstance(e, (SHCWalkTestButton, SHCWalkTestStopButton))
        ]
        assert len(walk_buttons) == 2

    def test_setup_no_walk_test_when_supports_false(
        self, mock_config_entry, mock_session
    ):
        dev = self._md2_device(supports_walk=False)
        mock_session.device_helper.motion_detectors2 = [dev]
        result = _run_setup(mock_config_entry, mock_session)
        assert not any(isinstance(e, SHCWalkTestButton) for e in result)
        assert not any(isinstance(e, SHCWalkTestStopButton) for e in result)

    def test_setup_no_walk_test_when_walk_state_none(
        self, mock_config_entry, mock_session
    ):
        dev = self._md2_device(walk_state=None)
        mock_session.device_helper.motion_detectors2 = [dev]
        result = _run_setup(mock_config_entry, mock_session)
        assert not any(isinstance(e, SHCWalkTestButton) for e in result)

    def test_setup_walk_unique_ids(self, mock_config_entry, mock_session):
        dev = _make_device(root_device_id="root1", device_id="dev1")
        dev.supports_walk_test = True
        dev.walk_state = "STOPPED"
        dev.supports_detection_test = False
        dev.reset_tampered_state = lambda: None
        dev.supports_tamper_reset = True
        mock_session.device_helper.motion_detectors2 = [dev]
        result = _run_setup(mock_config_entry, mock_session)
        uids = [e._attr_unique_id for e in result]
        assert "root1_dev1_walk_test" in uids
        assert "root1_dev1_walk_test_stop" in uids


class TestWalkTestButtonSetup:
    """async_setup_entry wiring for the MD2 walk-test buttons (fixture-driven,
    from test_apk_walktest_and_sensitivity.py)."""

    def test_walk_test_button_created_when_walk_state_present(
        self, mock_config_entry, mock_session
    ):
        from boschshcpy.services_impl import WalkTestService

        md2 = _fake_md2(
            walk_state=WalkTestService.WalkState.UNKNOWN, supports_walk_test=True
        )
        mock_session.device_helper.motion_detectors2 = [md2]
        entities = _run_setup(mock_config_entry, mock_session)
        types = [type(e).__name__ for e in entities]
        assert "SHCWalkTestButton" in types

    def test_walk_test_button_skipped_when_no_walk_state_attr(
        self, mock_config_entry, mock_session
    ):
        md2 = _fake_md2()  # no walk_state attr
        mock_session.device_helper.motion_detectors2 = [md2]
        entities = _run_setup(mock_config_entry, mock_session)
        types = [type(e).__name__ for e in entities]
        assert "SHCWalkTestButton" not in types

    def test_walk_test_button_skipped_when_walk_state_is_none(
        self, mock_config_entry, mock_session
    ):
        # supports_walk_test=True but walk_state=None -> skipped
        md2 = _fake_md2(walk_state=None, supports_walk_test=True)
        mock_session.device_helper.motion_detectors2 = [md2]
        entities = _run_setup(mock_config_entry, mock_session)
        types = [type(e).__name__ for e in entities]
        assert "SHCWalkTestButton" not in types

    def test_walk_test_stop_button_created_alongside_start(
        self, mock_config_entry, mock_session
    ):
        from boschshcpy.services_impl import WalkTestService

        md2 = _fake_md2(
            walk_state=WalkTestService.WalkState.UNKNOWN, supports_walk_test=True
        )
        mock_session.device_helper.motion_detectors2 = [md2]
        entities = _run_setup(mock_config_entry, mock_session)
        types = [type(e).__name__ for e in entities]
        assert "SHCWalkTestStopButton" in types

    def test_walk_test_stop_button_skipped_when_no_walk_state(
        self, mock_config_entry, mock_session
    ):
        md2 = _fake_md2()  # no walk_state attr
        mock_session.device_helper.motion_detectors2 = [md2]
        entities = _run_setup(mock_config_entry, mock_session)
        types = [type(e).__name__ for e in entities]
        assert "SHCWalkTestStopButton" not in types


class TestSHCWalkTestButton:
    """Unit tests for SHCWalkTestButton via `_fake_md2` (AsyncMock-driven,
    from test_apk_walktest_and_sensitivity.py — a narrower complement to
    TestSHCWalkTestButtons above, which uses manual received-list mocks)."""

    def _make(self):
        dev = _fake_md2()
        b = SHCWalkTestButton.__new__(SHCWalkTestButton)
        b._device = dev
        b._attr_unique_id = f"{dev.root_device_id}_{dev.id}_walk_test"
        b._attr_name = "Walk Test"
        return b

    def test_unique_id(self):
        b = self._make()
        assert b._attr_unique_id == "root1_md1_walk_test"

    def test_async_press_calls_async_set_walk_state_request(self):
        from boschshcpy.services_impl import WalkTestService

        dev = _fake_md2(async_set_walk_state_request=AsyncMock())
        b = SHCWalkTestButton.__new__(SHCWalkTestButton)
        b._device = dev
        asyncio.run(b.async_press())
        dev.async_set_walk_state_request.assert_called_once_with(
            WalkTestService.WalkStateRequest.WALK_STATE_START
        )

    def test_async_press_with_real_enum_value(self):
        from boschshcpy.services_impl import WalkTestService

        dev = _fake_md2(async_set_walk_state_request=AsyncMock())
        b = SHCWalkTestButton.__new__(SHCWalkTestButton)
        b._device = dev
        asyncio.run(b.async_press())
        call_arg = dev.async_set_walk_state_request.call_args[0][0]
        assert call_arg == WalkTestService.WalkStateRequest.WALK_STATE_START

    def test_translation_key(self):
        # Icon now comes from icons.json keyed by translation_key, not a
        # hardcoded _attr_icon (icon-translations quality-scale rule).
        b = self._make()
        assert b._attr_translation_key == "walk_test"


class TestSHCWalkTestStopButton:
    """Unit tests for SHCWalkTestStopButton via `_fake_md2` (complement to
    TestSHCWalkTestButtons above)."""

    def _make(self):
        dev = _fake_md2()
        b = SHCWalkTestStopButton.__new__(SHCWalkTestStopButton)
        b._device = dev
        b._attr_unique_id = f"{dev.root_device_id}_{dev.id}_walk_test_stop"
        b._attr_name = "Walk Test Stop"
        return b

    def test_unique_id(self):
        b = self._make()
        assert b._attr_unique_id == "root1_md1_walk_test_stop"

    def test_async_press_calls_async_set_walk_state_request_stop(self):
        from boschshcpy.services_impl import WalkTestService

        dev = _fake_md2(async_set_walk_state_request=AsyncMock())
        b = SHCWalkTestStopButton.__new__(SHCWalkTestStopButton)
        b._device = dev
        asyncio.run(b.async_press())
        dev.async_set_walk_state_request.assert_called_once_with(
            WalkTestService.WalkStateRequest.WALK_STATE_STOP
        )

    def test_async_press_uses_walk_state_stop_not_start(self):
        from boschshcpy.services_impl import WalkTestService

        dev = _fake_md2(async_set_walk_state_request=AsyncMock())
        b = SHCWalkTestStopButton.__new__(SHCWalkTestStopButton)
        b._device = dev
        asyncio.run(b.async_press())
        call_arg = dev.async_set_walk_state_request.call_args[0][0]
        assert call_arg == WalkTestService.WalkStateRequest.WALK_STATE_STOP
        assert call_arg != WalkTestService.WalkStateRequest.WALK_STATE_START

    def test_translation_key(self):
        # Icon now comes from icons.json keyed by translation_key, not a
        # hardcoded _attr_icon (icon-translations quality-scale rule).
        b = self._make()
        assert b._attr_translation_key == "walk_test_stop"


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
            "detection_test"
            if cls is SHCDetectionTestButton
            else "detection_test_stop"
        )
        btn._attr_name = (
            "Detection Test"
            if cls is SHCDetectionTestButton
            else "Detection Test Stop"
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

    def test_setup_detection_test_creates_start_and_stop(
        self, mock_config_entry, mock_session
    ):
        dev = self._md2_device()
        mock_session.device_helper.motion_detectors2 = [dev]
        result = _run_setup(mock_config_entry, mock_session)
        det_types = [type(e) for e in result]
        assert SHCDetectionTestButton in det_types
        assert SHCDetectionTestStopButton in det_types

    def test_setup_no_detection_when_not_supported(
        self, mock_config_entry, mock_session
    ):
        dev = self._md2_device(supports_detection=False)
        mock_session.device_helper.motion_detectors2 = [dev]
        result = _run_setup(mock_config_entry, mock_session)
        assert not any(isinstance(e, SHCDetectionTestButton) for e in result)

    def test_setup_det_unique_ids(self, mock_config_entry, mock_session):
        dev = _make_device(root_device_id="root1", device_id="dev1")
        dev.supports_walk_test = False
        dev.walk_state = None
        dev.supports_detection_test = True
        dev.reset_tampered_state = lambda: None
        dev.supports_tamper_reset = True
        mock_session.device_helper.motion_detectors2 = [dev]
        result = _run_setup(mock_config_entry, mock_session)
        uids = [e._attr_unique_id for e in result]
        assert "root1_dev1_detection_test" in uids
        assert "root1_dev1_detection_test_stop" in uids


class TestButtonSetup:
    """async_setup_entry wiring for MD2 detection-test + tamper-reset buttons
    (from test_md2_detection_tamper_pollcontrol.py)."""

    def test_detection_buttons_created_when_supported(
        self, mock_config_entry, mock_session
    ):
        md2 = _fake_md2(supports_detection_test=True)
        mock_session.device_helper.motion_detectors2 = [md2]
        entities = _run_setup(mock_config_entry, mock_session)
        types = [type(e).__name__ for e in entities]
        assert "SHCDetectionTestButton" in types
        assert "SHCDetectionTestStopButton" in types

    def test_detection_buttons_skipped_when_unsupported(
        self, mock_config_entry, mock_session
    ):
        md2 = _fake_md2(supports_detection_test=False)
        mock_session.device_helper.motion_detectors2 = [md2]
        entities = _run_setup(mock_config_entry, mock_session)
        types = [type(e).__name__ for e in entities]
        assert "SHCDetectionTestButton" not in types

    def test_tamper_reset_created_when_service_supported(
        self, mock_config_entry, mock_session
    ):
        md2 = _fake_md2(
            reset_tampered_state=lambda: None, supports_tamper_reset=True
        )
        mock_session.device_helper.motion_detectors2 = [md2]
        entities = _run_setup(mock_config_entry, mock_session)
        types = [type(e).__name__ for e in entities]
        assert "SHCTamperResetButton" in types

    def test_tamper_reset_skipped_when_service_unsupported(
        self, mock_config_entry, mock_session
    ):
        """reset_tampered_state()/async_reset_tampered_state() are defined
        unconditionally on SHCMotionDetector2, so gating must use the real
        supports_tamper_reset presence check, not hasattr on the method."""
        md2 = _fake_md2(supports_tamper_reset=False)
        mock_session.device_helper.motion_detectors2 = [md2]
        entities = _run_setup(mock_config_entry, mock_session)
        types = [type(e).__name__ for e in entities]
        assert "SHCTamperResetButton" not in types


class TestDetectionTestButtons:
    """Unit tests for SHCDetectionTestButton/StopButton via `_fake_md2`
    (AsyncMock-driven complement to TestSHCDetectionTestButtons above)."""

    def test_start_press(self):
        from boschshcpy.services_impl import DetectionTestService

        dev = _fake_md2(async_set_detection_state_request=AsyncMock())
        b = SHCDetectionTestButton.__new__(SHCDetectionTestButton)
        b._device = dev
        asyncio.run(b.async_press())
        dev.async_set_detection_state_request.assert_called_once_with(
            DetectionTestService.DetectionStateRequest.DETECTION_STATE_START
        )

    def test_stop_press(self):
        from boschshcpy.services_impl import DetectionTestService

        dev = _fake_md2(async_set_detection_state_request=AsyncMock())
        b = SHCDetectionTestStopButton.__new__(SHCDetectionTestStopButton)
        b._device = dev
        asyncio.run(b.async_press())
        dev.async_set_detection_state_request.assert_called_once_with(
            DetectionTestService.DetectionStateRequest.DETECTION_STATE_STOP
        )

    def test_unique_ids(self):
        dev = _fake_md2()
        start = SHCDetectionTestButton.__new__(SHCDetectionTestButton)
        start._attr_unique_id = f"{dev.root_device_id}_{dev.id}_detection_test"
        stop = SHCDetectionTestStopButton.__new__(SHCDetectionTestStopButton)
        stop._attr_unique_id = f"{dev.root_device_id}_{dev.id}_detection_test_stop"
        assert start._attr_unique_id == "root1_md1_detection_test"
        assert stop._attr_unique_id == "root1_md1_detection_test_stop"


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

    def test_setup_tamper_button_created_when_reset_method_present(
        self, mock_config_entry, mock_session
    ):
        dev = self._md2_device_with_tamper(has_tamper=True)
        mock_session.device_helper.motion_detectors2 = [dev]
        result = _run_setup(mock_config_entry, mock_session)
        assert len(result) == 1
        assert isinstance(result[0], SHCTamperResetButton)

    def test_setup_no_tamper_button_without_reset_method(
        self, mock_config_entry, mock_session
    ):
        dev = self._md2_device_with_tamper(has_tamper=False)
        mock_session.device_helper.motion_detectors2 = [dev]
        result = _run_setup(mock_config_entry, mock_session)
        assert not any(isinstance(e, SHCTamperResetButton) for e in result)

    def test_setup_tamper_unique_id(self, mock_config_entry, mock_session):
        dev = _make_device(root_device_id="root1", device_id="dev1")
        dev.supports_walk_test = False
        dev.walk_state = None
        dev.supports_detection_test = False
        dev.reset_tampered_state = lambda: None
        dev.supports_tamper_reset = True
        mock_session.device_helper.motion_detectors2 = [dev]
        result = _run_setup(mock_config_entry, mock_session)
        assert result[0]._attr_unique_id == "root1_dev1_reset_tamper"


class TestTamperResetButton:
    """Unit tests for SHCTamperResetButton via `_fake_md2` (AsyncMock-driven
    complement to TestSHCTamperResetButton above)."""

    def test_press_calls_async_reset(self):
        dev = _fake_md2(async_reset_tampered_state=AsyncMock())
        b = SHCTamperResetButton.__new__(SHCTamperResetButton)
        b._device = dev
        asyncio.run(b.async_press())
        dev.async_reset_tampered_state.assert_called_once_with()

    def test_translation_key(self):
        # Icon now comes from icons.json keyed by translation_key, not a
        # hardcoded _attr_icon (icon-translations quality-scale rule).
        b = SHCTamperResetButton.__new__(SHCTamperResetButton)
        assert b._attr_translation_key == "reset_tamper"


# ---------------------------------------------------------------------------
# DimmerPreviewMaxButton + DimmerPreviewMinButton (dimmer configuration, #123)
# ---------------------------------------------------------------------------


class TestButtonDimmerSetup:
    """async_setup_entry: dimmer preview buttons."""

    def test_dimmer_with_dimmer_configuration_adds_preview_buttons(
        self, mock_config_entry, mock_session
    ):
        """Dimmer with supports_dimmer_configuration=True → both preview buttons."""
        dev = _fake_dev("dim1", supports_dimmer_configuration=True)
        mock_session.device_helper.micromodule_dimmers = [dev]
        result = _run_setup(mock_config_entry, mock_session)
        types = [type(e).__name__ for e in result]
        assert "DimmerPreviewMaxButton" in types
        assert "DimmerPreviewMinButton" in types

    def test_dimmer_without_supports_skips_preview_buttons(
        self, mock_config_entry, mock_session
    ):
        """supports_dimmer_configuration=False → buttons not added."""
        dev = _fake_dev("dim1")  # no supports_dimmer_configuration
        mock_session.device_helper.micromodule_dimmers = [dev]
        result = _run_setup(mock_config_entry, mock_session)
        types = [type(e).__name__ for e in result]
        assert "DimmerPreviewMaxButton" not in types


class TestDimmerPreviewButtonInits:
    """DimmerPreviewMaxButton/DimmerPreviewMinButton __init__ + async_press."""

    def test_dimmer_preview_max_button_init(self):
        """DimmerPreviewMaxButton.__init__ sets unique_id."""
        dev = _fake_dev("dim1")
        btn = DimmerPreviewMaxButton(dev, "entry1")
        assert btn._attr_unique_id == "root1_dim1_dimmer_preview_max"

    def test_dimmer_preview_min_button_init(self):
        """DimmerPreviewMinButton.__init__ sets unique_id."""
        dev = _fake_dev("dim1")
        btn = DimmerPreviewMinButton(dev, "entry1")
        assert btn._attr_unique_id == "root1_dim1_dimmer_preview_min"

    def test_dimmer_preview_max_press_with_service(self):
        """DimmerPreviewMaxButton.async_press calls async_preview_max_brightness."""
        svc = MagicMock()
        svc.async_preview_max_brightness = AsyncMock()
        dev = _fake_dev("dim1", dimmer_configuration=svc)
        btn = DimmerPreviewMaxButton.__new__(DimmerPreviewMaxButton)
        btn._device = dev
        _run(btn.async_press())
        svc.async_preview_max_brightness.assert_called_once()

    def test_dimmer_preview_min_press_with_service(self):
        """DimmerPreviewMinButton.async_press calls async_preview_min_brightness."""
        svc = MagicMock()
        svc.async_preview_min_brightness = AsyncMock()
        dev = _fake_dev("dim1", dimmer_configuration=svc)
        btn = DimmerPreviewMinButton.__new__(DimmerPreviewMinButton)
        btn._device = dev
        _run(btn.async_press())
        svc.async_preview_min_brightness.assert_called_once()


class TestButtonDimmerExcluded:
    """button.py: excluded dimmer device → continue."""

    def test_excluded_dimmer_skipped_in_button_setup(
        self, mock_config_entry, mock_session
    ):
        """device_excluded → continue before dimmer_configuration check."""
        dev = _fake_dev("dim_excl", supports_dimmer_configuration=True)
        mock_session.device_helper.micromodule_dimmers = [dev]
        mock_config_entry.options = {OPT_EXCLUDED_DEVICES: ["dim_excl"]}
        result = _run_setup(mock_config_entry, mock_session)
        assert not any(isinstance(e, DimmerPreviewMaxButton) for e in result)


def test_dimmer_preview_max_calls_service():
    btn = _new(DimmerPreviewMaxButton)
    svc = SimpleNamespace(async_preview_max_brightness=AsyncMock())
    btn._device = SimpleNamespace(dimmer_configuration=svc)
    asyncio.run(btn.async_press())
    svc.async_preview_max_brightness.assert_awaited_once()


def test_dimmer_preview_min_calls_service():
    btn = _new(DimmerPreviewMinButton)
    svc = SimpleNamespace(async_preview_min_brightness=AsyncMock())
    btn._device = SimpleNamespace(dimmer_configuration=svc)
    asyncio.run(btn.async_press())
    svc.async_preview_min_brightness.assert_awaited_once()


class TestSHCEnableAllDiagnosticsButtonSetup:
    """The hub-level diagnostics-enable button is created unconditionally,
    unlike every other button here (all gated by device buckets/options)."""

    def test_button_always_created_even_with_no_other_entities(
        self, mock_config_entry, mock_session
    ) -> None:
        mock_config_entry.unique_id = None
        mock_config_entry.runtime_data.shc_device = None
        collected = asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )

        assert any(
            isinstance(e, SHCEnableAllDiagnosticsButton) for e in collected
        )

    def test_unique_id_scoped_to_config_entry(self) -> None:
        button = SHCEnableAllDiagnosticsButton(
            entry_unique_id=None, entry_id="entry123"
        )
        assert button.unique_id == "entry123_enable_all_diagnostics"

    def test_entity_category_is_config_not_diagnostic(self) -> None:
        """Must stay visible/enabled by default — it's the button that
        enables the (hidden-by-default) diagnostic entities, so it can't be
        one itself."""
        button = SHCEnableAllDiagnosticsButton(
            entry_unique_id=None, entry_id="entry123"
        )
        assert button.entity_category == EntityCategory.CONFIG


def test_dimmer_preview_buttons_safe_without_service():
    max_btn = _new(DimmerPreviewMaxButton)
    max_btn._device = SimpleNamespace(dimmer_configuration=None)
    asyncio.run(max_btn.async_press())  # no error

    min_btn = _new(DimmerPreviewMinButton)
    min_btn._device = SimpleNamespace(dimmer_configuration=None)
    asyncio.run(min_btn.async_press())  # no error


# ---------------------------------------------------------------------------
# Mixed async_setup_entry — all device types together
# ---------------------------------------------------------------------------


def test_setup_mixed_all_entity_types(mock_config_entry, mock_session):
    """All device buckets populated → one entity per type (plus tamper)."""
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

    mock_session.device_helper.micromodule_impulse_relays = [relay]
    mock_session.device_helper.smoke_detectors = [smoke]
    mock_session.device_helper.motion_detectors2 = [md2]
    mock_session.device_helper.outdoor_sirens = [siren]
    mock_session.scenarios = [sc]

    mock_config_entry.options = {OPT_SCENARIOS_AS_BUTTONS: True}
    mock_config_entry.unique_id = "uid"
    mock_config_entry.runtime_data.shc_device = shc_dev

    result = _run_setup(mock_config_entry, mock_session)

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


def test_setup_empty_all_buckets_yields_nothing(mock_config_entry, mock_session):
    """All device buckets empty → nothing added."""
    result = _run_setup(mock_config_entry, mock_session)
    assert result == []
