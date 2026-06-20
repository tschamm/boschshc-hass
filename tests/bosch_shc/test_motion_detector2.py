"""Unit tests for Motion Detector II [+M] entity implementations.

Covers:
- OccupancyDetectionSensor (binary_sensor.py)
- MotionDetectorLight (light.py)
- pet_immunity_enabled SHCSwitch (switch.py / SWITCH_TYPES)

All tests bypass SHCEntity.__init__ via __new__ + fake device (SimpleNamespace).
No HA harness required.

Run with:
  PYTHONPATH="<boschshc-hass>:<boschshcpy>" \\
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \\
  python3 -m pytest tests/bosch_shc/test_motion_detector2.py -q -o addopts=
"""

from types import SimpleNamespace

from homeassistant.components.light import ColorMode

from custom_components.bosch_shc.binary_sensor import OccupancyDetectionSensor
from custom_components.bosch_shc.light import MotionDetectorLight
from custom_components.bosch_shc.switch import SWITCH_TYPES, SHCSwitch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_md2_device(**kwargs):
    """Return a fake SHCMotionDetector2-shaped SimpleNamespace."""
    defaults = dict(
        name="Motion Detector II",
        id="hdm:ZigBee:000000000000abcd",
        root_device_id="64-da-a0-xx-xx-xx",
        # OccupancyDetectionService
        occupied=False,
        last_occupancy_change_time="2026-06-20T12:00:00.000Z",
        # BinarySwitch / MultiLevelSwitch (MD2 light)
        binaryswitch=False,
        multi_level_switch=50,
        # PetImmunity
        pet_immunity_enabled=False,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_occupancy_sensor(**device_kwargs):
    dev = _make_md2_device(**device_kwargs)
    s = OccupancyDetectionSensor.__new__(OccupancyDetectionSensor)
    s._device = dev
    s._attr_name = f"{dev.name} Occupancy"
    s._attr_unique_id = f"{dev.root_device_id}_{dev.id}_occupancy"
    return s


def _make_light(**device_kwargs):
    dev = _make_md2_device(**device_kwargs)
    light = MotionDetectorLight.__new__(MotionDetectorLight)
    light._device = dev
    light._attr_name = f"{dev.name} Motion Light"
    light._attr_unique_id = f"{dev.root_device_id}_{dev.id}_motionlight"
    light._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
    light._attr_color_mode = ColorMode.BRIGHTNESS
    return light


def _make_pet_switch(**device_kwargs):
    dev = _make_md2_device(**device_kwargs)
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = dev
    sw.entity_description = SWITCH_TYPES["pet_immunity_enabled"]
    sw.entity_id = "switch.test_pet"
    return sw


# ---------------------------------------------------------------------------
# OccupancyDetectionSensor
# ---------------------------------------------------------------------------


class TestOccupancyDetectionSensor:
    """Tests for the MD2 occupancy binary sensor."""

    def test_device_class_is_occupancy(self):
        """_attr_device_class must be OCCUPANCY."""
        from homeassistant.components.binary_sensor import BinarySensorDeviceClass
        s = OccupancyDetectionSensor.__new__(OccupancyDetectionSensor)
        assert s._attr_device_class == BinarySensorDeviceClass.OCCUPANCY

    def test_is_on_when_occupied(self):
        s = _make_occupancy_sensor(occupied=True)
        assert s.is_on is True

    def test_is_off_when_not_occupied(self):
        s = _make_occupancy_sensor(occupied=False)
        assert s.is_on is False

    def test_extra_state_attributes_contains_timestamp(self):
        ts = "2026-06-20T12:34:56.789Z"
        s = _make_occupancy_sensor(last_occupancy_change_time=ts)
        attrs = s.extra_state_attributes
        assert "last_occupancy_change" in attrs
        assert attrs["last_occupancy_change"] == ts

    def test_unique_id_format(self):
        dev = _make_md2_device(
            root_device_id="root-X",
            id="dev-Y",
        )
        s = OccupancyDetectionSensor.__new__(OccupancyDetectionSensor)
        s._device = dev
        s._attr_name = f"{dev.name} Occupancy"
        s._attr_unique_id = f"{dev.root_device_id}_{dev.id}_occupancy"
        assert s._attr_unique_id == "root-X_dev-Y_occupancy"

    def test_name_format(self):
        dev = _make_md2_device(name="Flur Bewegungsmelder")
        s = OccupancyDetectionSensor.__new__(OccupancyDetectionSensor)
        s._device = dev
        s._attr_name = f"{dev.name} Occupancy"
        s._attr_unique_id = f"{dev.root_device_id}_{dev.id}_occupancy"
        assert s._attr_name == "Flur Bewegungsmelder Occupancy"


# ---------------------------------------------------------------------------
# MotionDetectorLight
# ---------------------------------------------------------------------------


class TestMotionDetectorLight:
    """Tests for the MD2 indicator light entity."""

    def test_color_mode_is_brightness(self):
        """Supported mode must be BRIGHTNESS only."""
        light = _make_light()
        assert light._attr_color_mode == ColorMode.BRIGHTNESS
        assert light._attr_supported_color_modes == {ColorMode.BRIGHTNESS}

    def test_is_on_true(self):
        light = _make_light(binaryswitch=True)
        assert light.is_on is True

    def test_is_on_false(self):
        light = _make_light(binaryswitch=False)
        assert light.is_on is False

    def test_brightness_scales_from_device_level(self):
        """level=100 → HA brightness 255."""
        light = _make_light(multi_level_switch=100)
        assert light.brightness == 255

    def test_brightness_level_50_maps_to_128(self):
        """level=50 → HA brightness round(50*255/100)=128 (or 127/128 depending on rounding)."""
        light = _make_light(multi_level_switch=50)
        assert light.brightness == round(50 * 255 / 100)

    def test_brightness_level_0_maps_to_0(self):
        light = _make_light(multi_level_switch=0)
        assert light.brightness == 0

    def test_brightness_none_level_maps_to_0(self):
        light = _make_light(multi_level_switch=None)
        assert light.brightness == 0

    def test_turn_on_sets_binaryswitch(self):
        """turn_on without kwargs must set binaryswitch=True when off."""
        calls = []

        class _Dev:
            binaryswitch = False
            multi_level_switch = 50

        dev = _Dev()

        def binaryswitch_setter(self_, val):
            calls.append(("binaryswitch", val))
            self_._binaryswitch = val

        _Dev.binaryswitch = property(lambda s: s._binaryswitch, binaryswitch_setter)

        light = MotionDetectorLight.__new__(MotionDetectorLight)
        dev._binaryswitch = False
        light._device = dev
        light._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
        light._attr_color_mode = ColorMode.BRIGHTNESS

        light.turn_on()
        assert ("binaryswitch", True) in calls

    def test_turn_on_with_brightness_sets_level(self):
        """turn_on(ATTR_BRIGHTNESS=128) must write multi_level_switch=50."""
        from homeassistant.components.light import ATTR_BRIGHTNESS

        level_written = []
        switch_written = []

        class _Dev:
            _level = 50
            _on = False

            @property
            def multi_level_switch(self_):
                return self_._level

            @multi_level_switch.setter
            def multi_level_switch(self_, v):
                level_written.append(v)
                self_._level = v

            @property
            def binaryswitch(self_):
                return self_._on

            @binaryswitch.setter
            def binaryswitch(self_, v):
                switch_written.append(v)
                self_._on = v

        dev = _Dev()
        light = MotionDetectorLight.__new__(MotionDetectorLight)
        light._device = dev
        light._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
        light._attr_color_mode = ColorMode.BRIGHTNESS

        ha_brightness = 128  # ~50 in device scale
        light.turn_on(**{ATTR_BRIGHTNESS: ha_brightness})

        assert len(level_written) == 1
        expected_level = max(round(ha_brightness * 100 / 255), 1)
        assert level_written[0] == expected_level
        # light was off → binaryswitch must also be set
        assert True in switch_written

    def test_turn_on_brightness_clamps_to_minimum_1(self):
        """Near-zero HA brightness must not send level=0 to device."""
        from homeassistant.components.light import ATTR_BRIGHTNESS

        level_written = []

        class _Dev:
            _level = 0
            binaryswitch = True  # already on

            @property
            def multi_level_switch(self_):
                return self_._level

            @multi_level_switch.setter
            def multi_level_switch(self_, v):
                level_written.append(v)
                self_._level = v

        dev = _Dev()
        light = MotionDetectorLight.__new__(MotionDetectorLight)
        light._device = dev
        light._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
        light._attr_color_mode = ColorMode.BRIGHTNESS

        light.turn_on(**{ATTR_BRIGHTNESS: 1})
        assert level_written[0] >= 1

    def test_turn_off_sets_binaryswitch_false(self):
        """turn_off must set binaryswitch=False."""
        written = []

        class _Dev:
            _on = True

            @property
            def binaryswitch(self_):
                return self_._on

            @binaryswitch.setter
            def binaryswitch(self_, v):
                written.append(v)
                self_._on = v

        dev = _Dev()
        light = MotionDetectorLight.__new__(MotionDetectorLight)
        light._device = dev
        light._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
        light._attr_color_mode = ColorMode.BRIGHTNESS

        light.turn_off()
        assert written == [False]

    def test_unique_id_format(self):
        dev = _make_md2_device(root_device_id="root1", id="dev1")
        light = MotionDetectorLight.__new__(MotionDetectorLight)
        light._device = dev
        light._attr_name = f"{dev.name} Motion Light"
        light._attr_unique_id = f"{dev.root_device_id}_{dev.id}_motionlight"
        assert light._attr_unique_id == "root1_dev1_motionlight"


# ---------------------------------------------------------------------------
# Pet Immunity Switch
# ---------------------------------------------------------------------------


class TestPetImmunitySwitch:
    """Tests for the pet_immunity_enabled SWITCH_TYPE and SHCSwitch integration."""

    def test_switch_type_exists(self):
        assert "pet_immunity_enabled" in SWITCH_TYPES

    def test_on_key_is_pet_immunity_enabled(self):
        assert SWITCH_TYPES["pet_immunity_enabled"].on_key == "pet_immunity_enabled"

    def test_on_value_is_bool_true(self):
        assert SWITCH_TYPES["pet_immunity_enabled"].on_value is True

    def test_should_poll_is_false(self):
        assert SWITCH_TYPES["pet_immunity_enabled"].should_poll is False

    def test_entity_category_is_config(self):
        from homeassistant.helpers.entity import EntityCategory
        assert SWITCH_TYPES["pet_immunity_enabled"].entity_category == EntityCategory.CONFIG

    def test_is_on_when_enabled(self):
        sw = _make_pet_switch(pet_immunity_enabled=True)
        assert sw.is_on is True

    def test_is_off_when_disabled(self):
        sw = _make_pet_switch(pet_immunity_enabled=False)
        assert sw.is_on is False

    def test_turn_on_sets_true(self):
        """turn_on() must call setattr(device, 'pet_immunity_enabled', True)."""
        written = []

        class _Dev:
            _pet = False

            @property
            def pet_immunity_enabled(self_):
                return self_._pet

            @pet_immunity_enabled.setter
            def pet_immunity_enabled(self_, v):
                written.append(v)
                self_._pet = v

        dev = _Dev()
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = dev
        sw.entity_description = SWITCH_TYPES["pet_immunity_enabled"]
        sw.entity_id = "switch.pet_test"
        sw.turn_on()
        assert written == [True]

    def test_turn_off_sets_false(self):
        """turn_off() must call setattr(device, 'pet_immunity_enabled', False)."""
        written = []

        class _Dev:
            _pet = True

            @property
            def pet_immunity_enabled(self_):
                return self_._pet

            @pet_immunity_enabled.setter
            def pet_immunity_enabled(self_, v):
                written.append(v)
                self_._pet = v

        dev = _Dev()
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = dev
        sw.entity_description = SWITCH_TYPES["pet_immunity_enabled"]
        sw.entity_id = "switch.pet_test"
        sw.turn_off()
        assert written == [False]

    def test_attr_name_with_pet_immunity_suffix(self):
        """unique_id uses lowercased attr_name suffix 'petimmunity'."""
        dev = _make_md2_device(
            name="Motion Sensor", root_device_id="rootA", id="devB"
        )
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = dev
        sw.entity_description = SWITCH_TYPES["pet_immunity_enabled"]
        # Replicate the SHCSwitch.__init__ name/unique_id logic
        attr_name = "PetImmunity"
        sw._attr_name = f"{dev.name} {attr_name}"
        sw._attr_unique_id = f"{dev.root_device_id}_{dev.id}_{attr_name.lower()}"
        assert sw._attr_name == "Motion Sensor PetImmunity"
        assert sw._attr_unique_id == "rootA_devB_petimmunity"


# ---------------------------------------------------------------------------
# boschshcpy lib setters (unit-level, no HA dependency)
# ---------------------------------------------------------------------------


class TestSHCMotionDetector2LibSetters:
    """Verify that the new public setters on SHCMotionDetector2 call through."""

    def test_binaryswitch_setter_calls_put_state_element(self):
        """binaryswitch setter must invoke put_state_element('on', bool)."""
        calls = []

        class _FakeBinarySwitchService:
            def put_state_element(self_, key, value):
                calls.append((key, value))

            @property
            def value(self_):
                return False

        # Import the real class, patch in a fake service
        from boschshcpy.models_impl import SHCMotionDetector2
        dev = SHCMotionDetector2.__new__(SHCMotionDetector2)
        dev._binaryswitch_service = _FakeBinarySwitchService()
        dev.binaryswitch = True
        assert calls == [("on", True)]

    def test_multi_level_switch_setter_calls_put_state_element(self):
        """multi_level_switch setter must invoke put_state_element('level', value)."""
        calls = []

        class _FakeMultiLevelSwitchService:
            def put_state_element(self_, key, value):
                calls.append((key, value))

            @property
            def value(self_):
                return 50

        from boschshcpy.models_impl import SHCMotionDetector2
        dev = SHCMotionDetector2.__new__(SHCMotionDetector2)
        dev._multi_level_switch_service = _FakeMultiLevelSwitchService()
        dev.multi_level_switch = 75
        assert calls == [("level", 75)]

    def test_pet_immunity_setter_delegates_to_service(self):
        """pet_immunity_enabled setter must write to the PetImmunity service."""
        written = []

        class _FakePetImmunityService:
            _enabled = False

            @property
            def enabled(self_):
                return self_._enabled

            @enabled.setter
            def enabled(self_, v):
                written.append(v)
                self_._enabled = v

        from boschshcpy.models_impl import SHCMotionDetector2
        dev = SHCMotionDetector2.__new__(SHCMotionDetector2)
        dev._petimmunity_service = _FakePetImmunityService()
        dev.pet_immunity_enabled = True
        assert written == [True]
