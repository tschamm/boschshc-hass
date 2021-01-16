"""Describe Shelly logbook events."""

from homeassistant.const import ATTR_DEVICE_ID, ATTR_NAME
from homeassistant.core import callback

from .const import (
    ATTR_CLICK_TYPE,
    DOMAIN,
    EVENT_BOSCH_SHC_SCENARIO_TRIGGER,
)

@callback
def async_describe_events(hass, async_describe_event):
    """Describe logbook events."""

    @callback
    def async_describe_scenario_trigger_event(event):
        """Describe bosch_shc.scenario_trigger logbook event."""

        name = event.data[ATTR_NAME]

        return {
            "name": "Bosch SHC",
            "message": f"'{name}' scenario trigger event was fired.",
        }

    async_describe_event(DOMAIN, EVENT_BOSCH_SHC_SCENARIO_TRIGGER, async_describe_scenario_trigger_event)
