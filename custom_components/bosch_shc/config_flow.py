"""Config flow for Bosch Smart Home Controller integration."""

from __future__ import annotations

import os
from contextlib import suppress
from os import makedirs
from typing import Any

import voluptuous as vol
from boschshcpy import SHCRegisterClient, SHCSession
from boschshcpy.exceptions import (
    SHCAuthenticationError,
    SHCConnectionError,
    SHCRegistrationError,
    SHCSessionError,
)
from homeassistant import config_entries, core
from homeassistant.components import zeroconf
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PASSWORD, CONF_TOKEN
from homeassistant.data_entry_flow import FlowResult, section
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
    TimeSelector,
)
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .const import (
    CAMERA_TOOL_DOMAIN,
    CAMERA_TOOL_URL,
    CONF_HOSTNAME,
    CONF_SHC_CERT,
    CONF_SHC_KEY,
    CONF_SSL_CERTIFICATE,
    CONF_SSL_KEY,
    DOMAIN,
    LOGGER,
    OPT_ALL_LIGHTS_AS_LIGHT,
    OPT_CHILD_LOCK_ENABLED,
    OPT_DIAGNOSTIC_ENTITIES,
    OPT_ENABLE_RAWSCAN,
    OPT_EXCLUDED_DEVICES,
    OPT_EXCLUDED_ROOMS,
    OPT_LIGHTS_AS_LIGHT,
    OPT_LONG_POLL_TIMEOUT,
    OPT_PRESENCE_ENTITY,
    OPT_ROOM_LIGHT_GROUPS,
    OPT_SCENARIOS_AS_BUTTONS,
    OPT_SCENARIOS_FILTER,
    OPT_SILENT_MODE_ENABLED,
    OPT_SILENT_MODE_END,
    OPT_SILENT_MODE_START,
    OPT_SSL_SKIP_VERIFY,
    OPT_SSL_VERIFY_HOSTNAME,
    OPT_SUPPRESS_CAMERA_SWITCHES,
    OPT_SUPPRESS_HUE_LIGHTS,
    OPT_SUPPRESS_LEDVANCE_LIGHTS,
    OPT_SUPPRESS_MOTION_INDICATOR_LIGHT,
    OPT_SUPPRESS_POWER_SENSORS,
)
from .entity import light_relay_friendly_model, light_switch_devices

# ── Section layout (single source of truth) ──────────────────────────────────
# Maps each section key to the flat OPT_* keys it contains.
# _flatten_sections() uses this to lift nested section dicts back to the flat
# shape that the rest of the integration (sensor.py, __init__.py) expects.
OPTIONS_SECTIONS: dict[str, list[str]] = {
    "features": [
        OPT_SCENARIOS_AS_BUTTONS,
        OPT_DIAGNOSTIC_ENTITIES,
        OPT_ENABLE_RAWSCAN,
        OPT_ALL_LIGHTS_AS_LIGHT,
        OPT_LIGHTS_AS_LIGHT,
        OPT_SUPPRESS_HUE_LIGHTS,
        OPT_SUPPRESS_LEDVANCE_LIGHTS,
        OPT_SUPPRESS_POWER_SENSORS,
        OPT_SUPPRESS_MOTION_INDICATOR_LIGHT,
        OPT_SCENARIOS_FILTER,
        OPT_SUPPRESS_CAMERA_SWITCHES,
        OPT_ROOM_LIGHT_GROUPS,
    ],
    "presence": [
        OPT_CHILD_LOCK_ENABLED,
        OPT_PRESENCE_ENTITY,
        OPT_SILENT_MODE_ENABLED,
        OPT_SILENT_MODE_START,
        OPT_SILENT_MODE_END,
    ],
    "advanced": [
        OPT_SSL_VERIFY_HOSTNAME,
        OPT_SSL_SKIP_VERIFY,
        OPT_LONG_POLL_TIMEOUT,
        OPT_EXCLUDED_DEVICES,
        OPT_EXCLUDED_ROOMS,
    ],
}


