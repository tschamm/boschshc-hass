"""Unit tests for #338: opt-in to expose a Light/Shutter Control II light relay
(MICROMODULE_LIGHT_ATTACHED) or a BSM light switch as a HA `light` instead of a
`switch`.

Covers the entity.py helpers (light_switch_devices / light_switch_as_light) and
the RelayLight entity's ONOFF behaviour.  Pure logic — no HA harness: RelayLight
is built via __new__ so SHCEntity.__init__ (which needs no hass) is bypassed and
only the properties under test run.
"""

import asyncio
from types import SimpleNamespace

from boschshcpy import SHCLightSwitch
from homeassistant.components.light import ColorMode

from custom_components.bosch_shc.const import OPT_LIGHTS_AS_LIGHT
from custom_components.bosch_shc.entity import (
    light_switch_as_light,
    light_switch_devices,
)
from custom_components.bosch_shc.light import RelayLight

State = SHCLightSwitch.PowerSwitchService.State


def _make_device(**kwargs):
    defaults = dict(
        name="Light Control II",
        root_device_id="root1",
        id="dev1",
        switchstate=State.OFF,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ── entity.light_switch_as_light ─────────────────────────────────────────────

def test_opt_in_true_when_id_listed():
    dev = _make_device(id="abc")
    assert light_switch_as_light(dev, {OPT_LIGHTS_AS_LIGHT: ["abc"]}) is True


def test_opt_in_false_when_id_absent_or_unset():
    dev = _make_device(id="abc")
    assert light_switch_as_light(dev, {OPT_LIGHTS_AS_LIGHT: ["other"]}) is False
    assert light_switch_as_light(dev, {}) is False
    # Stored option may be explicit None (cleared) — must not raise.
    assert light_switch_as_light(dev, {OPT_LIGHTS_AS_LIGHT: None}) is False


# ── entity.light_switch_devices ──────────────────────────────────────────────

def test_devices_combine_both_buckets_bsm_first():
    bsm = _make_device(id="bsm1")
    mm = _make_device(id="mm1")
    session = SimpleNamespace(
        device_helper=SimpleNamespace(
            light_switches_bsm=[bsm],
            micromodule_light_attached=[mm],
        )
    )
    assert light_switch_devices(session) == [bsm, mm]


def test_devices_getattr_safe_when_bucket_missing():
    # An older pinned lib may not expose every bucket → getattr fallback to [].
    session = SimpleNamespace(device_helper=SimpleNamespace())
    assert light_switch_devices(session) == []


# ── light.RelayLight ─────────────────────────────────────────────────────────

def test_relaylight_is_onoff_light():
    # HA's entity metaclass turns the class-body _attr_* into property
    # descriptors, so assert the public color-mode API on an instance.
    lt = RelayLight.__new__(RelayLight)
    assert lt.color_mode == ColorMode.ONOFF
    assert lt.supported_color_modes == {ColorMode.ONOFF}


def test_relaylight_is_on_reflects_switchstate():
    lt = RelayLight.__new__(RelayLight)
    lt._device = _make_device(switchstate=State.ON)
    assert lt.is_on is True
    lt._device = _make_device(switchstate=State.OFF)
    assert lt.is_on is False


def test_relaylight_is_on_none_when_service_unavailable():
    """A relay with no connected load can raise AttributeError → None, no crash."""

    class _NoService:
        @property
        def switchstate(self):
            raise AttributeError("PowerSwitch service is None")

    lt = RelayLight.__new__(RelayLight)
    lt._device = _NoService()
    assert lt.is_on is None


def test_relaylight_turn_on_off_invoke_async_setter():
    calls = []

    async def _fake_set(value):
        calls.append(value)

    lt = RelayLight.__new__(RelayLight)
    lt._device = SimpleNamespace(async_set_switchstate=_fake_set)
    asyncio.run(lt.async_turn_on())
    asyncio.run(lt.async_turn_off())
    assert calls == [True, False]


# ── #338 refinements: global toggle + friendly model label ───────────────────

def test_global_toggle_overrides_per_device():
    from custom_components.bosch_shc.const import OPT_ALL_LIGHTS_AS_LIGHT
    dev = _make_device(id="not-listed")
    # global ON → light regardless of the per-device list
    assert light_switch_as_light(dev, {OPT_ALL_LIGHTS_AS_LIGHT: True}) is True
    assert light_switch_as_light(
        dev, {OPT_ALL_LIGHTS_AS_LIGHT: True, OPT_LIGHTS_AS_LIGHT: []}
    ) is True
    # global OFF → falls back to the per-device list
    assert light_switch_as_light(
        dev, {OPT_ALL_LIGHTS_AS_LIGHT: False, OPT_LIGHTS_AS_LIGHT: ["not-listed"]}
    ) is True
    assert light_switch_as_light(dev, {OPT_ALL_LIGHTS_AS_LIGHT: False}) is False


def test_friendly_model_label():
    from custom_components.bosch_shc.entity import light_relay_friendly_model
    assert light_relay_friendly_model(
        _make_device(device_model="MICROMODULE_LIGHT_ATTACHED")
    ) == "Light/Shutter Control II"
    assert light_relay_friendly_model(_make_device(device_model="BSM")) == \
        "In-wall light switch"
    # unknown model falls back to the raw model string (never crashes/blank)
    assert light_relay_friendly_model(_make_device(device_model="WHATEVER")) == \
        "WHATEVER"
