"""Constants for the Bosch SHC integration."""
import logging

ATTR_NAME = "name"
ATTR_EVENT_TYPE = "event_type"
ATTR_EVENT_SUBTYPE = "event_subtype"
ATTR_LAST_TIME_TRIGGERED = "lastTimeTriggered"
ATTR_SERVICE_ID = "service_id"
ATTR_TITLE = "title"

CONF_HOSTNAME = "hostname"
CONF_SHC_CERT = "bosch_shc-cert"
CONF_SHC_KEY = "bosch_shc-key"
CONF_SUBTYPE = "subtype"
CONF_SSL_CERTIFICATE = "ssl_certificate"
CONF_SSL_KEY = "ssl_key"

DATA_SESSION = "session"
DATA_SHC = "shc"
DATA_TITLE = "title"
DATA_POLLING_HANDLER = "polling_handler"

DOMAIN = "bosch_shc"

EVENT_BOSCH_SHC = "bosch_shc.event"

LOGGER = logging.getLogger(__package__)

SERVICE_SMOKEDETECTOR_CHECK = "smokedetector_check"
SERVICE_SMOKEDETECTOR_ALARMSTATE = "smokedetector_alarmstate"
SERVICE_TRIGGER_SCENARIO = "trigger_scenario"
SERVICE_TRIGGER_RAWSCAN = "trigger_rawscan"

SUPPORTED_INPUTS_EVENTS_TYPES = {
    "PRESS_SHORT",
    "PRESS_LONG",
    "PRESS_LONG_RELEASED",
    "MOTION",
    "SCENARIO",
    "ALARM",
}

INPUTS_EVENTS_SUBTYPES_WRC2 = {
    "LOWER_BUTTON",
    "UPPER_BUTTON",
}

INPUTS_EVENTS_SUBTYPES_SWITCH2 = {
    "LOWER_LEFT_BUTTON",
    "LOWER_RIGHT_BUTTON",
    "UPPER_LEFT_BUTTON",
    "UPPER_RIGHT_BUTTON",
}

ALARM_EVENTS_SUBTYPES_SD = {
    "IDLE_OFF",
    "INTRUSION_ALARM",
    "SECONDARY_ALARM",
    "PRIMARY_ALARM",
}

ALARM_EVENTS_SUBTYPES_SDS = {
    "ALARM_OFF",
    "ALARM_ON",
    "ALARM_MUTED",
}
