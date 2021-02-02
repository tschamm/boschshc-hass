"""Constants for the Bosch SHC integration."""

ATTR_NAME = "name"
ATTR_EVENT_TYPE = "event_type"
ATTR_EVENT_SUBTYPE = "event_subtype"
ATTR_LAST_TIME_TRIGGERED = "lastTimeTriggered"

CONF_SUBTYPE = "subtype"
CONF_SSL_CERTIFICATE = "ssl_certificate"
CONF_SSL_KEY = "ssl_key"

DOMAIN = "bosch_shc"

EVENT_BOSCH_SHC = "bosch_shc.event"

SERVICE_SMOKEDETECTOR_CHECK = "smokedetector_check"
SERVICE_SMOKEDETECTOR_ALARMSTATE = "smokedetector_alarmstate"
SERVICE_TRIGGER_SCENARIO = "trigger_scenario"

SUPPORTED_INPUTS_EVENTS_TYPES = {
    "PRESS_SHORT",
    "PRESS_LONG",
    "MOTION",
    "SCENARIO",
}

INPUTS_EVENTS_SUBTYPES = {
    "LOWER_BUTTON",
    "UPPER_BUTTON",
}
