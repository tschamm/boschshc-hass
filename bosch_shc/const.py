"""Constants for the Bosch SHC integration."""

ATTR_NAME = "name"
ATTR_CLICK_TYPE = "click_type"
ATTR_BUTTON = "button"
ATTR_LAST_TIME_TRIGGERED = "lastTimeTriggered"

CONF_SSL_CERTIFICATE = "ssl_certificate"
CONF_SSL_KEY = "ssl_key"
CONF_SUBTYPE = "subtype"

DOMAIN = "bosch_shc"

EVENT_BOSCH_SHC_CLICK = "bosch_shc.click"
EVENT_BOSCH_SHC_SCENARIO_TRIGGER = "bosch_shc.scenario_trigger"

SERVICE_TRIGGER_SCENARIO = "trigger_scenario"

SUPPORTED_INPUTS_EVENTS_TYPES = {
    "PRESS_SHORT",
    "PRESS_LONG",
}

INPUTS_EVENTS_SUBTYPES = {
    "LOWER_BUTTON": 1,
    "UPPER_BUTTON": 2,
}