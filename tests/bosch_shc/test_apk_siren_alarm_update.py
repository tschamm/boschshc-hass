"""Unit tests for the APK-driven HA entities (#120 siren, #174 SD II alarm,
#186 controller update). Pure-unit style: build entities via __new__ and inject
a SimpleNamespace device, exercising the read/derive logic without HA harness.
"""

from types import SimpleNamespace

from custom_components.bosch_shc.binary_sensor import (
    SirenAcousticAlarmSensor,
    SirenVisualAlarmSensor,
    SirenTamperSensor,
)
from custom_components.bosch_shc.sensor import (
    SirenBatterySensor,
    SirenMainPowerSensor,
    SirenSolarChargingSensor,
)
from custom_components.bosch_shc.number import SirenConfigNumber, _SIREN_ALARM_DELAY
from custom_components.bosch_shc.select import SirenSoundLevelSelect
from custom_components.bosch_shc.update import ControllerUpdate


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
