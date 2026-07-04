"""Isolation-safe tests for diagnostics.py (no HA harness).

Drives async_get_config_entry_diagnostics with fake hass/entry/session and
asserts the structure + that credentials / network PII are redacted while device
names and service states are preserved.
"""
import asyncio
from types import SimpleNamespace

from homeassistant.components.diagnostics import REDACTED

from custom_components.bosch_shc.const import DATA_SESSION, DOMAIN
from custom_components.bosch_shc.diagnostics import (
    async_get_config_entry_diagnostics,
)


def _entry():
    return SimpleNamespace(
        entry_id="E1",
        title="Bosch SHC",
        data={
            "host": "192.0.2.10",
            "password": "supersecret",
            "ssl_certificate": "/config/cert.pem",
            "ssl_key": "/config/key.pem",
            "token": "tok-123",
            "name": "Bosch SHC",
        },
        options={},
    )


def _session():
    shutter = SimpleNamespace(
        id="hdm:ZigBee:abc123",
        root_device_id="aa:bb:cc:dd:ee:ff",
        device_model="MICROMODULE_SHUTTER",
        manufacturer="BOSCH",
        name="Living Room Shutter",
        room_id="hz_1",
        serial="SERIAL-9999",
        device_services=[
            SimpleNamespace(
                id="ShutterControl",
                state={
                    "@type": "shutterControlState",
                    "level": 0.5,
                    "operationState": "MOVING",
                },
            )
        ],
    )
    info = SimpleNamespace(
        version="10.25.0",
        # This integration only ever constructs SHCSessionAsync, whose
        # .information is _AsyncSHCInformation: a plain string update_state,
        # no updateState enum attribute at all. Mocking the old sync shape
        # here (updateState=SimpleNamespace(name=...)) previously masked a
        # real, 100%-reproducible AttributeError crash in diagnostics.py.
        update_state="NO_UPDATE_AVAILABLE",
        macAddress="aa:bb:cc:dd:ee:ff",
        shcIpAddress="192.0.2.10",
    )
    return SimpleNamespace(information=info, devices=[shutter])


def _run(hass, entry):
    return asyncio.run(async_get_config_entry_diagnostics(hass, entry))


def test_redacts_entry_credentials_keeps_title():
    hass = SimpleNamespace(data={DOMAIN: {"E1": {DATA_SESSION: _session()}}})
    diag = _run(hass, _entry())
    d = diag["entry"]["data"]
    assert d["password"] == REDACTED
    assert d["ssl_certificate"] == REDACTED
    assert d["ssl_key"] == REDACTED
    assert d["token"] == REDACTED
    assert d["host"] == REDACTED
    assert d["name"] == "Bosch SHC"  # not a secret, kept
    assert diag["entry"]["title"] == "Bosch SHC"


def test_shc_block_redacts_mac_and_ip_keeps_version():
    hass = SimpleNamespace(data={DOMAIN: {"E1": {DATA_SESSION: _session()}}})
    diag = _run(hass, _entry())
    assert diag["shc"]["macAddress"] == REDACTED
    assert diag["shc"]["ip"] == REDACTED
    assert diag["shc"]["version"] == "10.25.0"
    assert diag["shc"]["update_state"] == "NO_UPDATE_AVAILABLE"


def test_shc_block_falls_back_to_sync_update_state_enum():
    """Compat: if a differently-shaped session ever exposes the old sync
    SHCInformation.updateState enum instead of the async update_state
    string, the enum's .name must still be used."""
    shc_info = SimpleNamespace(
        version="10.25.0",
        updateState=SimpleNamespace(name="UPDATE_AVAILABLE"),
        macAddress="aa:bb:cc:dd:ee:ff",
        shcIpAddress="192.0.2.10",
    )
    session = SimpleNamespace(information=shc_info, devices=[])
    hass = SimpleNamespace(data={DOMAIN: {"E1": {DATA_SESSION: session}}})
    diag = _run(hass, _entry())
    assert diag["shc"]["update_state"] == "UPDATE_AVAILABLE"


def test_device_dump_redacts_pii_keeps_name_model_state():
    hass = SimpleNamespace(data={DOMAIN: {"E1": {DATA_SESSION: _session()}}})
    diag = _run(hass, _entry())
    assert diag["device_count"] == 1
    dev = diag["devices"][0]
    # PII redacted
    assert dev["root_device_id"] == REDACTED
    assert dev["serial"] == REDACTED
    # device_id embeds a hardware address for Zigbee devices
    # (e.g. "hdm:ZigBee:5c0272fffe462481") — same class of PII as
    # macAddress/serial/root_device_id, must be redacted too.
    assert dev["device_id"] == REDACTED
    # debugging-relevant fields kept
    assert dev["name"] == "Living Room Shutter"
    assert dev["device_model"] == "MICROMODULE_SHUTTER"
    # service.id (e.g. "ShutterControl") is not identifying and must survive
    # redaction — it's needed to read the dump.
    assert dev["services"][0]["id"] == "ShutterControl"
    state = dev["services"][0]["state"]
    assert state["operationState"] == "MOVING"
    assert state["level"] == 0.5


def test_session_not_loaded():
    hass = SimpleNamespace(data={DOMAIN: {"E1": {}}})
    diag = _run(hass, _entry())
    assert diag["session"] == "not loaded"
    # entry data still present + redacted
    assert diag["entry"]["data"]["password"] == REDACTED


def test_domain_missing_entirely():
    hass = SimpleNamespace(data={})
    diag = _run(hass, _entry())
    assert diag["session"] == "not loaded"


def test_integration_version_present():
    hass = SimpleNamespace(data={DOMAIN: {"E1": {DATA_SESSION: _session()}}})
    diag = _run(hass, _entry())
    assert isinstance(diag["integration_version"], str)
    assert diag["integration_version"]  # non-empty
