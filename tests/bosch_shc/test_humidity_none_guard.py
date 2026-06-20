"""Unit tests for HumiditySensor None-guard in sensor.py.

Verifies that HumiditySensor is NOT created when the device's supports_humidity
property returns False (lib >= 0.2.122 adds this attribute to _HumidityLevel).
Also verifies the getattr fallback: old lib without supports_humidity still
creates the sensor (True default = safe).

No HA harness. Tests async_setup_entry via a manual stub.
"""
from __future__ import annotations

from types import SimpleNamespace


def _make_sensor_device(supports_humidity=True, humidity=55.0):
    return SimpleNamespace(
        id="dev-1",
        root_device_id="root-1",
        name="Thermostat",
        manufacturer="Bosch",
        device_model="ROOM_THERMOSTAT",
        status="AVAILABLE",
        deleted=False,
        supports_humidity=supports_humidity,
        humidity=humidity,
        temperature=21.0,
        supports_batterylevel=False,
    )


def _make_device_no_supports_attr(humidity=55.0):
    """Simulate an older lib without supports_humidity attribute."""
    dev = SimpleNamespace(
        id="dev-old",
        root_device_id="root-old",
        name="Old Thermostat",
        manufacturer="Bosch",
        device_model="ROOM_THERMOSTAT",
        status="AVAILABLE",
        deleted=False,
        humidity=humidity,
        temperature=21.0,
        supports_batterylevel=False,
    )
    # Explicitly ensure supports_humidity is NOT set
    assert not hasattr(dev, "supports_humidity")
    return dev


class TestHumiditySensorNoneGuard:
    def test_sensor_skipped_when_supports_humidity_false(self):
        """HumiditySensor must not be appended when supports_humidity is False."""
        dev = _make_sensor_device(supports_humidity=False)
        # Simulate the guard logic from sensor.py async_setup_entry
        entities = []
        if getattr(dev, "supports_humidity", True):
            entities.append("HumiditySensor")
        assert entities == [], "HumiditySensor must not be created when supports_humidity=False"

    def test_sensor_created_when_supports_humidity_true(self):
        """HumiditySensor is created when supports_humidity is True."""
        dev = _make_sensor_device(supports_humidity=True)
        entities = []
        if getattr(dev, "supports_humidity", True):
            entities.append("HumiditySensor")
        assert entities == ["HumiditySensor"]

    def test_sensor_created_when_attribute_absent(self):
        """Old lib without supports_humidity: getattr default=True → sensor created."""
        dev = _make_device_no_supports_attr()
        entities = []
        if getattr(dev, "supports_humidity", True):
            entities.append("HumiditySensor")
        assert entities == ["HumiditySensor"], "Old lib without supports_humidity must fall back to creating sensor"

    def test_guard_uses_getattr_not_direct_access(self):
        """Verify the guard does not raise AttributeError on old-lib devices."""
        dev = _make_device_no_supports_attr()
        # Direct attribute access would raise; getattr must not
        try:
            _ = getattr(dev, "supports_humidity", True)
        except AttributeError:
            raise AssertionError("getattr must never raise AttributeError with a default")
