"""Describe Shelly logbook events."""

from homeassistant.const import ATTR_NAME
from homeassistant.core import callback

from .const import ATTR_EVENT_SUBTYPE, ATTR_EVENT_TYPE, DOMAIN, EVENT_BOSCH_SHC


@callback
def async_describe_events(hass, async_describe_event):
    """Describe logbook events."""

    @callback
    def async_describe_bosch_shc_scenario_trigger_event(event):
        """Describe bosch_shc.scenario_trigger logbook event."""

        name = event.data[ATTR_NAME]

        return {
            "name": "Bosch SHC",
            "message": f"'{name}' scenario trigger event was fired.",
        }

    @callback
    def async_describe_bosch_shc_event(event):
        """Describe bosch_shc.click logbook event."""

        device_name = event.data[ATTR_NAME]
        button = event.data[ATTR_EVENT_SUBTYPE]
        event_type = event.data[ATTR_EVENT_TYPE]

        if event_type == "MOTION":
            return {
                "name": "Bosch SHC",
                "message": f"'{device_name}' motion event was fired.",
            }

        if event_type == "SCENARIO":
            return {
                "name": "Bosch SHC",
                "message": f"'{device_name}' scenario trigger event was fired.",
            }

        return {
            "name": "Bosch SHC",
            "message": f"'{event_type}' click event for {device_name} button '{button}' was fired.",
        }

    async_describe_event(
        DOMAIN,
        EVENT_BOSCH_SHC,
        async_describe_bosch_shc_event,
    )