def _flatten_sections(user_input: dict[str, Any]) -> dict[str, Any]:
    """Flatten section-grouped submit dict back to a single flat dict.

    HA's section() helper returns nested input in the shape
    {section_key: {field: value, ...}, ...}.  This helper lifts every nested
    field up to the top level so the rest of the integration keeps reading
    flat OPT_* keys unchanged.

    Non-sectioned keys (e.g. from older tests or programmatic updates)
    pass through unchanged.  Duplicate keys raise ValueError.
    """
    flat: dict[str, Any] = {}
    seen_section_keys: set[str] = set()

    for section_key in OPTIONS_SECTIONS:
        seen_section_keys.add(section_key)
        sec_payload = user_input.get(section_key)
        if sec_payload is None or not isinstance(sec_payload, dict):
            continue
        for field, value in sec_payload.items():
            if field in flat:
                raise ValueError(
                    f"_flatten_sections: duplicate key {field!r} from "
                    f"section {section_key!r}"
                )
            flat[field] = value

    for key, value in user_input.items():
        if key in seen_section_keys:
            continue
        if key in flat:
            raise ValueError(
                f"_flatten_sections: duplicate key {key!r} at top level and inside a section"
            )
        flat[key] = value

    return flat


HOST_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): TextSelector(
            TextSelectorConfig(type=TextSelectorType.TEXT)
        ),
    }
)


def write_tls_asset(hass: core.HomeAssistant, filename: str, asset: bytes) -> None:
    """Write the tls assets to disk with owner-only permissions (0o600)."""
    makedirs(hass.config.path(DOMAIN), exist_ok=True)
    path = hass.config.path(DOMAIN, filename)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf8") as file_handle:
        file_handle.write(asset.decode("utf-8"))


def create_credentials_and_validate(
    hass: core.HomeAssistant,
    host: str,
    user_input: dict[str, Any],
    zeroconf_instance: Any,
) -> Any:
    """Create and store credentials and validate session."""
    helper = SHCRegisterClient(host, user_input[CONF_PASSWORD])
    result = helper.register(user_input[CONF_NAME].lower(), user_input[CONF_NAME])

    if result is not None:
        hostname = result["token"].split(":", 1)[1]
        cert_path = hass.config.path(DOMAIN, CONF_SHC_CERT + "_" + hostname + ".pem")
        key_path = hass.config.path(DOMAIN, CONF_SHC_KEY + "_" + hostname + ".pem")
        write_tls_asset(hass, CONF_SHC_CERT + "_" + hostname + ".pem", result["cert"])
        write_tls_asset(hass, CONF_SHC_KEY + "_" + hostname + ".pem", result["key"])

        session = SHCSession(
            host,
            cert_path,
            key_path,
            True,
            zeroconf_instance,
        )
        try:
            session.authenticate()
        except Exception:
            # Don't leave an orphaned cert/key pair on disk for a pairing
            # attempt that didn't complete — the caller's except clauses
            # handle showing the user an error; re-raise unchanged.
            for path in (cert_path, key_path):
                with suppress(FileNotFoundError):
                    os.remove(path)
            raise

    return result


