"""Isolation-safe unit tests for logbook.py.

Tests the pure inner function async_describe_bosch_shc_event directly by
capturing it via a stub async_describe_event callback — no HA harness needed.
PIN_EVERY_MODE: one test per discrete event_type branch + fallback variants.
"""

from types import SimpleNamespace

from custom_components.bosch_shc.logbook import async_describe_events


def _get_describer():
    """Return the inner async_describe_bosch_shc_event function."""
    captured = {}

    def fake_async_describe_event(domain, event_name, fn):
        captured["fn"] = fn

    async_describe_events(hass=None, async_describe_event=fake_async_describe_event)
    return captured["fn"]


def _event(event_type, event_subtype="some_subtype", device_name="TestDevice"):
    return SimpleNamespace(
        data={
            "name": device_name,
            "event_type": event_type,
            "event_subtype": event_subtype,
        }
    )


describe = _get_describer()


# ---------------------------------------------------------------------------
# MOTION
# ---------------------------------------------------------------------------

def test_motion_event_name():
    result = describe(_event("MOTION"))
    assert result["name"] == "Bosch SHC"


def test_motion_event_message_contains_device():
    result = describe(_event("MOTION", device_name="Garten"))
    assert result["message"] == "'Garten' motion event was fired."


def test_motion_event_subtype_not_in_message():
    result = describe(_event("MOTION", event_subtype="IGNORED_SUBTYPE"))
    assert "IGNORED_SUBTYPE" not in result["message"]


# ---------------------------------------------------------------------------
# ALARM
# ---------------------------------------------------------------------------

def test_alarm_event_name():
    result = describe(_event("ALARM", event_subtype="INTRUSION_DETECTED"))
    assert result["name"] == "Bosch SHC"


def test_alarm_event_message_contains_device_and_subtype():
    result = describe(_event("ALARM", event_subtype="INTRUSION_DETECTED", device_name="Eingang"))
    assert result["message"] == "'Eingang' alarm event 'INTRUSION_DETECTED' was fired."


def test_alarm_event_different_subtype():
    result = describe(_event("ALARM", event_subtype="TILT_DETECTED", device_name="Fenster"))
    assert "TILT_DETECTED" in result["message"]
    assert "'Fenster'" in result["message"]


# ---------------------------------------------------------------------------
# SCENARIO
# ---------------------------------------------------------------------------

def test_scenario_event_name():
    result = describe(_event("SCENARIO"))
    assert result["name"] == "Bosch SHC"


def test_scenario_event_message_contains_device():
    result = describe(_event("SCENARIO", device_name="Szene1"))
    assert result["message"] == "'Szene1' scenario trigger event was fired."


def test_scenario_event_subtype_not_in_message():
    result = describe(_event("SCENARIO", event_subtype="IGNORED"))
    assert "IGNORED" not in result["message"]


# ---------------------------------------------------------------------------
# Fallback / click event (any unknown event_type)
# ---------------------------------------------------------------------------

def test_unknown_event_type_falls_back_to_click_format():
    result = describe(_event("PRESS_SHORT", event_subtype="TOP", device_name="Wandschalter"))
    assert result["name"] == "Bosch SHC"
    assert result["message"] == "'PRESS_SHORT' click event for Wandschalter button 'TOP' was fired."


def test_unknown_event_type_includes_subtype():
    result = describe(_event("PRESS_LONG", event_subtype="BOTTOM", device_name="Schalter"))
    assert "BOTTOM" in result["message"]
    assert "PRESS_LONG" in result["message"]


def test_garbage_event_type_falls_back_gracefully():
    result = describe(_event("!!!INVALID!!!", event_subtype="xyz", device_name="Dev"))
    assert result["name"] == "Bosch SHC"
    assert "!!!INVALID!!!" in result["message"]
    assert "xyz" in result["message"]


def test_empty_string_event_type_falls_back():
    result = describe(_event("", event_subtype="sub", device_name="Dev"))
    assert result["name"] == "Bosch SHC"
    # empty string is not MOTION/ALARM/SCENARIO → click branch
    assert "sub" in result["message"]


# ---------------------------------------------------------------------------
# Message structure invariants
# ---------------------------------------------------------------------------

def test_all_branches_return_name_and_message_keys():
    for event_type in ("MOTION", "ALARM", "SCENARIO", "CLICK"):
        result = describe(_event(event_type, event_subtype="sub", device_name="Dev"))
        assert "name" in result, f"missing 'name' for {event_type}"
        assert "message" in result, f"missing 'message' for {event_type}"


def test_device_name_with_special_chars():
    result = describe(_event("MOTION", device_name="Kamera 'Außen'"))
    assert "Kamera 'Außen'" in result["message"]
