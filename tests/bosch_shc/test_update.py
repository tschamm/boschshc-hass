"""Unit tests for update.py — ControllerUpdate and DeviceUpdate entities plus
async_setup_entry wiring (hass#186 controller update, coverage-gap tests for
lines 41-60, 73-76, 111-113). Pure-unit style: entities built via __new__ or
directly, with SimpleNamespace/MagicMock stand-ins, no HA harness.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from boschshcpy.exceptions import SHCException
from homeassistant.exceptions import HomeAssistantError

from homeassistant.components.update import UpdateEntityFeature

from custom_components.bosch_shc.const import OPT_EXCLUDED_DEVICES
from custom_components.bosch_shc.update import (
    FIRMWARE_CAPABLE_MODELS,
    ControllerUpdate,
    DeviceUpdate,
    async_setup_entry,
)

from .conftest import run_setup_entry


def _new(cls):
    return cls.__new__(cls)


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


# --------------------------- #186 controller update -------------------------


def test_controller_update_latest_version_when_available():
    info = SimpleNamespace(
        version="10.20.1", available_version="10.25.9", update_state="UPDATE_AVAILABLE"
    )
    u = _new(ControllerUpdate)
    u._information = info
    assert u.installed_version == "10.20.1"
    assert u.latest_version == "10.25.9"
    assert u.in_progress is False


def test_controller_update_latest_equals_installed_when_no_update():
    info = SimpleNamespace(
        version="10.20.1", available_version=None, update_state="NO_UPDATE_AVAILABLE"
    )
    u = _new(ControllerUpdate)
    u._information = info
    assert u.latest_version == "10.20.1"


def test_controller_update_in_progress():
    info = SimpleNamespace(
        version="10.20.1", available_version="10.25.9", update_state="DOWNLOADING"
    )
    u = _new(ControllerUpdate)
    u._information = info
    assert u.in_progress is True


class TestControllerUpdateInit:
    """Cover ControllerUpdate.__init__ (lines 73-76)."""

    def test_init_sets_attributes(self):
        info = SimpleNamespace(unique_id="aa:bb:cc:dd:ee:ff", version="9.0.0")
        cu = ControllerUpdate(info, "My SHC Title", "entry1")
        assert cu._information is info
        assert cu._entry_id == "entry1"
        assert "aa:bb:cc:dd:ee:ff" in cu._attr_unique_id
        assert cu._attr_device_info is not None


class TestControllerUpdateAsyncUpdate:
    """Cover ControllerUpdate.async_update (lines 111-113)."""

    def test_async_update_calls_refresh_when_present(self):
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
        info = SimpleNamespace(unique_id="aa:bb:cc:dd:ee:ff", version="9.0.0")
        cu = ControllerUpdate(info, "My SHC", "e1")
        # information without async_refresh — must not raise
        _run(cu.async_update())


class TestControllerUpdateAsyncInstall:
    """Cover ControllerUpdate.async_install (the new APK-traced trigger)."""

    def test_async_install_calls_start_software_update(self):
        called = []

        async def fake_start():
            called.append(True)

        cu = ControllerUpdate(
            SimpleNamespace(unique_id="aa:bb:cc:dd:ee:ff", version="9.0.0"),
            "My SHC",
            "e1",
        )
        cu._information = SimpleNamespace(
            async_start_software_update=fake_start,
        )
        _run(cu.async_install(version=None, backup=False))
        assert called

    def test_async_install_wraps_shc_exception(self):
        async def fake_start():
            raise SHCException("boom")

        cu = ControllerUpdate(
            SimpleNamespace(unique_id="aa:bb:cc:dd:ee:ff", version="9.0.0"),
            "My SHC",
            "e1",
        )
        cu._information = SimpleNamespace(async_start_software_update=fake_start)
        with pytest.raises(HomeAssistantError):
            _run(cu.async_install(version=None, backup=False))


# ------------------- per-device firmware-state-probe entity ------------------


def test_device_update_installed_version_is_fixed_marker():
    u = _new(DeviceUpdate)
    u._firmware_state = "UpToDate"
    assert u.installed_version == "up_to_date"


def test_device_update_up_to_date_states_report_no_update():
    for state in (None, "UpToDate", "UpToDateAwaitingUserInteraction", "Fetching"):
        u = _new(DeviceUpdate)
        u._firmware_state = state
        assert u.latest_version == u.installed_version, state
        assert u.in_progress is False


def test_device_update_pending_states_report_update_available():
    for state in ("UpdateAvailable", "AwaitingActivation", "AwaitingActivationTimeout", "UpdatePending", "AwaitingUserInteraction", "Failed"):
        u = _new(DeviceUpdate)
        u._firmware_state = state
        assert u.latest_version != u.installed_version, state


def test_device_update_unknown_state_is_mid_transfer_not_up_to_date():
    """hass#373 follow-up: a live-confirmed transfer goes through "Unknown"
    mid-transfer (rawscan-database.md) -- must show as in-progress/pending,
    not silently as up to date (was hiding a genuinely still-running update)."""
    u = _new(DeviceUpdate)
    u._firmware_state = "Unknown"
    assert u.latest_version != u.installed_version
    assert u.in_progress is True


def test_device_update_in_progress_states():
    for state in ("UpdateRunning", "TransferringUpdate", "Unknown"):
        u = _new(DeviceUpdate)
        u._firmware_state = state
        assert u.in_progress is True
        # still reported as pending, not up to date
        assert u.latest_version != u.installed_version


def test_device_update_supports_progress_feature():
    """`in_progress` has no effect in the UI without PROGRESS declared
    (homeassistant/components/update/__init__.py) -- must actually be set."""
    u = _new(DeviceUpdate)
    assert UpdateEntityFeature.PROGRESS in u.supported_features


def test_controller_update_supports_progress_feature():
    cu = _new(ControllerUpdate)
    assert UpdateEntityFeature.PROGRESS in cu.supported_features


def test_device_update_release_summary_surfaces_raw_state():
    u = _new(DeviceUpdate)
    u._firmware_state = "AwaitingActivation"
    assert u.release_summary == "AwaitingActivation"


def test_device_update_unrecognized_state_treated_as_pending():
    """A future/unknown state string must not silently hide as up-to-date."""
    u = _new(DeviceUpdate)
    u._firmware_state = "SomeBrandNewState"
    assert u.latest_version != u.installed_version


class TestDeviceUpdateAsyncUpdate:
    def test_async_update_stores_probed_state(self):
        u = _new(DeviceUpdate)

        async def fake_probe():
            return "AwaitingActivation"

        u._device = SimpleNamespace(
            name="FakeDev", async_firmware_update_state=fake_probe
        )
        _run(u.async_update())
        assert u._firmware_state == "AwaitingActivation"

    def test_async_update_logs_and_keeps_last_state_on_error(self):
        u = _new(DeviceUpdate)
        u._firmware_state = "UpToDate"

        async def fake_probe():
            raise SHCException("boom")

        u._device = SimpleNamespace(
            name="FakeDev", async_firmware_update_state=fake_probe
        )
        _run(u.async_update())
        assert u._firmware_state == "UpToDate"

class TestDeviceUpdateAsyncInstall:
    """Cover DeviceUpdate.async_install (the new APK-traced trigger)."""

    def test_async_install_calls_activate_firmware_update(self):
        called = []

        async def fake_activate():
            called.append(True)

        u = _new(DeviceUpdate)
        u._firmware_state = "AwaitingActivation"
        u._device = SimpleNamespace(
            name="FakeDev", async_activate_firmware_update=fake_activate
        )
        _run(u.async_install(version=None, backup=False))
        assert called

    def test_async_install_wraps_shc_exception(self):
        async def fake_activate():
            raise SHCException("boom")

        u = _new(DeviceUpdate)
        u._firmware_state = "AwaitingActivation"
        u._device = SimpleNamespace(
            name="FakeDev", async_activate_firmware_update=fake_activate
        )
        with pytest.raises(HomeAssistantError):
            _run(u.async_install(version=None, backup=False))

    def test_async_install_refuses_when_not_awaiting_activation(self):
        """hass#373: any state other than AwaitingActivation must be refused
        locally (informative error) instead of hitting the SHC and getting a
        confusing raw 409."""
        for state in (
            None,
            "UpToDate",
            "UpdateAvailable",
            "TransferringUpdate",
            "UpdatePending",
            "UpdateRunning",
            "AwaitingUserInteraction",
            "AwaitingActivationTimeout",
            "Failed",
            "Unknown",
            "Fetching",
            "UpToDateAwaitingUserInteraction",
        ):
            called = []

            async def fake_activate():
                called.append(True)

            u = _new(DeviceUpdate)
            u._firmware_state = state
            u._device = SimpleNamespace(
                name="FakeDev", async_activate_firmware_update=fake_activate
            )
            with pytest.raises(HomeAssistantError):
                _run(u.async_install(version=None, backup=False))
            assert not called, state


# ------------------------- async_setup_entry wiring --------------------------


class TestUpdateAsyncSetupEntry:
    """Cover update.py async_setup_entry body (lines 41-60)."""

    def _run(self, mock_config_entry, mock_session) -> list:
        # update.py's async_setup_entry reads config_entry.title (unlike the
        # device_helper-bucket platforms the shared fixture was built for);
        # the shared mock_config_entry fixture doesn't set it, so wire it here.
        mock_config_entry.title = "Test SHC"
        return asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )

    def test_setup_entry_with_information_and_no_devices(
        self, mock_config_entry, mock_session
    ):
        """Lines 44-48, 54-60: controller entity created, empty device list."""
        mock_session.devices = []
        result = self._run(mock_config_entry, mock_session)
        assert any(e._information for e in result)

    def test_setup_entry_polls_before_add(self, mock_config_entry, mock_session):
        """hass#373 follow-up: without update_before_add=True, HA schedules
        the first poll a full SCAN_INTERVAL (6h) from now, leaving these
        entities on a stale/unset firmware_state after every restart."""
        mock_config_entry.title = "Test SHC"
        mock_config_entry.runtime_data.session = mock_session
        mock_session.devices = []
        calls = []

        def add(entities, update_before_add=False):
            calls.append(update_before_add)

        asyncio.run(async_setup_entry(SimpleNamespace(), mock_config_entry, add))
        assert calls == [True]

    def test_setup_entry_device_with_firmware_capable_model(
        self, mock_config_entry, mock_session
    ):
        """A device whose model is in FIRMWARE_CAPABLE_MODELS gets a DeviceUpdate."""
        dev = _fake_dev(device_model="TRV_GEN2")
        mock_session.devices = [dev]
        result = self._run(mock_config_entry, mock_session)
        assert any(isinstance(e, DeviceUpdate) for e in result)

    def test_setup_entry_device_with_unsupported_model_skipped(
        self, mock_config_entry, mock_session
    ):
        """A device whose model isn't firmware-capable is skipped."""
        dev = _fake_dev()  # device_model="TestModel" (default, not in the set)
        mock_session.devices = [dev]
        result = self._run(mock_config_entry, mock_session)
        assert not any(isinstance(e, DeviceUpdate) for e in result)

    @pytest.mark.parametrize("model", sorted(FIRMWARE_CAPABLE_MODELS))
    def test_setup_entry_every_firmware_capable_model_gets_an_entity(
        self, mock_config_entry, mock_session, model
    ):
        dev = _fake_dev(device_model=model)
        mock_session.devices = [dev]
        result = self._run(mock_config_entry, mock_session)
        assert any(isinstance(e, DeviceUpdate) for e in result), model


class TestUpdateExcludedDevice:
    """update.py: device_excluded in setup loop → continue."""

    @pytest.mark.parametrize(
        "mock_config_entry",
        [{"options": {OPT_EXCLUDED_DEVICES: ["excl1"]}}],
        indirect=True,
    )
    def test_excluded_device_skipped_in_update_setup(
        self, mock_config_entry, mock_session
    ):
        """Device in OPT_EXCLUDED_DEVICES → continue (no DeviceUpdate added)."""
        dev = _fake_dev("excl1", device_model="TRV_GEN2")
        mock_session.devices = [dev]
        mock_config_entry.title = "Test SHC"
        result = asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )
        assert not any(isinstance(e, DeviceUpdate) for e in result)
