"""Unit tests for sensor.py entity classes.

Pattern: bypass __init__ via Cls.__new__(Cls), inject fake device via SimpleNamespace.
No HA harness, no tests.common, no async_setup_entry.
"""

from types import SimpleNamespace

from boschshcpy.models_impl import SHCSmartPlugCompact
from boschshcpy.services_impl import AirQualityLevelService, ValveTappetService
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import (
    CONCENTRATION_PARTS_PER_MILLION,
    LIGHT_LUX,
    PERCENTAGE,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.helpers.entity import EntityCategory

from custom_components.bosch_shc.sensor import (
    AirQualitySensor,
    CommunicationQualitySensor,
    EnergySensor,
    EnergyYieldSensor,
    HumidityRatingSensor,
    HumiditySensor,
    IlluminanceLevelSensor,
    PowerSensor,
    PowerYieldSensor,
    PurityRatingSensor,
    PuritySensor,
    TemperatureRatingSensor,
    TemperatureSensor,
    ValveTappetSensor,
)


# ---------------------------------------------------------------------------
# TemperatureSensor
# ---------------------------------------------------------------------------


def _temp_sensor(temperature):
    s = TemperatureSensor.__new__(TemperatureSensor)
    s._device = SimpleNamespace(temperature=temperature)
    return s


class TestTemperatureSensor:
    def test_native_value(self):
        assert _temp_sensor(21.5).native_value == 21.5

    def test_native_value_zero(self):
        assert _temp_sensor(0.0).native_value == 0.0

    def test_native_value_negative(self):
        assert _temp_sensor(-5.0).native_value == -5.0

    def test_device_class(self):
        assert _temp_sensor(22.0).device_class == SensorDeviceClass.TEMPERATURE

    def test_unit(self):
        assert _temp_sensor(22.0).native_unit_of_measurement == UnitOfTemperature.CELSIUS

    def test_state_class(self):
        assert _temp_sensor(22.0).state_class == SensorStateClass.MEASUREMENT


# ---------------------------------------------------------------------------
# HumiditySensor
# ---------------------------------------------------------------------------


def _humidity_sensor(humidity):
    s = HumiditySensor.__new__(HumiditySensor)
    s._device = SimpleNamespace(humidity=humidity)
    return s


class TestHumiditySensor:
    def test_native_value(self):
        assert _humidity_sensor(55.0).native_value == 55.0

    def test_native_value_zero(self):
        assert _humidity_sensor(0).native_value == 0

    def test_device_class(self):
        assert _humidity_sensor(55.0).device_class == SensorDeviceClass.HUMIDITY

    def test_unit(self):
        assert _humidity_sensor(55.0).native_unit_of_measurement == PERCENTAGE

    def test_state_class(self):
        assert _humidity_sensor(55.0).state_class == SensorStateClass.MEASUREMENT


# ---------------------------------------------------------------------------
# PuritySensor
# ---------------------------------------------------------------------------


def _purity_sensor(purity):
    s = PuritySensor.__new__(PuritySensor)
    s._device = SimpleNamespace(purity=purity)
    return s


class TestPuritySensor:
    def test_native_value(self):
        assert _purity_sensor(800).native_value == 800

    def test_native_value_high(self):
        assert _purity_sensor(2000).native_value == 2000

    def test_unit_is_ppm(self):
        assert _purity_sensor(800).native_unit_of_measurement == CONCENTRATION_PARTS_PER_MILLION

    def test_state_class(self):
        assert _purity_sensor(800).state_class == SensorStateClass.MEASUREMENT

    def test_device_class(self):
        # Bosch "purity" is air-purity/VOC ppm, not CO2 — no device_class (#204),
        # matching HA Core's own bosch_shc integration.
        assert _purity_sensor(400).device_class is None

    def test_unit(self):
        assert _purity_sensor(400).native_unit_of_measurement == CONCENTRATION_PARTS_PER_MILLION


# ---------------------------------------------------------------------------
# AirQualitySensor
# ---------------------------------------------------------------------------


def _air_quality_sensor(combined_rating, description="Good air"):
    s = AirQualitySensor.__new__(AirQualitySensor)
    s._device = SimpleNamespace(combined_rating=combined_rating, description=description)
    return s


class TestAirQualitySensor:
    def test_native_value_good(self):
        rating = AirQualityLevelService.RatingState.GOOD
        s = _air_quality_sensor(rating)
        assert s.native_value == "GOOD"

    def test_native_value_medium(self):
        rating = AirQualityLevelService.RatingState.MEDIUM
        s = _air_quality_sensor(rating)
        assert s.native_value == "MEDIUM"

    def test_native_value_bad(self):
        rating = AirQualityLevelService.RatingState.BAD
        s = _air_quality_sensor(rating)
        assert s.native_value == "BAD"

    def test_extra_state_attributes(self):
        rating = AirQualityLevelService.RatingState.GOOD
        s = _air_quality_sensor(rating, description="Fresh")
        assert s.extra_state_attributes == {"rating_description": "Fresh"}

    def test_native_value_unknown_rating_returns_none(self):
        s = AirQualitySensor.__new__(AirQualitySensor)

        class _BadEnum:
            @property
            def name(self):
                raise ValueError("unknown_rating")

        s._device = SimpleNamespace(combined_rating=_BadEnum(), name="test")
        assert s.native_value is None


# ---------------------------------------------------------------------------
# TemperatureRatingSensor
# ---------------------------------------------------------------------------


def _temp_rating_sensor(rating):
    s = TemperatureRatingSensor.__new__(TemperatureRatingSensor)
    s._device = SimpleNamespace(temperature_rating=rating)
    return s


class TestTemperatureRatingSensor:
    def test_native_value_good(self):
        s = _temp_rating_sensor(AirQualityLevelService.RatingState.GOOD)
        assert s.native_value == "GOOD"

    def test_native_value_medium(self):
        s = _temp_rating_sensor(AirQualityLevelService.RatingState.MEDIUM)
        assert s.native_value == "MEDIUM"

    def test_native_value_bad(self):
        s = _temp_rating_sensor(AirQualityLevelService.RatingState.BAD)
        assert s.native_value == "BAD"


# ---------------------------------------------------------------------------
# HumidityRatingSensor
# ---------------------------------------------------------------------------


def _humidity_rating_sensor(rating):
    s = HumidityRatingSensor.__new__(HumidityRatingSensor)
    s._device = SimpleNamespace(humidity_rating=rating)
    return s


class TestHumidityRatingSensor:
    def test_native_value_good(self):
        s = _humidity_rating_sensor(AirQualityLevelService.RatingState.GOOD)
        assert s.native_value == "GOOD"

    def test_native_value_medium(self):
        s = _humidity_rating_sensor(AirQualityLevelService.RatingState.MEDIUM)
        assert s.native_value == "MEDIUM"

    def test_native_value_bad(self):
        s = _humidity_rating_sensor(AirQualityLevelService.RatingState.BAD)
        assert s.native_value == "BAD"


# ---------------------------------------------------------------------------
# PurityRatingSensor
# ---------------------------------------------------------------------------


def _purity_rating_sensor(rating):
    s = PurityRatingSensor.__new__(PurityRatingSensor)
    s._device = SimpleNamespace(purity_rating=rating)
    return s


class TestPurityRatingSensor:
    def test_native_value_good(self):
        s = _purity_rating_sensor(AirQualityLevelService.RatingState.GOOD)
        assert s.native_value == "GOOD"

    def test_native_value_medium(self):
        s = _purity_rating_sensor(AirQualityLevelService.RatingState.MEDIUM)
        assert s.native_value == "MEDIUM"

    def test_native_value_bad(self):
        s = _purity_rating_sensor(AirQualityLevelService.RatingState.BAD)
        assert s.native_value == "BAD"


# ---------------------------------------------------------------------------
# PowerSensor
# ---------------------------------------------------------------------------


def _power_sensor(powerconsumption):
    s = PowerSensor.__new__(PowerSensor)
    s._device = SimpleNamespace(powerconsumption=powerconsumption)
    return s


class TestPowerSensor:
    def test_native_value(self):
        assert _power_sensor(150.5).native_value == 150.5

    def test_native_value_zero(self):
        assert _power_sensor(0.0).native_value == 0.0

    def test_device_class(self):
        assert _power_sensor(0.0).device_class == SensorDeviceClass.POWER

    def test_unit(self):
        assert _power_sensor(0.0).native_unit_of_measurement == UnitOfPower.WATT

    def test_state_class(self):
        assert _power_sensor(0.0).state_class == SensorStateClass.MEASUREMENT


# ---------------------------------------------------------------------------
# EnergySensor
# ---------------------------------------------------------------------------


def _energy_sensor(energyconsumption_wh):
    s = EnergySensor.__new__(EnergySensor)
    s._device = SimpleNamespace(energyconsumption=energyconsumption_wh)
    return s


class TestEnergySensor:
    def test_native_value_converts_wh_to_kwh(self):
        """energyconsumption is in Wh; native_value must divide by 1000."""
        assert _energy_sensor(5000).native_value == 5.0

    def test_native_value_zero(self):
        assert _energy_sensor(0).native_value == 0.0

    def test_native_value_partial_kwh(self):
        assert _energy_sensor(1500).native_value == 1.5

    def test_device_class(self):
        assert _energy_sensor(0).device_class == SensorDeviceClass.ENERGY

    def test_unit(self):
        assert _energy_sensor(0).native_unit_of_measurement == UnitOfEnergy.KILO_WATT_HOUR

    def test_state_class_total_increasing(self):
        assert _energy_sensor(0).state_class == SensorStateClass.TOTAL_INCREASING


# ---------------------------------------------------------------------------
# CommunicationQualitySensor
# ---------------------------------------------------------------------------


def _comm_quality_sensor(state):
    s = CommunicationQualitySensor.__new__(CommunicationQualitySensor)
    s._device = SimpleNamespace(communicationquality=state)
    return s


class TestCommunicationQualitySensor:
    # #339: native_value is now a lowercase, translatable slug.
    def test_native_value_good(self):
        state = SHCSmartPlugCompact.CommunicationQualityService.State.GOOD
        assert _comm_quality_sensor(state).native_value == "good"

    def test_native_value_medium(self):
        state = SHCSmartPlugCompact.CommunicationQualityService.State.MEDIUM
        assert _comm_quality_sensor(state).native_value == "medium"

    def test_native_value_bad(self):
        state = SHCSmartPlugCompact.CommunicationQualityService.State.BAD
        assert _comm_quality_sensor(state).native_value == "bad"

    def test_native_value_unknown(self):
        state = SHCSmartPlugCompact.CommunicationQualityService.State.UNKNOWN
        assert _comm_quality_sensor(state).native_value == "unknown"

    def test_native_value_fetching(self):
        state = SHCSmartPlugCompact.CommunicationQualityService.State.FETCHING
        assert _comm_quality_sensor(state).native_value == "fetching"

    def test_native_value_normal(self):
        state = SHCSmartPlugCompact.CommunicationQualityService.State.NORMAL
        assert _comm_quality_sensor(state).native_value == "normal"


# ---------------------------------------------------------------------------
# ValveTappetSensor
# ---------------------------------------------------------------------------


def _valve_sensor(position, valvestate):
    s = ValveTappetSensor.__new__(ValveTappetSensor)
    s._device = SimpleNamespace(position=position, valvestate=valvestate)
    return s


class TestValveTappetSensor:
    def test_native_value(self):
        state = ValveTappetService.State.IN_START_POSITION
        assert _valve_sensor(50, state).native_value == 50

    def test_native_value_zero(self):
        state = ValveTappetService.State.IN_START_POSITION
        assert _valve_sensor(0, state).native_value == 0

    def test_extra_state_attributes_adaption_successful(self):
        state = ValveTappetService.State.VALVE_ADAPTION_SUCCESSFUL
        s = _valve_sensor(100, state)
        assert s.extra_state_attributes == {"valve_tappet_state": "VALVE_ADAPTION_SUCCESSFUL"}

    def test_extra_state_attributes_adaption_in_progress(self):
        state = ValveTappetService.State.VALVE_ADAPTION_IN_PROGRESS
        s = _valve_sensor(50, state)
        assert s.extra_state_attributes == {"valve_tappet_state": "VALVE_ADAPTION_IN_PROGRESS"}

    def test_extra_state_attributes_not_available(self):
        state = ValveTappetService.State.NOT_AVAILABLE
        s = _valve_sensor(0, state)
        assert s.extra_state_attributes == {"valve_tappet_state": "NOT_AVAILABLE"}

    def test_extra_state_attributes_value_error_yields_none(self):
        """If valvestate.name raises ValueError, valve_tappet_state must be None."""
        class _BadState:
            @property
            def name(self):
                raise ValueError("unknown state")

        s = ValveTappetSensor.__new__(ValveTappetSensor)
        s._device = SimpleNamespace(position=0, valvestate=_BadState(), name="test-valve")
        assert s.extra_state_attributes == {"valve_tappet_state": None}

    def test_unit_is_percent(self):
        state = ValveTappetService.State.IN_START_POSITION
        assert _valve_sensor(0, state).native_unit_of_measurement == PERCENTAGE

    def test_entity_category_diagnostic(self):
        state = ValveTappetService.State.IN_START_POSITION
        assert _valve_sensor(0, state).entity_category == EntityCategory.DIAGNOSTIC


# ---------------------------------------------------------------------------
# IlluminanceLevelSensor — mirrors test_illuminance.py style
# ---------------------------------------------------------------------------


def _illum_sensor(illuminance_value):
    s = IlluminanceLevelSensor.__new__(IlluminanceLevelSensor)
    s._device = SimpleNamespace(illuminance=illuminance_value)
    return s


class TestIlluminanceLevelSensor:
    # native_value: numeric passthrough, non-numeric coerced to None (#315)
    def test_gen1_string_coerced_none(self):
        assert _illum_sensor("MEDIUM").native_value is None

    def test_gen2_int_value(self):
        assert _illum_sensor(320).native_value == 320

    def test_gen2_int_zero(self):
        assert _illum_sensor(0).native_value == 0

    def test_none_value(self):
        assert _illum_sensor(None).native_value is None

    # static metadata — stable regardless of value (#315)
    def test_state_class_measurement(self):
        assert _illum_sensor(13).state_class == SensorStateClass.MEASUREMENT

    def test_state_class_stable_when_none(self):
        """A None value must keep MEASUREMENT — not re-raise state_class_removed."""
        assert _illum_sensor(None).state_class == SensorStateClass.MEASUREMENT

    def test_device_class_illuminance(self):
        assert _illum_sensor(9).device_class == SensorDeviceClass.ILLUMINANCE

    def test_unit_lux(self):
        assert _illum_sensor(9).native_unit_of_measurement == LIGHT_LUX


# ---------------------------------------------------------------------------
# EnergyYieldSensor / PowerYieldSensor (#331)
# ---------------------------------------------------------------------------


def _energy_yield_sensor(energy_yield):
    s = EnergyYieldSensor.__new__(EnergyYieldSensor)
    s._device = SimpleNamespace(energy_yield=energy_yield)
    return s


def _power_yield_sensor(powerconsumption):
    s = PowerYieldSensor.__new__(PowerYieldSensor)
    s._device = SimpleNamespace(powerconsumption=powerconsumption)
    return s


class TestEnergyYieldSensor:
    def test_wh_to_kwh(self):
        assert _energy_yield_sensor(234.0).native_value == 0.234

    def test_zero(self):
        assert _energy_yield_sensor(0.0).native_value == 0.0

    def test_none_passthrough(self):
        assert _energy_yield_sensor(None).native_value is None

    def test_device_class_energy(self):
        assert _energy_yield_sensor(1.0).device_class == SensorDeviceClass.ENERGY

    def test_state_class_total_increasing(self):
        assert (
            _energy_yield_sensor(1.0).state_class
            == SensorStateClass.TOTAL_INCREASING
        )


def _terminal_temp_sensor(value):
    from custom_components.bosch_shc.sensor import TerminalTemperatureSensor
    s = TerminalTemperatureSensor.__new__(TerminalTemperatureSensor)
    s._device = SimpleNamespace(terminal_temperature=value)
    return s


class TestTerminalTemperatureSensor:
    def test_native_value(self):
        assert _terminal_temp_sensor(20.6).native_value == 20.6

    def test_device_class_temperature(self):
        assert (
            _terminal_temp_sensor(20.6).device_class
            == SensorDeviceClass.TEMPERATURE
        )

    def test_unit_celsius(self):
        assert (
            _terminal_temp_sensor(20.6).native_unit_of_measurement
            == UnitOfTemperature.CELSIUS
        )


class TestPowerYieldSensor:
    def test_positive_yield_from_negative_consumption(self):
        assert _power_yield_sensor(-800.0).native_value == 800.0

    def test_zero_while_consuming(self):
        assert _power_yield_sensor(1.0).native_value == 0.0

    def test_zero_when_zero(self):
        assert _power_yield_sensor(0.0).native_value == 0.0

    def test_none_passthrough(self):
        assert _power_yield_sensor(None).native_value is None

    def test_device_class_power(self):
        assert _power_yield_sensor(-5.0).device_class == SensorDeviceClass.POWER

    def test_unit_watt(self):
        assert (
            _power_yield_sensor(-5.0).native_unit_of_measurement
            == UnitOfPower.WATT
        )
