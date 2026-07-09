"""Unit tests for update.py — ControllerUpdate and DeviceUpdate entities plus
async_setup_entry wiring (hass#186 controller update, coverage-gap tests for
lines 41-60, 73-76, 111-113). Pure-unit style: entities built via __new__ or
directly, with SimpleNamespace/MagicMock stand-ins, no HA harness.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

from custom_components.bosch_shc.const import OPT_EXCLUDED_DEVICES
from custom_components.bosch_shc.update import ControllerUpdate, DeviceUpdate


def _new(cls):
    return cls.__new__(cls)


def _run(coro):
    return asyncio.run(coro)


def _sw_service(**kw):
    """A stand-in SoftwareUpdate service carrying the real SwUpdateState enum."""
    from boschshcpy.services_impl import SoftwareUpdateService

    return SimpleNamespace(SwUpdateState=SoftwareUpdateService.SwUpdateState, **kw)


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


# --------------------- per-device SoftwareUpdate entity ---------------------


def test_device_update_available():
    from boschshcpy.services_impl import SoftwareUpdateService

    svc = _sw_service(
        sw_installed_version="1.0.0",
        sw_update_available_version="1.1.0",
        sw_update_state=SoftwareUpdateService.SwUpdateState.UPDATE_AVAILABLE,
    )
    u = _new(DeviceUpdate)
    u._device = SimpleNamespace(software_update=svc)
    assert u.installed_version == "1.0.0"
    assert u.latest_version == "1.1.0"
    assert u.in_progress is False


def test_device_update_latest_equals_installed_when_no_update():
    from boschshcpy.services_impl import SoftwareUpdateService

    svc = _sw_service(
        sw_installed_version="1.0.0",
        sw_update_available_version="1.0.0",
        sw_update_state=SoftwareUpdateService.SwUpdateState.NO_UPDATE_AVAILABLE,
    )
    u = _new(DeviceUpdate)
    u._device = SimpleNamespace(software_update=svc)
    assert u.latest_version == "1.0.0"


def test_device_update_latest_version_kept_after_failed_install():
    """Regression: a failed install doesn't apply the pending version, so
    latest_version must keep showing it instead of falling back to
    sw_installed_version (which would misreport "up to date" right when the
    update is still outstanding)."""
    from boschshcpy.services_impl import SoftwareUpdateService

    svc = _sw_service(
        sw_installed_version="1.0.0",
        sw_update_available_version="1.1.0",
        sw_update_state=SoftwareUpdateService.SwUpdateState.UPDATE_FAILED,
    )
    u = _new(DeviceUpdate)
    u._device = SimpleNamespace(software_update=svc)
    assert u.latest_version == "1.1.0"


def test_device_update_in_progress():
    from boschshcpy.services_impl import SoftwareUpdateService

    svc = _sw_service(
        sw_installed_version="1.0.0",
        sw_update_available_version="1.1.0",
        sw_update_state=SoftwareUpdateService.SwUpdateState.INSTALLING,
    )
    u = _new(DeviceUpdate)
    u._device = SimpleNamespace(software_update=svc)
    assert u.in_progress is True


def test_device_update_no_service_is_safe():
    u = _new(DeviceUpdate)
    u._device = SimpleNamespace(software_update=None)
    assert u.installed_version is None
    assert u.latest_version is None
    assert u.in_progress is False


# ------------------------- async_setup_entry wiring --------------------------


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
        from custom_components.bosch_shc.update import async_setup_entry

        dev = _fake_dev(supports_software_update=True)
        session = self._make_session(devices=[dev])
        hass = _fake_hass(session=session)
        entry = _fake_entry(hass=hass)

        collected = []
        _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))
        assert any(isinstance(e, DeviceUpdate) for e in collected)

    def test_setup_entry_device_without_software_update(self):
        """Line 57: device without supports_software_update is skipped."""
        from custom_components.bosch_shc.update import async_setup_entry

        dev = _fake_dev()  # no supports_software_update
        session = self._make_session(devices=[dev])
        hass = _fake_hass(session=session)
        entry = _fake_entry(hass=hass)

        collected = []
        _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))
        assert not any(isinstance(e, DeviceUpdate) for e in collected)


class TestUpdateExcludedDevice:
    """update.py line 56: device_excluded in setup loop → continue."""

    def test_excluded_device_skipped_in_update_setup(self):
        """Line 56: device in OPT_EXCLUDED_DEVICES → continue (no DeviceUpdate added)."""
        from custom_components.bosch_shc.update import async_setup_entry

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

