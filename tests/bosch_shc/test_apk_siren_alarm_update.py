"""Unit tests for the APK-driven HA entities (#120 siren, #174 SD II alarm,
#186 controller update). Pure-unit style: build entities via __new__ and inject
a SimpleNamespace device, exercising the read/derive logic without HA harness.
"""

from types import SimpleNamespace

from custom_components.bosch_shc.binary_sensor import (
    SirenAcousticAlarmSensor,
    SirenTamperSensor,
    SirenVisualAlarmSensor,
)
from custom_components.bosch_shc.number import _SIREN_ALARM_DELAY, SirenConfigNumber
from custom_components.bosch_shc.select import SirenSoundLevelSelect
from custom_components.bosch_shc.sensor import (
    KeypadTriggerSensor,
    SirenBatterySensor,
    SirenMainPowerSensor,
    SirenSolarChargingSensor,
)
from custom_components.bosch_shc.update import ControllerUpdate, DeviceUpdate


def _new(cls):
    return cls.__new__(cls)


# --------------------------- #120 binary sensors ---------------------------

def test_siren_binary_sensors_read_flags():
    siren = SimpleNamespace(
        acoustic_alarm_on=True, visual_alarm_on=False, tamper_activated=True
    )
    a = _new(SirenAcousticAlarmSensor)
    a._device = SimpleNamespace(siren=siren)
    assert a.is_on is True

    v = _new(SirenVisualAlarmSensor)
    v._device = SimpleNamespace(siren=siren)
    assert v.is_on is False

    t = _new(SirenTamperSensor)
    t._device = SimpleNamespace(siren=siren)
    assert t.is_on is True


# --------------------------- #120 power sensors ----------------------------

def test_siren_battery_sensor():
    s = _new(SirenBatterySensor)
    s._device = SimpleNamespace(
        power_supply=SimpleNamespace(battery_percentage_remaining=73)
    )
    assert s.native_value == 73


def test_siren_main_power_and_solar_enum_lowercased():
    mp = _new(SirenMainPowerSensor)
    mp._device = SimpleNamespace(
        power_supply=SimpleNamespace(main_power_supply=SimpleNamespace(name="SOLAR"))
    )
    assert mp.native_value == "solar"
    assert mp.native_value in mp._attr_options

    sc = _new(SirenSolarChargingSensor)
    sc._device = SimpleNamespace(
        power_supply=SimpleNamespace(solar_charging_score=SimpleNamespace(name="GOOD"))
    )
    assert sc.native_value == "good"


# --------------------------- #120 number (config) --------------------------

def test_siren_config_number_reads_and_clamps():
    n = SirenConfigNumber(
        SimpleNamespace(root_device_id="r", id="d", name="Siren"),
        "entry",
        *_SIREN_ALARM_DELAY,
    )
    n._device = SimpleNamespace(siren=SimpleNamespace(alarm_delay=42))
    assert n.native_value == 42.0
    assert n._attr_native_min_value == 0.0
    assert n._attr_native_max_value == 180.0


# --------------------------- #120 select -----------------------------------

def test_siren_sound_level_current_option():
    sel = _new(SirenSoundLevelSelect)
    sel._device = SimpleNamespace(
        siren=SimpleNamespace(sound_level=SimpleNamespace(name="HIGH"))
    )
    assert sel.current_option == "high"
    assert sel.current_option in sel._attr_options


# --------------------------- #186 controller update ------------------------

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


# --------------------- per-device SoftwareUpdate entity --------------------

def _sw_service(**kw):
    """A stand-in SoftwareUpdate service carrying the real SwUpdateState enum."""
    from boschshcpy.services_impl import SoftwareUpdateService

    return SimpleNamespace(SwUpdateState=SoftwareUpdateService.SwUpdateState, **kw)


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


# ----------------------- KeypadTrigger diag sensor -------------------------

def test_keypad_trigger_sensor():
    svc = SimpleNamespace(
        switch_type="UNIVERSAL_SWITCH",
        scenario_id_associations=[{"keyName": "LOWER_BUTTON", "scenarioId": "x"}],
        ids_to_trigger=["x"],
    )
    s = _new(KeypadTriggerSensor)
    s._device = SimpleNamespace(keypadtrigger=svc)
    assert s.native_value == "UNIVERSAL_SWITCH"
    assert s.extra_state_attributes["ids_to_trigger"] == ["x"]


def test_keypad_trigger_sensor_no_service_is_safe():
    s = _new(KeypadTriggerSensor)
    s._device = SimpleNamespace(keypadtrigger=None)
    assert s.native_value is None
    assert s.extra_state_attributes is None


# --- #342: translated names actually resolve (SHCEntity._attr_name shadow fix) ---

_FAKE_DEVICE = SimpleNamespace(
    root_device_id="root-1",
    id="hdm:ZigBee:dev1",
    name="Schlafzimmerfenster",
    status="AVAILABLE",
)


def test_bypass_switch_uses_translation_key_not_device_name():
    """#342: bypass must drop _attr_name=None so HA uses the 'bypass' name key.

    If _attr_name stayed None, HA's _name_internal returns it (device name) before
    consulting translation_key — defeating the whole fix.
    """
    from custom_components.bosch_shc.switch import SWITCH_TYPES, SHCSwitch

    sw = SHCSwitch(
        device=_FAKE_DEVICE, entry_id="e1", description=SWITCH_TYPES["bypass"]
    )
    assert not hasattr(sw, "_attr_name")
    assert sw.translation_key == "bypass"
    # unique_id stays the primary id (no orphaning / migration needed)
    assert sw.unique_id == "root-1_hdm:ZigBee:dev1"


def test_device_update_and_keypad_sensor_drop_attr_name():
    from custom_components.bosch_shc.sensor import KeypadTriggerSensor
    from custom_components.bosch_shc.update import DeviceUpdate

    u = DeviceUpdate(device=_FAKE_DEVICE, entry_id="e1")
    assert not hasattr(u, "_attr_name")
    assert u.translation_key == "device_firmware"

    s = KeypadTriggerSensor(device=_FAKE_DEVICE, entry_id="e1")
    assert not hasattr(s, "_attr_name")
    assert s.translation_key == "keypad_trigger"
