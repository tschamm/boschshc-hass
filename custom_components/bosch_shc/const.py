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
DATA_CERT_CHECK_UNSUB = "cert_check_unsub"

DOMAIN = "bosch_shc"

EVENT_BOSCH_SHC = "bosch_shc.event"

LOGGER = logging.getLogger(__package__)

SERVICE_SMOKEDETECTOR_CHECK = "smokedetector_check"
SERVICE_SMOKEDETECTOR_ALARMSTATE = "smokedetector_alarmstate"
SERVICE_TRIGGER_SCENARIO = "trigger_scenario"
SERVICE_TRIGGER_RAWSCAN = "trigger_rawscan"

# Options flow keys
OPT_SCENARIOS_AS_BUTTONS = "scenarios_as_buttons"
OPT_DIAGNOSTIC_ENTITIES = "diagnostic_entities"
OPT_ENABLE_RAWSCAN = "enable_rawscan_service"
OPT_SSL_VERIFY_HOSTNAME = "ssl_verify_hostname"
OPT_LONG_POLL_TIMEOUT = "long_poll_timeout"
OPT_CHILD_LOCK_ENABLED = "child_lock_enabled"
OPT_PRESENCE_ENTITY = "child_lock_presence_entity"
OPT_EXCLUDED_DEVICES = "excluded_devices"
OPT_EXCLUDED_ROOMS = "excluded_rooms"
# #338: per-device opt-in to expose a Light/Shutter Control II (or BSM) light
# relay as a HA `light` entity instead of the default `switch`. Holds a list of
# device ids; default [] keeps the historical `switch` behaviour (non-breaking).
OPT_LIGHTS_AS_LIGHT = "light_switches_as_light"
# #338 follow-up: a single toggle to expose ALL eligible light relays as lights
# at once (overrides the per-device list above). Default False = unchanged.
OPT_ALL_LIGHTS_AS_LIGHT = "all_light_switches_as_light"
# #264: opt-in skip of SHC server-certificate verification (expired cert on an
# offline local-only controller). mTLS client-cert auth is unaffected.
OPT_SSL_SKIP_VERIFY = "ssl_skip_verify"
# #344: opt-out to suppress Hue lights from SHC (avoids duplicates when Hue
# integration is also active). Default False = keep existing behaviour.
OPT_SUPPRESS_HUE_LIGHTS = "suppress_hue_lights"
# Presence + time-window driven silent mode (mirrors the child-lock feature).
OPT_SILENT_MODE_ENABLED = "silent_mode_enabled"
OPT_SILENT_MODE_START = "silent_mode_start"
OPT_SILENT_MODE_END = "silent_mode_end"

# Camera Tool companion project
CAMERA_TOOL_URL = (
    "https://github.com/mosandlt/Bosch-Smart-Home-Camera-Tool-HomeAssistant"
)
CAMERA_TOOL_DOMAIN = "bosch_shc_camera"
ISSUE_CAMERA_TOOL = "camera_tool_available"

# Certificate handling
CERT_EXPIRY_WARNING_DAYS = 30
DOMAIN_NOTIFICATION_ID = "bosch_shc_certificate"

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