def get_info_from_host(
    hass: core.HomeAssistant,
    host: str,
    zeroconf_instance: Any,
) -> dict[str, Any]:
    """Get information from host."""
    session = SHCSession(
        host,
        "",
        "",
        True,
        zeroconf_instance,
    )
    information = session.mdns_info()
    return {"title": information.name, "unique_id": information.unique_id}


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):  # type: ignore[call-arg, misc]
    """Handle a config flow for Bosch SHC."""

    VERSION = 1
    info: dict[str, Any] | None = None
    host: str | None = None
    hostname: str | None = None

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> OptionsFlowHandler:
        """Return the options flow for this handler."""
        return OptionsFlowHandler()

    async def async_step_reauth(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Perform reauth upon an API authentication error."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Dialog that informs the user that reauth is required."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST]
            try:
                info = await self._get_info(host)
            except SHCConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except  # noqa: BLE001
                LOGGER.exception("Unexpected exception during reauth_confirm")
                errors["base"] = "unknown"
            else:
                # Guard against reauth-ing entry A's credentials onto a
                # different SHC (typo, DHCP reassignment landing on a second
                # controller) — mirrors reconfigure_host/repair_credentials.
                await self.async_set_unique_id(info["unique_id"])
                self._abort_if_unique_id_mismatch(reason="wrong_shc")
                self.host = host
                self.info = info
                return await self.async_step_credentials()

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=HOST_SCHEMA,
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Show a menu: change host only, or re-pair (regenerate certificate)."""
        return self.async_show_menu(
            step_id="reconfigure",
            menu_options=["reconfigure_host", "repair_credentials"],
        )

    async def async_step_reconfigure_host(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Allow the user to change the SHC host/IP without re-pairing."""
        entry = self._get_reconfigure_entry()
        errors = {}
        if user_input is not None:
            new_host = user_input[CONF_HOST]
            try:
                info = await self._get_info(new_host)
            except SHCConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except  # noqa: BLE001
                LOGGER.exception("Unexpected exception during reconfigure_host")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(info["unique_id"])
                self._abort_if_unique_id_mismatch(reason="wrong_shc")
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_HOST: new_host},
                )

        return self.async_show_form(
            step_id="reconfigure_host",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_HOST, default=entry.data.get(CONF_HOST, "")
                    ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
                }
            ),
            errors=errors,
        )

    async def async_step_repair_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Re-pair: regenerate the client certificate/key for this SHC entry."""
        entry = self._get_reconfigure_entry()
        errors = {}
        if user_input is not None:
            host = user_input[CONF_HOST]
            zeroconf_instance = await zeroconf.async_get_instance(self.hass)

            # mDNS-probe the target host's identity BEFORE registering new
            # credentials against it — unlike reconfigure_host, this step
            # previously wrote whatever host the user typed straight into the
            # entry with no check it's the same physical SHC (typo, DHCP
            # reassignment, second controller on the LAN would all silently
            # repoint an existing entry's cert/token). Kept in its own
            # try/except: _abort_if_unique_id_mismatch raises AbortFlow, which
            # must propagate to HA's flow manager, not be swallowed by the
            # broad `except Exception` in the registration try/except below.
            try:
                info = await self._get_info(host)
            except SHCConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except  # noqa: BLE001
                LOGGER.exception(
                    "Unexpected exception probing SHC identity for repair_credentials"
                )
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(info["unique_id"])
                self._abort_if_unique_id_mismatch(reason="wrong_shc")

            result = None
            if not errors:
                try:
                    result = await self.hass.async_add_executor_job(
                        create_credentials_and_validate,
                        self.hass,
                        host,
                        user_input,
                        zeroconf_instance,
                    )
                except SHCAuthenticationError:
                    errors["base"] = "invalid_auth"
                except SHCConnectionError:
                    errors["base"] = "cannot_connect"
                except SHCSessionError as err:
                    LOGGER.warning("Session error: %s", err.message)
                    errors["base"] = "session_error"
                except SHCRegistrationError as err:
                    LOGGER.warning("Registration error: %s", err.message)
                    errors["base"] = "pairing_failed"
                except Exception:  # pylint: disable=broad-except  # noqa: BLE001
                    LOGGER.exception("Unexpected exception during repair_credentials")
                    errors["base"] = "unknown"

            if not errors:
                if result is None:
                    errors["base"] = "pairing_failed"
                else:
                    hostname = result["token"].split(":", 1)[1]
                    new_entry_data = {
                        CONF_SSL_CERTIFICATE: self.hass.config.path(
                            DOMAIN, CONF_SHC_CERT + "_" + hostname + ".pem"
                        ),
                        CONF_SSL_KEY: self.hass.config.path(
                            DOMAIN, CONF_SHC_KEY + "_" + hostname + ".pem"
                        ),
                        CONF_HOST: host,
                        CONF_TOKEN: result["token"],
                        CONF_HOSTNAME: hostname,
                    }
                    return self.async_update_reload_and_abort(
                        entry,
                        data=new_entry_data,
                    )

        current_host = entry.data.get(CONF_HOST, "")
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=current_host): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.TEXT)
                ),
                vol.Required(CONF_PASSWORD): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
                vol.Optional(CONF_NAME, default="HomeAssistant"): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.TEXT)
                ),
            }
        )

        return self.async_show_form(
            step_id="repair_credentials",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors = {}
        if user_input is not None:
            host = user_input[CONF_HOST]
            try:
                self.info = info = await self._get_info(host)
            except SHCConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except  # noqa: BLE001
                LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(info["unique_id"])
                self._abort_if_unique_id_configured({CONF_HOST: host})
                self.host = host
                return await self.async_step_credentials()
        return self.async_show_form(
            step_id="user", data_schema=HOST_SCHEMA, errors=errors
        )

    async def async_step_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the credentials step."""
        errors = {}
        if user_input is not None:
            zeroconf_instance = await zeroconf.async_get_instance(self.hass)
            try:
                result = await self.hass.async_add_executor_job(
                    create_credentials_and_validate,
                    self.hass,
                    self.host,
                    user_input,
                    zeroconf_instance,
                )
            except SHCAuthenticationError:
                errors["base"] = "invalid_auth"
            except SHCConnectionError:
                errors["base"] = "cannot_connect"
            except SHCSessionError as err:
                LOGGER.warning("Session error: %s", err.message)
                errors["base"] = "session_error"
            except SHCRegistrationError as err:
                LOGGER.warning("Registration error: %s", err.message)
                errors["base"] = "pairing_failed"
            except Exception:  # pylint: disable=broad-except  # noqa: BLE001
                LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                if result is None:
                    errors["base"] = "pairing_failed"
                else:
                    hostname = result["token"].split(":", 1)[1]
                    entry_data = {
                        CONF_SSL_CERTIFICATE: self.hass.config.path(
                            DOMAIN, CONF_SHC_CERT + "_" + hostname + ".pem"
                        ),
                        CONF_SSL_KEY: self.hass.config.path(
                            DOMAIN, CONF_SHC_KEY + "_" + hostname + ".pem"
                        ),
                        CONF_HOST: self.host,
                        CONF_TOKEN: result["token"],
                        CONF_HOSTNAME: hostname,
                    }
                    _info = self.info
                    assert _info is not None
                    existing_entry = await self.async_set_unique_id(_info["unique_id"])
                    if existing_entry:
                        return self.async_update_reload_and_abort(
                            existing_entry,
                            data=entry_data,
                        )

                    return self.async_create_entry(
                        title=_info["title"],
                        data=entry_data,
                    )
        else:
            user_input = {}

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_PASSWORD, default=user_input.get(CONF_PASSWORD, "")
                ): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
                vol.Optional(
                    CONF_NAME, default=user_input.get(CONF_NAME, "HomeAssistant")
                ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
            }
        )

        return self.async_show_form(
            step_id="credentials", data_schema=schema, errors=errors
        )

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> FlowResult:
        """Handle zeroconf discovery."""
        if not discovery_info.name.startswith("Bosch SHC"):
            return self.async_abort(reason="not_bosch_shc")

        try:
            self.info = await self._get_info(discovery_info.host)
        except SHCConnectionError:
            return self.async_abort(reason="cannot_connect")
        self.host = discovery_info.host

        local_name = discovery_info.hostname[:-1]
        node_name = local_name[: -len(".local")]

        _info = self.info
        assert _info is not None
        await self.async_set_unique_id(_info["unique_id"])
        self._abort_if_unique_id_configured({CONF_HOST: self.host})
        self.context["title_placeholders"] = {"name": node_name}
        return await self.async_step_confirm_discovery()

    async def async_step_confirm_discovery(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle discovery confirm."""
        errors: dict[str, str] = {}
        if user_input is not None:
            return await self.async_step_credentials()

        return self.async_show_form(
            step_id="confirm_discovery",
            description_placeholders={
                "model": "Bosch SHC",
                "host": self.host,
            },
            errors=errors,
        )

    async def _get_info(self, host: str) -> dict[str, Any]:
        """Get additional information."""
        zeroconf_instance = await zeroconf.async_get_instance(self.hass)

        return await self.hass.async_add_executor_job(  # type: ignore[no-any-return]
            get_info_from_host,
            self.hass,
            host,
            zeroconf_instance,
        )


class OptionsFlowHandler(config_entries.OptionsFlowWithReload):  # type: ignore[misc]
    """Handle options flow for Bosch SHC."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        current = self.config_entry.options

        if user_input is not None:
            # HA's section() nests fields; flatten back to the flat OPT_* shape
            # that sensor.py, __init__.py, etc. read.
            flat = _flatten_sections(user_input)
            # async_create_entry REPLACES the stored options wholesale.  Some
            # device-list fields are only shown when the live session yields
            # candidates (eligible light relays / devices / rooms); when hidden
            # they are absent from the submit dict, so carry over the stored
            # value instead of silently wiping the user's selection.
            for key in (
                OPT_LIGHTS_AS_LIGHT,
                OPT_ALL_LIGHTS_AS_LIGHT,
                OPT_SUPPRESS_HUE_LIGHTS,
                OPT_SUPPRESS_LEDVANCE_LIGHTS,
                OPT_SUPPRESS_MOTION_INDICATOR_LIGHT,
                OPT_SUPPRESS_CAMERA_SWITCHES,
                OPT_SCENARIOS_FILTER,
                OPT_EXCLUDED_DEVICES,
                OPT_EXCLUDED_ROOMS,
            ):
                if key not in flat and key in current:
                    flat[key] = current[key]
            return self.async_create_entry(title="", data=flat)

        # The presence entity option became multi-select; existing entries may
        # still hold a single entity id as a plain string. Coerce to a list so
        # the multiple=True EntitySelector never receives a string (which makes
        # the frontend ha-entities-picker crash with "t.map is not a function").
        _presence_default = current.get(OPT_PRESENCE_ENTITY, [])
        if isinstance(_presence_default, str):
            _presence_default = [_presence_default] if _presence_default else []

        # Build device/room option lists from the live session.
        device_options = []
        room_options = []
        light_switch_options = []
        _has_cameras = False
        _camera_tool_installed = False
        _has_hue_lights = False
        _has_ledvance_lights = False
        _has_md2 = False
        _scenario_options = []
        try:
            _camera_tool_installed = bool(
                self.hass.config_entries.async_entries(CAMERA_TOOL_DOMAIN)
            )
            if hasattr(self.config_entry, "runtime_data"):
                session = self.config_entry.runtime_data.session
                _has_cameras = bool(
                    session.device_helper.camera_eyes
                    or session.device_helper.camera_360
                    or session.device_helper.camera_outdoor_gen2
                )
                rooms = {r.id: r.name for r in session.rooms}
                for dev in session.devices:
                    room_name = rooms.get(getattr(dev, "room_id", None), "")
                    label = f"{dev.name} ({room_name})" if room_name else dev.name
                    device_options.append({"value": dev.id, "label": label})
                room_options = [
                    {"value": rid, "label": name} for rid, name in rooms.items()
                ]
                _has_hue_lights = bool(session.device_helper.hue_lights)
                _has_ledvance_lights = bool(
                    getattr(session.device_helper, "ledvance_lights", [])
                )
                _has_md2 = bool(getattr(session.device_helper, "motion_detectors2", []))
                _scenario_options = [
                    {"value": s.id, "label": s.name}
                    for s in session.scenarios
                    if getattr(s, "id", None) and getattr(s, "name", None)
                ]
                # #338: only the on/off light-relay devices are eligible to be
                # presented as a `light`.  Append a friendly model name so a BSM
                # relay is distinguishable from a Light Control II channel,
                # without the confusing raw "MICROMODULE_*" string.
                for dev in light_switch_devices(session):
                    room_name = rooms.get(getattr(dev, "room_id", None), "")
                    friendly = light_relay_friendly_model(dev)
                    base = f"{dev.name} ({room_name})" if room_name else dev.name
                    label = f"{base} – {friendly}" if friendly else base
                    light_switch_options.append({"value": dev.id, "label": label})
        except (
            Exception  # noqa: BLE001 — never break options flow on session error
        ):
            LOGGER.debug("Could not build device/room filter options", exc_info=True)

        features_fields = {
            vol.Optional(
                OPT_SCENARIOS_AS_BUTTONS,
                default=current.get(OPT_SCENARIOS_AS_BUTTONS, False),
            ): BooleanSelector(),
            vol.Optional(
                OPT_DIAGNOSTIC_ENTITIES,
                default=current.get(OPT_DIAGNOSTIC_ENTITIES, True),
            ): BooleanSelector(),
            vol.Optional(
                OPT_ENABLE_RAWSCAN,
                default=current.get(OPT_ENABLE_RAWSCAN, True),
            ): BooleanSelector(),
        }
        # Power & energy sensors: always offer (many users want to suppress).
        features_fields[
            vol.Optional(
                OPT_SUPPRESS_POWER_SENSORS,
                default=current.get(OPT_SUPPRESS_POWER_SENSORS, False),
            )
        ] = BooleanSelector()

        # #244: per-room "all lights" master control. Always offered (like
        # scenarios_as_buttons/diagnostic_entities above) — a no-op if no room
        # has 2+ eligible lights, same as those unconditional toggles.
        features_fields[
            vol.Optional(
                OPT_ROOM_LIGHT_GROUPS,
                default=current.get(OPT_ROOM_LIGHT_GROUPS, False),
            )
        ] = BooleanSelector()

        # Scenario filter: only when there are scenarios to choose from.
        if _scenario_options:
            _valid_scenario_ids = {opt["value"] for opt in _scenario_options}
            _filter_default = [
                sid
                for sid in current.get(OPT_SCENARIOS_FILTER, [])
                if sid in _valid_scenario_ids
            ]
            features_fields[
                vol.Optional(
                    OPT_SCENARIOS_FILTER,
                    default=_filter_default,
                )
            ] = SelectSelector(
                SelectSelectorConfig(
                    options=_scenario_options,
                    multiple=True,
                    mode=SelectSelectorMode.DROPDOWN,
                    custom_value=False,
                    sort=True,
                )
            )

        # #344: only offer the Hue suppression toggle when the controller has
        # Hue lights (avoids a confusing option for users without any).
        if _has_hue_lights:
            features_fields[
                vol.Optional(
                    OPT_SUPPRESS_HUE_LIGHTS,
                    default=current.get(OPT_SUPPRESS_HUE_LIGHTS, False),
                )
            ] = BooleanSelector()

        # #338: only offer the "expose as light" controls when the controller
        # actually has light-relay devices that can switch domain.
        if light_switch_options:
            # A single toggle to convert ALL of them at once (overrides the
            # per-device picker below).
            features_fields[
                vol.Optional(
                    OPT_ALL_LIGHTS_AS_LIGHT,
                    default=current.get(OPT_ALL_LIGHTS_AS_LIGHT, False),
                )
            ] = BooleanSelector()
            features_fields[
                vol.Optional(
                    OPT_LIGHTS_AS_LIGHT,
                    default=current.get(OPT_LIGHTS_AS_LIGHT, []),
                )
            ] = SelectSelector(
                SelectSelectorConfig(
                    options=light_switch_options,
                    multiple=True,
                    mode=SelectSelectorMode.DROPDOWN,
                    custom_value=False,
                    sort=True,
                )
            )

        if _has_ledvance_lights:
            features_fields[
                vol.Optional(
                    OPT_SUPPRESS_LEDVANCE_LIGHTS,
                    default=current.get(OPT_SUPPRESS_LEDVANCE_LIGHTS, False),
                )
            ] = BooleanSelector()

        if _has_md2:
            features_fields[
                vol.Optional(
                    OPT_SUPPRESS_MOTION_INDICATOR_LIGHT,
                    default=current.get(OPT_SUPPRESS_MOTION_INDICATOR_LIGHT, False),
                )
            ] = BooleanSelector()

        if _has_cameras:
            features_fields[
                vol.Optional(
                    OPT_SUPPRESS_CAMERA_SWITCHES,
                    default=current.get(OPT_SUPPRESS_CAMERA_SWITCHES, False),
                )
            ] = BooleanSelector()

        schema = vol.Schema(
            {
                vol.Required("features"): section(
                    vol.Schema(features_fields),
                    {"collapsed": False},
                ),
                vol.Required("presence"): section(
                    vol.Schema(
                        {
                            vol.Optional(
                                OPT_CHILD_LOCK_ENABLED,
                                default=current.get(
                                    OPT_CHILD_LOCK_ENABLED, bool(_presence_default)
                                ),
                            ): BooleanSelector(),
                            vol.Optional(
                                OPT_PRESENCE_ENTITY,
                                default=_presence_default,
                            ): EntitySelector(
                                EntitySelectorConfig(
                                    multiple=True,
                                    domain=[
                                        "person",
                                        "device_tracker",
                                        "binary_sensor",
                                        "input_boolean",
                                        "zone",
                                        "group",
                                    ],
                                )
                            ),
                            vol.Optional(
                                OPT_SILENT_MODE_ENABLED,
                                default=current.get(OPT_SILENT_MODE_ENABLED, False),
                            ): BooleanSelector(),
                            vol.Optional(
                                OPT_SILENT_MODE_START,
                                default=current.get(OPT_SILENT_MODE_START, "22:00:00"),
                            ): TimeSelector(),
                            vol.Optional(
                                OPT_SILENT_MODE_END,
                                default=current.get(OPT_SILENT_MODE_END, "06:00:00"),
                            ): TimeSelector(),
                        }
                    ),
                    {"collapsed": False},
                ),
                vol.Required("advanced"): section(
                    vol.Schema(
                        {
                            vol.Optional(
                                OPT_SSL_VERIFY_HOSTNAME,
                                default=current.get(OPT_SSL_VERIFY_HOSTNAME, False),
                            ): BooleanSelector(),
                            vol.Optional(
                                OPT_SSL_SKIP_VERIFY,
                                default=current.get(OPT_SSL_SKIP_VERIFY, False),
                            ): BooleanSelector(),
                            vol.Optional(
                                OPT_LONG_POLL_TIMEOUT,
                                default=current.get(OPT_LONG_POLL_TIMEOUT, 10),
                            ): NumberSelector(
                                NumberSelectorConfig(
                                    min=5,
                                    max=60,
                                    step=1,
                                    unit_of_measurement="s",
                                    mode=NumberSelectorMode.BOX,
                                )
                            ),
                            vol.Optional(
                                OPT_EXCLUDED_DEVICES,
                                default=current.get(OPT_EXCLUDED_DEVICES, []),
                            ): (
                                SelectSelector(
                                    SelectSelectorConfig(
                                        options=device_options,
                                        multiple=True,
                                        mode=SelectSelectorMode.DROPDOWN,
                                        custom_value=False,
                                        sort=True,
                                    )
                                )
                                if device_options
                                else vol.Schema(vol.All(list, [str]))
                            ),
                            vol.Optional(
                                OPT_EXCLUDED_ROOMS,
                                default=current.get(OPT_EXCLUDED_ROOMS, []),
                            ): (
                                SelectSelector(
                                    SelectSelectorConfig(
                                        options=room_options,
                                        multiple=True,
                                        mode=SelectSelectorMode.DROPDOWN,
                                        custom_value=False,
                                        sort=True,
                                    )
                                )
                                if room_options
                                else vol.Schema(vol.All(list, [str]))
                            ),
                        }
                    ),
                    {"collapsed": False},
                ),
            }
        )
        camera_note = (
            f"\n\n💡 You have Bosch cameras connected — for advanced camera "
            f"features beyond this integration, see the dedicated Camera Tool: "
            f"{CAMERA_TOOL_URL}"
            if _has_cameras and not _camera_tool_installed
            else ""
        )
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            description_placeholders={"camera_tool": camera_note},
        )
