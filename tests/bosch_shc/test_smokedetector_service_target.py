"""Regression test for Fix #43: smokedetector services must use target: not entity_id field.

Entity services registered via platform.async_register_entity_service() receive
their target entity through target.entity_id, not via service data. The services.yaml
must declare target: with an entity selector (not a fields.entity_id text field).
"""

import pathlib
import yaml


SERVICES_YAML = (
    pathlib.Path(__file__).parent.parent.parent
    / "custom_components/bosch_shc/services.yaml"
)


def _load_services():
    with open(SERVICES_YAML) as fh:
        return yaml.safe_load(fh)


def test_smokedetector_check_has_target():
    """smokedetector_check declares target: (entity picker, not data field)."""
    services = _load_services()
    assert "target" in services["smokedetector_check"], (
        "smokedetector_check must declare target: for entity targeting"
    )


def test_smokedetector_check_has_no_entity_id_field():
    """smokedetector_check must NOT have fields.entity_id (would be a spurious text box)."""
    services = _load_services()
    fields = services["smokedetector_check"].get("fields") or {}
    assert "entity_id" not in fields, (
        "smokedetector_check.fields.entity_id must be removed; "
        "entity targeting goes through target:, not service data"
    )


def test_smokedetector_alarmstate_has_target():
    """smokedetector_alarmstate declares target: (entity picker)."""
    services = _load_services()
    assert "target" in services["smokedetector_alarmstate"], (
        "smokedetector_alarmstate must declare target: for entity targeting"
    )


def test_smokedetector_alarmstate_has_no_entity_id_field():
    """smokedetector_alarmstate must NOT have fields.entity_id."""
    services = _load_services()
    fields = services["smokedetector_alarmstate"].get("fields") or {}
    assert "entity_id" not in fields, (
        "smokedetector_alarmstate.fields.entity_id must be removed; "
        "entity targeting goes through target:, not service data"
    )


def test_smokedetector_alarmstate_keeps_command_field():
    """smokedetector_alarmstate still has fields.command (the real service parameter)."""
    services = _load_services()
    fields = services["smokedetector_alarmstate"].get("fields") or {}
    assert "command" in fields, (
        "smokedetector_alarmstate.fields.command must remain (it is the actual service parameter)"
    )


def test_smokedetector_check_target_scoped_to_integration():
    """smokedetector_check target entity selector is scoped to bosch_shc integration."""
    services = _load_services()
    target = services["smokedetector_check"]["target"]
    assert "entity" in target
    assert target["entity"].get("integration") == "bosch_shc"


def test_smokedetector_alarmstate_target_scoped_to_integration():
    """smokedetector_alarmstate target entity selector is scoped to bosch_shc integration."""
    services = _load_services()
    target = services["smokedetector_alarmstate"]["target"]
    assert "entity" in target
    assert target["entity"].get("integration") == "bosch_shc"
