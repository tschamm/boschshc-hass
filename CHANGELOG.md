# Changelog

## 0.12.5 — firmware update entity now shows an actual progress indicator (#373)

**No breaking changes.**

- **Fix: the per-device firmware update entity's "in progress" state was
  silently ignored by the frontend.** `DeviceUpdate`/`ControllerUpdate`
  declared `in_progress` but never the `UpdateEntityFeature.PROGRESS`
  flag it requires to have any effect — confirmed against HA core's own
  `UpdateEntity` (`in_progress`/`update_percentage` are no-ops without it).
  Both entities now declare `PROGRESS`; no numeric percentage is reported
  (`update_percentage` stays `None`), since neither the local SHC API nor
  the official Bosch app itself expose one — APK decompile of the app's own
  `FirmwarePresenter`/`FirmwareView` confirmed it only ever shows a plain
  status label, never a progress bar, matching what #373's reporter saw.

## 0.12.4 — fix confusing raw 409 on firmware update.install (#373)

**No breaking changes.**

- **Fix: firmware `update.install` fails with a confusing raw HTTP 409** on
  per-device update entities (`DeviceUpdate`, e.g. Radiator Thermostat II)
  (#373). Root cause: `latest_version` shows "update available" for *any*
  non-up-to-date firmware lifecycle state, but the live-confirmed
  `PUT .../activate` call is only actually valid from the `AwaitingActivation`
  state — every other pending state (`UpdateAvailable` = known but not yet
  transferred to the device, `Failed`, `AwaitingActivationTimeout`,
  `AwaitingUserInteraction` = needs physical confirmation on the device
  itself, `UpdatePending`/`UpdateRunning` = already activating) legitimately
  409s if activated (again). `async_install` now checks the currently-probed
  state first and refuses locally with a clear, translated message naming
  the actual blocking state, instead of hitting the SHC and surfacing a raw
  409. New `update_not_ready` translation key, all 29 languages.

## 0.12.2 — fix Room Climate Control devices losing their room name (#372)

**No breaking changes.**

- **Fix: every `ROOM_CLIMATE_CONTROL` device lost its room name** (showing
  the literal placeholder `-RoomClimateControl-` instead, e.g. "Büro",
  "Wohnzimmer", ...) (#372). Root cause: the virtual per-room
  `ROOM_CLIMATE_CONTROL` device's own raw name from the SHC really is the
  generic string `-RoomClimateControl-` — the `climate` entity has always
  resolved the real room name itself and set it explicitly, but several
  *other* entity types added on top of the same device across recent
  releases (`CallForHeatSensor`, `ScheduleOverrideActiveSensor`,
  `NextSetpointTemperatureSensor`, and this release's new temperature-drop
  switch/number) never did the same — whichever platform's device-registry
  write landed last silently won and overwrote the room name back to the
  placeholder. All four now resolve and report the same room name as the
  `climate` entity, so the device's display name stays correct regardless
  of entity/platform setup order. **Confirmed live** — reproduced and fixed
  against a real installation.
- Bumps the `boschshcpy` pin to 0.6.1 (Multiroom Boiler Control — lib-only,
  no owned hardware to design/live-test HA entities against yet; open-doors/
  open-windows summary; several smaller official-spec gaps closed — see
  `boschshcpy`'s own CHANGELOG for the full breakdown).
- **New: whole-home "Open Doors/Windows" sensor** — a single always-on
  sensor showing the total count of currently-open doors/windows, with the
  individual open item names as attributes. **Live-confirmed** against a
  real SHC. `should_poll=True`, matching the recently-fixed polling pattern.

## 0.12.1 — pin boschshcpy 0.5.1 (water-alarm mute bugfix)

**No breaking changes.**

- Bumps the `boschshcpy` pin to 0.5.1, which fixes two real bugs in the
  whole-home water-leak alarm domain added in 0.12.0/0.5.0: the
  `AlarmState` enum used the wrong value (`ALARM_ON` instead of the
  spec's `WATER_ALARM`, meaning a real alarm would have shown as
  `UNKNOWN`), and `mute()` used the wrong HTTP method (`PUT` instead of
  the spec's `POST`, meaning the mute button would have failed outright).
  Both were found by cross-checking the official OpenAPI spec — see
  `boschshcpy`'s own CHANGELOG for detail. No code changes needed on this
  side; the water-alarm mute button (`button.py`) only calls
  `async_mute()`, it never touched the broken enum directly.

## 0.12.0 — big sync with the official Bosch Smart Home app

A large round of reverse-engineering (APK decompile + live traffic capture
against a real SHC) closing the gap between this integration and what the
official Bosch app can do — many new entities, all built on the matching
`boschshcpy` 0.5.0 release. Everything marked **live-confirmed** was
verified against a real controller/real device, not implemented from the
OpenAPI spec/decompile alone.

- **★ Firmware updates, end to end — the headline feature of this release.**
  The controller's `update.*` entity now has `INSTALL` wired up, and every
  device whose model has a firmware UI in the Bosch app (TRV_GEN2/
  TRV_GEN2_DUAL, MD2, SMOKE_DETECTOR2, TWINGUARD, OUTDOOR_SIREN,
  MICROMODULE_LIGHT_CONTROL, MICROMODULE_BLINDS/SHUTTER/AWNING,
  PLUG_COMPACT_DUAL) now gets its own firmware-status `update` entity —
  so a pending update shows up as a normal HA "Update available"
  notification, with an Install button, instead of requiring the Bosch app.
  Not in the official OpenAPI spec — traced via APK decompile
  (`FirmwarePresenter`/`FirmwareStateLoader`,
  `RestRequests.getDeviceFirmwareState`/`putDeviceFirmwareActivation`); an
  earlier attempt this same development round gated entity creation on a
  per-device `SoftwareUpdate` service that turned out to be a wrong guess
  (no real device ever advertises it) — replaced with a device-agnostic
  probe. **Confirmed live end to end**, including the actual install: a
  TRV_GEN2 radiator thermostat's pending update was triggered from this
  integration's own Install button and moved through
  `AwaitingActivation` → `UpdatePending` → `UpToDateAwaitingUserInteraction`
  over ~90 seconds — a genuine, successful over-the-air firmware install,
  the device stayed fully functional throughout.
- **New: automation-rule entities** (opt-in, `automation_rules_as_entities`
  option) — one switch (enable/disable) + one button (trigger now) per
  Bosch-app-native automation rule, **live-confirmed** against a real SHC
  with 23 real user-configured rules.
- **New: intrusion-alarm and water-alarm "Mute" buttons** — closes a real
  gap: the Bosch app's in-alarm "Mute" action had no equivalent in this
  integration before. Always-on when the corresponding alarm system is
  present, **live-confirmed**.
- **New: temperature-drop controls** — a switch (enable/disable) + number
  (drop value in °C) per room with the anti-frost/window-open compensation
  service, mirroring the Bosch app's room-detail screen. **Live-confirmed**
  across 12 real rooms.
- **New: thermostat regulation-algorithm select** — lets you switch a
  thermostat between "Internal" and "Custom" regulation, mirroring the
  Bosch app. Probed per-device (not created on devices that don't support
  it); **live-confirmed** absence-handling against several real HomeMaticIP
  room-thermostats and a TRV_GEN2 valve, none of which expose this
  capability on this installation.
- Hardening found via an internal bug-hunt pass on the above: a
  long-standing `SHCEntity.should_poll` override was silently defeating
  `_attr_should_poll = True` on any subclass (affected the 4 new
  poll-based entities above plus the existing firmware-update entity) —
  fixed, and **live-verified** via a real-time log monitor to confirm
  genuinely periodic (~30s) polling across all affected entities, not a
  one-shot update at setup.

## 0.11.2 — fix stale device availability after an SHC firmware update (hass#370)

**No breaking changes.**

Bumped `boschshcpy` pin to **0.4.14**. After a long-poll poll-id resubscribe
(a ~24h cycle, or any connection gap long enough to invalidate the poll id —
e.g. an SHC firmware update/reboot), the library's refresh only short-polled
each device's *services*, never re-fetching the device's own top-level
`status`. A device that went `UNDEFINED` during the gap and later
reconnected could keep reporting stale availability indefinitely — showing
as a confident "closed"/"off" instead of "unavailable" right after an SHC
firmware update, which could mislead automations. Fixed at the library
level (`boschshcpy` 0.4.14); this release just picks up the new pin.

## 0.11.1 — climate auto-mode temperature fix, Zigbee mesh-view rework

**No breaking changes.**

- **`climate.py`:** `climate.set_temperature(temperature=X, hvac_mode="auto")`
  on a RoomClimateControl already in `AUTOMATIC` no longer silently drops the
  temperature change (#369). A 0.7.26 guard assumed the SHC always rejects a
  setpoint write while `operationMode=AUTOMATIC`; a reporter's before/after
  rawscan of the official app doing exactly this showed `setpointTemperature`
  written directly with `operationMode` staying `AUTOMATIC` — the schedule
  resumes on its own via the existing `nextChange` fields. The separate
  bare-call (no `hvac_mode` given) switch-to-`MANUAL`-first behavior (#180) is
  unchanged.
- **`zigbee_topology.py` (mesh view):** the topology graph now uses every hop
  in each device's full route, not just its own first hop, so a router that
  doesn't answer its own routing-info query (excluded, offline, never polled)
  still shows up connected if some other device's longer route passes through
  it. Visual refresh: fixed status palette (validated for contrast on light
  and dark), automatic dark mode, rounded label chips, native hover tooltips.

## 0.11.0 — mypy strict-typing cleanup, EntityDescription core-prep, test-fixture consolidation

**No breaking changes — internal refactor only, no entity/behavior changes.**

- Bumped `boschshcpy` pin to **0.4.13** — long-poll message-shape guards
  found via a chaos-engineering test round (`session.py`/`device_service.py`,
  no live incident, no HA-visible behavior change).
- **`__init__.py`:** dropped an unnecessary defensive `getattr(runtime.session,
  "devices", None) or []` in the Zigbee topology export service —
  `SHCData.session.devices` is a non-Optional, always-present property, so
  the fallback only masked a would-be-loud `AttributeError`.
- **`button.py`:** `SHCEnableAllDiagnosticsButton` (new in 0.10.15) now
  prefers the config entry's `unique_id` over its `entry_id` for its own
  `unique_id`, matching `SHCScenarioButton`'s existing convention; and
  guards `async_press` against an overlapping config-entry reload when the
  button is pressed twice in quick succession.
- **mypy strict-typing cleanup (the main change in this release):**
  `mypy.ini`'s `disable_error_code` line (which had masked 291 real errors)
  was removed entirely after fixing every one of them. Two recurring fix
  shapes account for nearly all of them: a local `self._device: <ConcreteType>`
  narrowing in `__init__` for classes extending `SHCEntity` directly, and a
  PEP-695 generic `EntityDescription` (`class SHCXEntityDescription[_DeviceT:
  SHCDevice](XEntityDescription)`) for platforms with many near-identical
  device-specific classes. `mypy custom_components/bosch_shc/` (CI's exact
  gate command) is now genuinely clean with no suppressions.
- **EntityDescription-dataclass refactor** (the ha-core Platinum-tier
  convention, prepping this codebase for an easier future upstream port):
  applied to `switch.py`, `sensor.py` (~27 classes → one generic driver),
  `select.py`, `binary_sensor.py`, and `number.py`. `climate.py`/`cover.py`/
  `light.py`/`button.py`/`update.py`/`event.py` were checked and deliberately
  left as direct classes — each is genuinely device-distinct or too small a
  set to benefit from the pattern.
- **Test-fixture consolidation:** introduced shared `mock_config_entry`/
  `device_buckets`/`mock_session`/`run_setup_entry` fixtures in
  `tests/bosch_shc/conftest.py`, replacing bespoke duplicated per-file mock
  helpers across 13 test files.
- `scripts/comment_length_baseline.txt` regenerated — the refactor shifted
  line numbers throughout and carried forward existing per-device hardware/
  API documentation comments into the new `EntityDescription` entries; same
  content, no new prose.

## 0.10.15 — Zigbee topology export, bulk-diagnostics button, ShutterContactSensor refactor

**No breaking changes.**

- **New service `bosch_shc.export_zigbee_topology`:** builds a Zigbee mesh
  topology graph from the last routing poll (`SHCZigbeeRoutingCoordinator`,
  already polling every 5 minutes) — per-hop link quality
  (good/medium/bad/no_connection/...) stitched from each device's own
  reported hop chain back to the controller. Returns the graph as JSON and
  as Mermaid diagram text in the service response, and additionally writes
  a JSON file + a self-contained, offline-viewable HTML/SVG page under
  `www/bosch_shc/<slug>_<entry_id>_zigbee_topology.html` (no external JS/CDN,
  no new dependency). Prompted by a routing-quality complaint in the
  community forum — there was previously no way to see *which* device is
  routing through *which* other device, only an aggregate per-device
  quality enum. Note the SHC's API only ever reports each device's own path
  back to the controller (no neighbor/routing table like Zigbee2MQTT/ZHA
  get via a coordinator-side Mgmt_Lqi_req scan), so this is a tree, not a
  full mesh graph with cross-links — and quality is categorical, not a
  numeric LQI/RSSI.
- **New button "Enable All Diagnostics"** (one per SHC controller, always
  created): bulk-enables every disabled-by-default diagnostic entity
  (Zigbee routing quality, communication quality, etc.) for that entry in
  one click, instead of opening each one individually in
  Settings > Devices & Services > Entities. Only touches entities HA itself
  disabled by default (`disabled_by: integration`) — an entity a user
  explicitly disabled is left alone. Triggers a config-entry reload so the
  newly-enabled entities actually start.
- **`binary_sensor.py`:** refactored `ShutterContactSensor` to the
  entity-description pattern (`SHCShutterContactSensorEntityDescription`
  with an `is_on_fn` callable), ported from the equivalent home-assistant/core
  refactor to keep the HACS fork and ha-core's `bosch_shc` in sync. Pure
  clarity refactor — behavior-preserving, `BatterySensor` and everything
  else in the file untouched.
- **`manifest.json`:** added `@mosandlt` to `codeowners`, mirroring
  home-assistant/core PR #174563 (merged) — an audit of every merged
  ha-core `bosch_shc` PR found this was the only gap; the other merged PR
  (#174550, `boschshcpy` pin bump to 0.3.5) is a no-op here since this fork
  is already far ahead on `0.4.12`.

## 0.10.14 — device_trigger.py refactor, session.py thread-safety fix

**No breaking changes.** Requires `boschshcpy==0.4.12`.

- **`device_trigger.py`:** refactored `async_get_triggers` to a table-driven
  `DEVICE_TRIGGER_TABLE` (`dev_type -> (CONF_TYPE, subtypes)`) for MD/MD2/SD/
  SMOKE_DETECTOR2/SMOKE_DETECTION_SYSTEM, replacing five near-identical
  dict-literal-construction blocks with one generic loop. Pure clarity
  refactor — behavior-preserving (verified against every existing test),
  WRC2/SWITCH2 and the SHC scenario-trigger block deliberately left as-is
  (different shape, don't fit the table).
- **`boschshcpy` 0.4.12:** fixed a thread-safety race in `session.py`
  between the polling thread and cross-thread readers of the device list
  (`RuntimeError: dictionary changed size during iteration`) — see that
  project's own changelog. Live-tested on production hardware before this
  release (unreleased lib code deployed directly, HA restarted, long-poll
  stream verified error-free) prior to being published to PyPI.

## 0.10.13 — bug-hunt round: bypass_infinite naming, SD II device triggers

**No breaking changes.** Requires `boschshcpy==0.4.11`.

Findings from a broad bug-hunt round across the integration and the
`boschshcpy` library it depends on:

- **`switch.py`:** the `bypass_infinite` switch (Shutter Contact II) never
  showed its translated name ("Bypass Never Expires") — it displayed the raw
  internal `attr_name` ("BypassInfinite") instead. The translation-key guard
  added for `bypass` (#342) only applied when the switch had no `attr_name`
  disambiguator; `bypass_infinite` has both a `translation_key` and an
  `attr_name` (needed to distinguish its unique_id from the sibling `bypass`
  switch on the same device), so it fell through the guard. Fixed so the
  translation applies whenever a `translation_key` is present, independent
  of `attr_name`.
- **`device_trigger.py`:** Smoke Detector II's device-trigger subtype list
  in the Automations UI reused gen-1 Smoke Detector's subtypes
  (`INTRUSION_ALARM`/`SECONDARY_ALARM`/`PRIMARY_ALARM`), but SD II's
  `AlarmService.State` actually reports
  `INTRUSION_ALARM_ON_REQUESTED`/`INTRUSION_ALARM_OFF_REQUESTED` — the real
  "alarm triggered" subtype was never selectable from the UI trigger picker
  for SD II owners (hand-written YAML using the correct string still
  worked). New `ALARM_EVENTS_SUBTYPES_SD2` constant, translated to all 30
  languages.
- **`boschshcpy` 0.4.11:** `ChildProtectionService.childLockActive` crash on
  a partial poll snapshot omitting the field, and an async request timeout
  not wrapping into `SHCConnectionError` — see that project's own changelog.

## 0.10.12 — fix stuck setup from 0.10.11's Zigbee routing coordinator (#362)

**No breaking changes.** No `boschshcpy` pin change.

- **`__init__.py`:** 0.10.11 introduced `SHCZigbeeRoutingCoordinator` and
  awaited its `async_config_entry_first_refresh()` unconditionally during
  setup. That method raises `ConfigEntryNotReady` on any failure of the
  coordinator's update — so a Zigbee-routing fetch hiccup (unreachable SHC,
  unsupported firmware endpoint, timeout) failed the *entire* integration
  setup, even though the coordinator only backs one diagnostic sensor that
  is disabled by default. Reported as the integration getting stuck
  flapping between "setup error, retrying" and "initializing". Switched to
  `async_refresh()`, which never raises: a failed first fetch just leaves
  the coordinator's `last_update_success` false and the sensor unavailable
  until its next 5-minute poll succeeds, without blocking anything else.
- **`manifest.json`:** the 0.10.11 release commit bumped the `boschshcpy`
  requirements pin but left the integration's own `"version"` field at
  `0.10.10` — exactly matching the report that the Integrations page showed
  "Version 0.10.10" after updating to 0.10.11. Fixed.

## 0.10.11 — Zigbee routing-quality diagnostic sensor

**No breaking changes.** Requires `boschshcpy==0.4.10`.

- **`sensor.py`:** new opt-in-by-default-off diagnostic `ZigbeeRoutingQuality`
  sensor, one per device whose id starts with `hdm:ZigBee:` (ENUM: good /
  medium / bad / no_connection / device_not_initialized / not_supported /
  unknown), with the resolved hop-by-hop route as a state attribute. Requires
  `boschshcpy` `SHCSessionAsync.get_zigbee_routing_info` — gated behind
  `diagnostic_entities` like the other diagnostic sensors in this file.
  Unlike almost everything else in this push-based integration, this data is
  not delivered by the long-poll stream at all, so it's backed by a new
  `SHCZigbeeRoutingCoordinator` (`coordinator.py`) — HA's documented
  `DataUpdateCoordinator` pattern for polled data — created once in
  `__init__.py` and shared across every Zigbee device's sensor, polling every
  5 minutes, fetching all devices concurrently rather than serially so a
  large Zigbee mesh doesn't delay integration setup. A single device's
  fetch failure doesn't fail the whole refresh: it's simply omitted from
  that cycle's data and the corresponding sensor reports unavailable,
  without affecting any other Zigbee device's sensor. Translated to all 30
  languages.

## 0.10.10 — light/cover error handling, event unsubscribe, number JSON-decode guard

**No breaking changes.** Requires `boschshcpy==0.4.9`.

Findings from a full code review, fixed in three passes:

- **`light.py`/`cover.py`:** `LightSwitch`, `MotionDetectorLight`, `RelayLight`
  (turn on/off) and all four `cover.py` write actions (open/close/set
  position/stop, including tilt) now catch `SHCException` and raise a
  translated `HomeAssistantError`, matching the pattern every other
  write-capable platform already had since 0.10.6. `cover.py`'s optimistic
  state (`is_opening`/`target_position`) is now only set after the device
  write succeeds, so a failed write no longer leaves the UI stuck showing a
  state change that never happened.
- **`cover.py`:** `ShutterControlCover.current_cover_position` no longer
  reports a stale HA-side target position while a Shutter-II device is being
  moved from the Bosch app or a physical switch — it now uses the live
  device-reported position whenever `operation_state` is `OPENING`/`CLOSING`.
- **`event.py`:** `UniversalSwitchEvent`, `LightControlButtonEvent`,
  `SHCScenarioEvent`, `MotionDetectorEvent`, `SmokeDetectionSystemEvent`, and
  `SmokeDetectorEvent` now unregister their callbacks on entity removal
  (`async_will_remove_from_hass`) — previously left subscribed indefinitely.
- **`number.py`:** all setters now catch `json.JSONDecodeError` from a
  malformed-but-200-OK write response, matching `DimmerConfigNumber`'s
  existing handling; the remaining `except (SHCException, SHCConnectionError)`
  tuples across `select.py`/`number.py`/`switch.py`/`binary_sensor.py`/
  `alarm_control_panel.py`/`__init__.py` are simplified to `except
  SHCException` (boschshcpy 0.4.9 made `SHCConnectionError` a subclass, see
  0.10.9's changelog entry).
- Two copy-paste doc fixes (`logbook.py`, `alarm_control_panel.py`) and a
  round of comment condensing flagged by the new comment-length CI gate.
- **`binary_sensor.py`/`cover.py`/`light.py`:** `*Service.State` enum
  comparisons switched from `==`/`!=` to `is`/`is not` — matches the
  identity-comparison convention `ha-core`'s custom mypy plugin enforces on
  these same platforms there, ahead of eventually migrating them.
- **`binary_sensor.py`/`sensor.py`:** `MotionDetectionSensor`,
  `OccupancyDetectionSensor`, `TamperSensor`, and
  `NextSetpointTemperatureSensor` now declare `_unrecorded_attributes` for
  their timestamp-valued `extra_state_attributes` (`last_motion_detected`,
  `last_occupancy_change`, `last_tamper_time`, `next_change_at`) — previously
  every state write added a new recorder DB row even when nothing
  user-visible changed, since each of those values is unique per event.

## 0.10.9 — boschshcpy 0.4.9, simplified button error handling

**No breaking changes.** Requires `boschshcpy==0.4.9`.

boschshcpy 0.4.9 makes `SHCConnectionError` a subclass of `SHCException` and
consistently wraps `requests` transport errors into it across all read/write
API calls (previously only some paths wrapped some transport errors — see
home-assistant/core#174613's review for the motivating discussion). Every
`button.py` entity's `except (SHCException, SHCConnectionError)` simplified
to `except SHCException` accordingly — no functional change, the exception
hierarchy is just unified now.

`SHCScenarioButton` now uses `_attr_translation_key = "scenario"` +
an `icons.json` entry instead of a hardcoded `_attr_icon`, matching every
other button entity in this file.

## 0.10.8 — device-inventory audit: bypass, energy reset, presence simulation, shutter diagnostics

**No breaking changes.** Requires `boschshcpy>=0.4.8`. New read-only sensors
and action entities across several device families, all found by an
APK-decompile audit of Thomas's real device inventory (thermostats, contacts,
power/energy, shutters) and confirmed genuinely reachable in the official
Bosch Android app before implementation.

**New entities:**
- Shutter contacts with Bypass support: `switch` "Bypass Never Expires" and
  `number` "Bypass Timeout" (1–15 minutes, corrected from a previous
  seconds/minutes mix-up — no OpenAPI spec exists for Bypass, confirmed via
  decompiled layout XML).
- Smart plugs (incl. compact): `button` "Reset Energy Counter" —
  `resetEnergySummation`.
- Presence simulation: `sensor` "Simulation Running Since"/"Simulation
  Running Until" (diagnostic).
- Room climate control: `binary_sensor` "Schedule Override Active" and
  `sensor` "Next Setpoint Temperature" (diagnostic, with next-change-time and
  next-operation-mode as attributes).
- Shutter Control II (BBL, micromodule shutter controls, micromodule
  blinds): `binary_sensor` "Calibration Required" (diagnostic), `sensor`
  "Reference Moving Time (Top to Bottom)"/"(Bottom to Top)" (diagnostic), and
  `button` "Recalibrate" — `resetCalibrationAndOpen`.
- `HeatingCircuit`'s setpoint slider min/max are now read dynamically from
  the device's own reported range instead of a hardcoded 5–30 °C, matching
  the real app's behavior; falls back to 5–30 °C on devices that don't report
  a range.

All new entity names translated to all 29 non-English languages.

## 0.10.7 — per-room light groups

**No breaking changes.** New opt-in feature: per-room light groups (#244).

A new options-flow toggle, "Enable per-room light groups" (default **off**),
creates one aggregate `light` entity per SHC room that has 2 or more
dimmable/color lights (LEDVANCE, Hue, Light/Shutter Control II dimmers),
letting you turn all of a room's lights on/off from a single entity —
mirroring the room-level control heating already gets "for free" via
`ROOM_CLIMATE_CONTROL`. On/off only, no brightness/colour aggregation.
Rooms with fewer than 2 eligible lights, or with the option off, get no
group entity (and any previously-created one is cleaned up automatically).
If a member light is unpaired live from the SHC, the group triggers a
config-entry reload to rebuild its membership rather than holding a stale
reference. Translated to all 30 languages.

## 0.10.6 — consistent entity-action error handling

**No breaking changes.** User-visible improvement: entity actions that fail
now show a clear error instead of either silently no-oping or crashing.

Closes a gap flagged in 0.10.4's round notes as "a cross-cutting decision
bigger than this pass's scope": `button.py`'s 11 `async_press` methods,
`select.py`'s 18 `async_select_option` methods, `switch.py`'s 4
`async_turn_on`/`async_turn_off` methods, and `number.py`'s 10
`async_set_native_value` methods had **no handling at all** for the
library's own `SHCException`/`SHCConnectionError` — a real API rejection
or SHC comms failure during a write propagated as a raw unhandled
exception instead of a clean, translated error. All 43 methods now follow
the same pattern already established in `alarm_control_panel.py`/
`binary_sensor.py`: catch `(SHCException, SHCConnectionError)`, raise
`HomeAssistantError` with a shared per-platform translation key
(`button_press_failed`, `select_option_failed`, `switch_action_failed`,
`number_set_failed` — reusing the existing `smoke_test_failed` where the
action is literally a smoke test). Translated to all 30 languages.

`quality_scale.yaml`'s `action-exceptions` rule was already marked `done`
but the claim was incomplete — it only covered two custom domain services,
not these 43 entity write methods. Corrected with an honest accounting of
what's covered now versus before.

`climate.py`'s existing log-and-swallow behavior was deliberately **not**
changed: `_async_apply_hvac_mode` is a shared bool-returning helper used
by both `async_set_hvac_mode` and `async_set_temperature`, and the two
callers need to distinguish "mode write failed" from "mode is a no-op"
differently. Correctly disambiguating that so one caller can raise while
the other keeps its existing behavior is a real refactor of shared control
flow, not a mechanical wrap — risks a live behavior regression without
real-device verification, so it's tracked as a separate follow-up rather
than forced through blind.

15 new regression tests covering the error path (representative coverage
across all 4 files, not one per method — 43 near-identical error-path
tests would be redundant given they all exercise the same try/except
shape). 2988/2988 tests green, ruff/pylint/mypy/codespell clean, Gold
quality-scale gate still passes.

## 0.10.5 — runtime-data migration (Platinum quality-scale)

**No breaking changes; no user-visible behavior change.** Internal
architecture cleanup only.

Migrated the last remaining `runtime-data` quality-scale gap (flagged as
`todo` in 0.10.4's Round 1 audit): every platform's `async_setup_entry`
(button, binary_sensor, climate, cover, event, light, number, select,
sensor, switch, valve, alarm_control_panel, update), plus
`device_trigger.get_device_from_id`, `diagnostics.async_get_config_entry_diagnostics`,
and the options flow, now read `config_entry.runtime_data.session`
directly instead of the legacy `hass.data[DOMAIN][entry_id][...]` dict.
The two entity classes that only carry an `entry_id` string (not the
config entry object itself — `event.py`'s `SHCScenarioEvent` and
`switch.py`'s user-defined-state switch) look the entry back up via
`hass.config_entries.async_get_entry(entry_id)`. The parallel
`hass.data[DOMAIN]` population in `async_setup_entry`/`async_unload_entry`
and the now-dead `DATA_SESSION`/`DATA_SHC`/`DATA_TITLE`/
`DATA_POLLING_HANDLER`/`DATA_CERT_CHECK_UNSUB` constants are gone
entirely. `quality_scale.yaml`'s `runtime-data` rule is `done` again —
this time genuinely, not the false claim Round 1 corrected. Both the
Gold and Platinum quality-scale gates pass in full for the first time.

All ~65 touched files (17 source, ~45 tests) re-verified: 2973/2973 tests
green, ruff/pylint/mypy/codespell clean.

## 0.10.4 — 5-round bug hunt across every platform file

**No breaking changes.** Pins `boschshcpy==0.4.7` (see that repo's
CHANGELOG — vibration-switch no-op fix, `SHCLightControl` swap-config
gate, `OccupancyDetectionService` hardening, MD2 tamper-reset detection,
plus `SHCLight.hs_color`).

Five rounds of bug-hunting, one per group of platform files, each
independently re-verified against the actual current lib/API-doc source
before fixing (not a blind pass) and covered by a new regression test.
None of these are tied to a reported issue — found via code review.

### Fixed

- **`diagnostics.py` — 100% reproducible crash on every "Download
  diagnostics" click.** Read `info.updateState.name` unconditionally, but
  this integration only ever constructs `SHCSessionAsync`, whose
  `.information` (`_AsyncSHCInformation`) has no `updateState` at all —
  only a plain string `update_state` (`__init__.py` already had this
  exact compat guard elsewhere). The test's own mock was shaped like the
  old sync object, which is why CI never caught it.
- **`light.py`/`switch.py` — orphaned entity after toggling "expose as
  light" (#338).** Switching a Light/Shutter Control II or BSM device
  between light and switch reloads the config entry, but neither
  platform's setup loop removed the previous platform's stale registry
  entry — same failure mode already fixed for `MotionDetectorLight` in
  #356, now applied to both loops.
- **`device_trigger.py` — MD2 and Smoke Detector II got zero "Add Device
  Trigger" options.** `async_get_triggers` matched the literal Gen1 model
  strings `"MD"`/`"SD"`, but `binary_sensor.py` fires identical
  MOTION/ALARM bus events for MD2/Smoke Detector II via the same entity
  classes.
- **`cover.py` — direction flags could get stuck.** `async_open_cover`/
  `async_close_cover` never cleared the opposite direction flag;
  `BlindsControlCover.async_stop_cover_tilt` calls the same physical stop
  endpoint as `async_stop_cover` but never cleared them either. Also
  added a `CALIBRATING` branch — a real 5th `ShutterControlService.State`
  (APK ground-truth) that previously matched nothing and left the flags
  frozen during an end-position auto-detect run.
- **`binary_sensor.py` — excluding the virtual "Smoke Detection System"
  device silently dropped every individual Twinguard alarm sensor too**,
  even ones never excluded themselves. Decoupled the tracker/per-Twinguard
  creation from that one device's own exclusion flag.
- **`sensor.py` — `TwinguardCombinedRatingSensor` could raise instead of
  showing "unknown".** Its `_attr_options` was missing `"unknown"`, but
  the lib's `RatingState` genuinely falls back to it; every sibling enum
  sensor already listed its "unknown" member.
- **`number.py` — `HeatingCircuitSetpointNumber` crashed on an
  unconfigured eco/comfort preset.** `float(getattr(svc, name))` was
  called unconditionally, but the getter legitimately returns `None` for
  a preset that was never configured.
- **`update.py` — a failed install could still show "up to date".**
  `latest_version` didn't treat `UPDATE_FAILED` (a real state) as
  still-outstanding, hiding a failed update exactly when it matters most.
- **`button.py` — MD2's tamper-reset button gate never actually gated
  anything.** `hasattr(device, "reset_tampered_state")` was always `True`
  since the method is defined unconditionally; now gated on a real
  `supports_tamper_reset` property (lib-side, checks the actual service).
- Reauth flow (`config_flow.py`) hardened to match the sibling
  reconfigure/repair-credentials flows (exception handling, wrong-SHC
  guard), and orphaned cert/key files are cleaned up if authentication
  fails after they were already written. Repairs issue IDs
  (`ISSUE_CERT_EXPIRING`/`ISSUE_CAMERA_TOOL`) are now scoped per
  `entry_id` so multiple SHC controllers can't collide on the same
  warning.
- `switch.py`/`icons.json`: the Bypass switch's hardcoded `icon=` on its
  `EntityDescription` was overriding `icons.json`, the same precedence bug
  already fixed for `_attr_icon` in 0.10.2 — moved into `icons.json`.
- **Translation placeholder mismatches, caught by live-deploying this
  release before tagging it** (Home Assistant logs an ERROR on load for
  any string whose placeholder set doesn't match the English source):
  Round 1's `{title}` addition to `issues.cert_expiring.description` was
  never propagated to the other 29 languages; a long-standing gap from
  0.7.16 left 28 languages missing `{camera_tool}` in
  `options.step.init.description`; and Swedish had literally translated
  the `{model}` placeholder's *name* into `{modell}`, which can never
  match. Fixed all 29/28/1 languages respectively. `check-translations.py`
  (the CI gate) gained a placeholder-parity check so this class of bug
  fails the gate next time instead of only surfacing at runtime.

### Known, not fixed this round

- `quality_scale.yaml`'s `runtime-data` rule was corrected from `done` to
  `todo` — 14 platforms still use the legacy `hass.data[DOMAIN]` path
  instead of `entry.runtime_data`. This is an honest correction, not a
  regression; the actual migration is a separate follow-up.
- None of `button.py`/`select.py`/`switch.py`/`number.py`'s write methods
  catch `SHCException`/`JSONRPCError` the way `climate.py` or
  `alarm_control_panel.py`/`binary_sensor.py` do — a cross-cutting
  convention decision bigger than this pass's scope.
- `MICROMODULE_SHUTTER`'s `current_cover_position` can show a stale
  `_target_position` during a physical-switch/app-triggered move; a naive
  fix risks regressing the intentional "jump to target" UX for
  HA-initiated commands and needs real-device testing first.

## 0.10.3 — Real #356 root cause found in boschshcpy, plus a wider APK audit

Pins `boschshcpy==0.4.6` (see that repo's CHANGELOG — this release grew
out of finding the real root cause of #356 there, which led to a wider
audit against a decompiled copy of the official Bosch app).

### Fixed

- **#356 — Motion Detector II `[+M]` indicator-light entity missing.**
  Root cause turned out not to be the installation profile (that theory,
  posted on the issue, was wrong and has been retracted there): the
  `boschshcpy` property this integration's `light.py` depends on
  (`supports_light`) was never actually implemented in the lib, despite
  0.9.2's CHANGELOG claiming it shipped paired with `boschshcpy` 0.4.5.
  Since `light.py` reads it via `getattr(light, "supports_light", False)`,
  the missing attribute silently defaulted to "unsupported" for every
  `[+M]` Motion Detector II since 0.9.2. Fixed lib-side in `boschshcpy`
  0.4.6; comment here corrected to no longer claim a profile dependency.
- **`CommunicationQualitySensor` had an invented `medium` state** that
  doesn't exist in the real API (`boschshcpy`'s `CommunicationQualityService.State`
  had a fictional `MEDIUM` member with no matching value on real
  hardware). Now reports `not_supported`, the value the Bosch app itself
  uses. Translation key renamed (`medium` → `not_supported`) across all
  30 languages.

### Added

- **Outdoor Siren power-supply fault diagnostics**: 4 new diagnostic
  `binary_sensor` entities — `siren_ac_dc_error`, `siren_battery_defect`,
  `siren_battery_temperature_abnormal`, `siren_primary_power_supply_outage`.
  boschshcpy's `OutdoorSirenPowerSupplyService` already exposed all four
  (`ac_dc_error`/`battery_defect`/`battery_temperature_abnormal`/
  `primary_power_supply_outage`, matching the APK's `PowerSupplyState`
  getters `isAcDcError()`/`isBatteryDefect()`/
  `isBatteryTemperatureAbnormal()`/`isPrimaryPowerSupplyOutage()`), but
  boschshc-hass never wired them into an entity — a siren with a real
  AC/DC fault, defective battery, abnormal battery temperature, or a mains
  outage produced zero visible signal in Home Assistant. Gated on
  `supports_power_supply`, alongside the existing
  `SirenAcousticAlarmSensor`/`SirenVisualAlarmSensor`/`SirenTamperSensor`.
- **Installation Profile select now available on relays and smart
  plugs**, not just Motion Detector II — `InstallationProfileSelect` was
  already fully generic (works on any device with `supported_profiles`),
  it just wasn't offered outside `motion_detectors2`. Added 4 new profile
  translation strings (`light`/`heating_rcc`/`boiler`/`mini_pv`) across
  all 30 languages for the wider device vocabulary this now surfaces.
- **`HeaterTypeSelect`: `VOLT_FREE_HEATING` option** — matches the new
  `boschshcpy` 0.4.6 enum member (a real heater type seen on hardware
  that previously collapsed to `UNKNOWN`).

## 0.10.2 — Quality-scale audit: icon-translations gap + doc corrections

**No breaking config changes.**

Full audit of all 52 `quality_scale.yaml` claims against current code (4
independent reviewers, one per tier) found one real implementation gap and
several stale documentation claims — no other functional bugs.

### Fixed

- **`icon-translations`: 18 entity classes hardcoded `_attr_icon` alongside
  `_attr_translation_key`** (`binary_sensor.py`, `button.py`, `sensor.py`,
  `select.py`). A hardcoded instance icon wins over `icons.json`'s default
  lookup, silently defeating the point of icon translations. Moved all 18
  icons into `icons.json` (keyed by `translation_key`) and removed the
  hardcoded `_attr_icon`. `SHCScenarioButton` intentionally keeps its
  hardcoded icon (no translation key — dynamic per-scenario name, nothing
  to conflict with).

### Added

- **New CI gates**: `scripts/check-icon-translations.py` (fails if
  `_attr_icon` and `_attr_translation_key` ever co-occur on the same class
  again) and `scripts/check-parallel-updates.py` (fails if any platform
  module is missing `PARALLEL_UPDATES` — also caught that the previous
  hand-maintained count in `quality_scale.yaml` was stale by one platform,
  `update.py`).

### Changed

- Corrected several stale `quality_scale.yaml` claims: an ancient pinned
  `boschshcpy` version quoted verbatim in `dependency-transparency`;
  `docs-known-limitations`/`docs-supported-devices` still said SHC I/Classic
  were unsupported (README corrected this in 0.7.28, the tracking doc
  wasn't updated); `async-dependency` claimed the synchronous `SHCSession`
  is unused (it's still used for pairing in `config_flow.py`, correctly
  offloaded to an executor); `strict-typing` claimed `mypy --strict` passes
  on boschshc-hass (it runs its own documented, intentionally looser rule
  set, not literal `--strict`); a few stale class-name references.

## 0.10.1 — Motion Detector II indicator light left orphaned after profile switch (#356)

**No breaking config changes.**

### Fixed

- **Stale `MotionDetectorLight` entity after an installation-profile switch**
  (`light.py`, `select.py`, new `entity.py` helper). The Motion Detector II
  `[+M]`/OUTDOOR indicator light is only backed by BinarySwitch/MultiLevelSwitch
  services that exist in that profile; switching the device to GENERIC via the
  writable `select.installation_profile` (#353) made `light.py` simply stop
  creating the entity on the next setup pass, leaving the old one orphaned in
  the entity registry indefinitely. Two fixes: (1) new
  `entity.async_remove_stale_entity()` actively removes the registry entry
  once a MD2's light becomes unsupported/excluded/suppressed, instead of just
  skipping creation; (2) `InstallationProfileSelect.async_select_option` now
  triggers a config-entry reload after writing the new profile, so the
  entity list updates immediately instead of only after a manual
  reload/restart. The motion sensor itself is unaffected either way.
- Same cleanup now also fires when a MD2 that previously had the light
  entity is added to the excluded-devices option (was previously skipped
  silently, same orphaning bug).

## 0.10.0 — HA 2026.7 compatibility: purpose-specific event triggers

**Breaking requirement change:** minimum supported Home Assistant version is
now **2026.7.0** (was effectively unbounded before, floor enforced only in
CI at 2026.2.0). HACS will block installs/updates on older HA. CI now runs
on Python 3.14 (HA 2026.7.0 requires Python >=3.14.2).

### Added

- **Compatibility with HA Core's new purpose-specific `event.received`
  trigger** (HA 2026.7 "Integrations have long been able to add their own
  actions; now they can add their own triggers and conditions too").  This
  is an entity-domain-generic trigger platform
  (`homeassistant/components/event/trigger.py`) that HA Core now ships for
  every `event.*` entity — no bosch_shc-specific code was needed since our
  event entities (`UniversalSwitchEvent`, `LightControlButtonEvent`,
  `SHCScenarioEvent`, `MotionDetectorEvent`, `SmokeDetectionSystemEvent`,
  `SmokeDetectorEvent`) already declare `_attr_event_types` — the only
  attribute the new trigger's `is_valid_state` actually checks (confirmed
  against the installed HA 2026.7.0 source; `_attr_device_class` is unset on
  the two smoke/alarm event entities, which is harmless since `device_class`
  only affects icon/naming, not trigger matching). Users on HA 2026.7+ can
  now build
  automations directly on "Event received" for any Bosch SHC button,
  scenario, motion, or alarm event entity, in addition to the existing
  `device_trigger.py` device-automation UI path (unaffected, still
  bus-event-based).

### Changed

- `requirements_test.txt`: `homeassistant` floor raised `>=2026.2.0` →
  `>=2026.7.0`.
- `hacs.json`: minimum `homeassistant` version raised `2021.1.5` →
  `2026.7.0`.
- CI (`tests.yml`, `quality.yml`, `release.yml`): Python `3.13` → `3.14`.

## 0.9.3 — Eco/reduced state still blocked temperature writes (#73)

**No breaking config changes.**

### Fixed

- **`WRONG_THERMOSTAT_GROUP_MODE` when setting temperature on a room in
  eco/reduced state** (`climate.py`). 0.5.1 fixed the case where a room was
  in `AUTOMATIC` (schedule) mode by dropping it to `MANUAL` before writing
  the setpoint, but the SHC independently rejects the same write whenever
  the room's `low` (eco/reduced) flag is set — e.g. triggered by an open
  window, or by underfloor heating cutting out. That branch only ran when
  an explicit `hvac_mode` was passed to `set_temperature`; a bare call (the
  common case — a script or automation just adjusting the setpoint) never
  cleared it. `async_set_temperature` now clears `low` itself first,
  independent of `operationMode`, whenever the device reports it.

## 0.9.2 — Three rounds of fleet bug-hunt fixes

**No breaking config changes.** One behavior change worth knowing about:
a Motion Detector II in the base/GENERIC installation profile (no `[+M]`)
no longer gets a (previously crash-prone) indicator-light entity — see
"Fixed" below. Pins `boschshcpy==0.4.5` (also released today; see that
repo's CHANGELOG for the matching lib-side fixes).

Three rounds of proactive fleet bug-hunting (parallel independent agents
per round), every fix adversarially re-verified by an independent post-fix
pass before being applied. Deployed and running on Thomas' own HA before
this release.

### Fixed

- **Silently dropped temperature write on a device that was off**
  (`climate.py`). `set_temperature(hvac_mode="heat", temperature=21)` on an
  OFF device could skip the setpoint write entirely: boschshcpy only awaits
  the HTTP PUT, it never updates the local device cache, so the code was
  re-reading the pre-write (stale) state right after telling the device to
  turn on. A follow-up pass then found the fix itself needed to fall back
  to the real cached state when the mode write *fails* (network error) —
  otherwise a failed mode change was trusted anyway, masking the real
  error behind a second, more confusing "failed to set temperature"
  warning.
- **Off-loop crash on device deletion** (`entity.py`, `switch.py`).
  Deleting a device (or User Defined State) in the Bosch app while HA is
  running called `hass.async_create_task()` from boschshcpy's background
  polling thread — not thread-safe, raises under HA's non-thread-safe-
  operation guard. Switched to the thread-safe `hass.create_task()`.
- **Child-lock left unlocked across a restart** (`__init__.py`). The
  presence-driven child-lock feature only reacted to state-*change*
  events — a person already home when HA restarted stayed unlocked until
  their next transition. Now evaluates and applies the correct state once
  at startup/reload too.
- **Diagnostics leaked a Zigbee hardware address** (`diagnostics.py`).
  `device.id` (e.g. `hdm:ZigBee:5c0272fffe462481`) wasn't in the redaction
  list — every "Download diagnostics" dump (routinely attached to public
  bug reports) leaked one per device. Redacted, renamed to `device_id` so
  the redaction doesn't also swallow the non-identifying `service.id`
  fields the dump is read for.
- **Credential repair could silently repoint an entry at the wrong
  controller** (`config_flow.py`). `async_step_repair_credentials` didn't
  verify the target host is the *same* physical SHC before writing new
  credentials over an existing entry — a typo, DHCP reassignment, or a
  second controller on the LAN would silently succeed. Now mDNS-probes and
  verifies identity first, matching the existing `reconfigure_host` guard.
- **Twinguard alarm-tracker race** (`binary_sensor.py`). A burst of
  `SurveillanceAlarm` callbacks (e.g. multiple Twinguards) could have two
  `get_messages()` HTTP calls in flight at once with no ordering
  guarantee — a slower, earlier-started call could overwrite a faster,
  fresher one. Added a generation counter so only the most-recently-
  started call's result is ever applied.
- **Motion Detector II crashed on the base/GENERIC installation profile**
  (`light.py`). The `[+M]` indicator-light services (`BinarySwitch`/
  `MultiLevelSwitch`) only exist on an MD2 in the `OUTDOOR`/`[+M]` profile
  — the far more common base-profile MD2 has neither, so every state
  read/write on the indicator-light entity raised `AttributeError`. The
  entity is no longer created for a base-profile device (paired with a
  `boschshcpy` fix that also makes the underlying getters/setters
  None-safe).
- **Alarm arm/disarm commands could crash with a raw traceback**
  (`alarm_control_panel.py`). The SHC can reject an arm/disarm request
  (e.g. a door/window sensor open) — `async_alarm_disarm`/`arm_away`/
  `arm_home`/`arm_custom_bypass`/`mute` had no exception handling, unlike
  every other write path in this integration. Now raises a clean
  `HomeAssistantError` instead.
- **Dimmer min/max brightness could be set to an inverted range**
  (`number.py`). `Dimmer Min Brightness` and `Dimmer Max Brightness` are
  independent HA number entities with no cross-validation — setting one
  past the other's cached value silently sent an invalid range to the SHC.
  Now caught (the underlying `boschshcpy` service rejects it) and logged
  as a warning instead.
- **Valve position display truncated instead of rounded** (`valve.py`).
  `int()` truncates toward zero (63.9% showed as 63%, not 64%) — switched
  to `round()`, same precision class as the earlier Twinguard fix.

### Security

- No hass-side security findings this round (see the paired `boschshcpy`
  0.4.5 CHANGELOG for lib-side security fixes: private-key file
  permissions, no key material printed to stdout, password prompting).

## 0.9.1 — Complete translations for all 29 languages

### Added

- **Full translation parity across all 29 languages.** Every translation file
  was brought up to date with `en.json` (391 keys). Previously all non-English
  languages were ~74 keys behind, so recently added strings fell back to
  English. Newly localized strings include:
  - Repair issues shown to users: the **certificate-expiring** notice (with
    renewal steps) and the **camera-tool-available** notice.
  - Service/error messages (`exceptions.*`): certificate errors, rawscan and
    scenario lookups, smoke-test and alarm-state failures.
  - Entity names added in recent releases: `Installation Profile` (incl.
    Indoor/Outdoor states), `Dimmer Phase Control`, the renamed
    `Smart Sensitivity Security/Comfort Level` and
    `Orientation Light Response Time` selects, the `Floor Temperature`,
    `Purity`, `Air Quality`, `*_rating`, `Energy/Power Yield`, `Valve Tappet`
    and `Detection Test State` sensors, the `Call for Heat`, `Vibration`,
    `Smoke`, `Occupancy` and `Tamper` binary sensors, the `Motion Light` and
    the `Preview Min/Max Brightness` buttons.
  - The complete Slovak (`sk`) translation contributed in #354.

### Fixed

- Removed stale translation keys left behind by earlier entity renames
  (`smart_sensitivity_security`, `smart_sensitivity_comfort`,
  `orientation_light_response`, `detection_state`) from every language file.
  These four entities showed English names in all non-English locales.

### Developer

- New `scripts/check-translations.py` gate enforces **full `en.json` key
  parity** for every translation file (no missing fall-throughs, no stale
  keys) and keeps `en.json` in sync with `strings.json`. Wired into both
  `scripts/local-ci.sh` and the `Quality` CI workflow, replacing the previous
  shallow `options.features`-only check.

## 0.9.0 — Change the Motion Detector II installation profile from Home Assistant

### Breaking changes

- **The read-only `Installation Profile` sensor was removed and replaced by a
  writable `Installation Profile` select** (#353). The installation profile
  (e.g. `GENERIC` / `OUTDOOR`) of the Motion Detector II [+M] can now be
  **changed** from Home Assistant, not just read.
  - The old sensor (`sensor.*_installation_profile`) **no longer exists**. Any
    dashboard card, automation, or template that referenced that sensor
    entity_id must be updated to use the new select entity
    (`select.*_installation_profile`).
  - The former sensor was disabled by default, so most installations will only
    see a new select appear.

### Added

- **Writable installation profile** for the Motion Detector II [+M] (#353).
  Options are populated from the device's advertised `supportedProfiles`;
  selecting one writes the device-level `profile` field via the local API
  (`boschshcpy` 0.4.3 `SHCDevice.async_set_profile()`). Use cases: switch the
  detection environment (indoor ↔ outdoor) without the Bosch app, and include
  profile changes in automations.

### Requirements

- Requires **boschshcpy 0.4.3** (adds the device-profile write path).

## 0.8.4 — Stop phantom switch/alarm events on resubscribe and restart

- **Fixed** (#336): Universal Switch button presses (and motion / smoke-alarm
  events) could re-fire as **phantom events** when the SHC rotated its long-poll
  subscription (~every 24 h) and again on every Home Assistant **restart**. The
  controller re-delivers each service's current state on (re)subscribe; the
  device-trigger path (`bosch_shc.event`) for Universal Switches had **no replay
  guard**, so every switch's last keypress replayed at once — re-triggering
  device-trigger automations (e.g. "all lights turned on" with nobody home).
  The switch listener now tracks the last fired `eventTimestamp`, seeded from the
  device's current state at startup, and only fires when it advances. The motion
  and smoke / smoke-detection-system guards are now likewise **seeded at startup**
  so they no longer fire a stale snapshot once per restart. Genuine presses and
  real state changes still fire normally.

## 0.8.3 — Keep decimals for more Twinguard / thermostat readings

- **Fixed** (#352 follow-up): the same `int()` truncation behind the Twinguard
  temperature bug also coarsened three other readings the SHC sends as decimals
  — Twinguard **humidity** and **air purity**, and the Thermostat II **valve
  position**. These now keep full precision. The sensors display them rounded,
  so the visible value is unchanged; long-term statistics graphs are smoother.
  Requires **boschshcpy 0.4.2**.

## 0.8.2 — Fix Twinguard temperature reporting only whole degrees

- **Fixed** (#352): the Twinguard temperature sensor reported only integer
  values — stepwise 1 °C jumps and an apparent flat-line. The underlying lib
  (`boschshcpy`) truncated the reading with `int()`; it now keeps the decimal
  Bosch sends. Requires **boschshcpy 0.4.1**.

## 0.8.1 — Document Smoke Detector II intrusion-alarm scope

- **Docs:** clarified that the Smoke Detector II **intrusion alarm** switch (#174) sounds
  **only that one detector's** siren. Verified on real hardware (#322): it does **not**
  cascade to other smoke detectors / Twinguards and raises **no** Bosch app notification.
  There is no local-API path to force the whole intrusion-alarm system (`SurveillanceAlarm`
  is read-only; the IDS only supports arm / disarm / mute) — so a generic "trigger alarm"
  service is not feasible. README entity table now lists Smoke Detector II's `switch`
  separately with a footnote describing the single-device scope.
- No functional change — the 0.7.11 switch behaviour is correct as-is.

## 0.8.0 — Platinum quality scale + boschshcpy 0.4.0

### boschshcpy 0.4.0

- **`py.typed` marker** — full PEP 561 type annotations; mypy can now type-check against the library
- `SHCSessionAsync` — async-first session class (foundation for future async migration)
- All service + model classes exported from top-level `__init__`
- `certificate.py`: `not_valid_after_utc` (replaces deprecated `not_valid_after`)
- ruff + mypy CI gate in the library
- GitHub Release auto-creation after PyPI publish

### Platinum quality scale

All cumulative quality scale rules (Bronze → Silver → Gold → Platinum) are **done** or **exempt**.

- Full mypy strict typing across the integration
- mypy gate now **enforced** in CI (was informational)
- 2915 tests passing, ≥95 % coverage gate

### Bug fixes (since 0.7.25)

**Stability / crash fixes**

- `UserDefinedState` crash on deleted states (#351) — Bosch API omits `'deleted'`/`'state'` keys when `False`; `.get()` fallback prevents `KeyError`
- `SwitchDeviceEventListener` duplicate `homeassistant_stop` listener — caused `"Unable to remove unknown job listener"` ValueError on every HA restart (4× per boot); removed since `async_unload_entry` already calls `shutdown()`
- `SmokeDetectorSensor.is_on`: `try/except (KeyError, ValueError)` guard on `alarmstate`
- `ClimateDevice` setup: `KeyError` guard on `session.room(room_id)`
- `config_flow`: `None` guard in `async_step_reconfigure` / `async_step_credentials` (prevented `AttributeError` on pairing failure)
- `rawscan` service: `return None` → raise `ServiceValidationError` (required for `SupportsResponse.ONLY`)
- 20 `services_impl` defensive guards for partial long-poll updates (`KeyError`/`ValueError`)

**Sensor correctness**

- Battery sensors: no longer show `unknown` / `low` erroneously — `BatteryLevelService.warningLevel` safe fallback → `NOT_AVAILABLE`
- `BatterySensor`: `self.name` → `self._device.name` in logger calls (was `None` during polling callbacks)
- `entity_id` deprecation fix (#296) — `trigger_id` now uses stable `unique_id`
- Climate `AUTO` mode guard — prevents jumping back to auto on `set_temperature`
- Number entity `None` guard
- Binary sensor unsubscribe + `ValueError` guard
- Event dedup + `ValueError` guard
- Switch `available` property fix

**Device compatibility**

- README: SHC I, SHC II, and SHC Classic all ✅ supported (corrected wrong claim)

### CI hardening

- `quality.yml`: pip install retry-with-backoff (self-heals when a just-released lib pin hasn't reached the CDN edge)
- `scripts/local-ci.sh` — mirrors Quality + Tests workflows locally for pre-push checks

---

## 0.7.25 — Gold quality scale

All 49 cumulative rules (Bronze + Silver + Gold) are **done** or **exempt**.

### Code

**`entity_registry_enabled_default = False`** on 5 sensors (disabled until user opts in):
- `CommunicationQualitySensor` — diagnostic ENUM, rarely useful day-to-day
- `ValveTappetSensor` — diagnostic %, changes frequently during heating season
- `WalkStateSensor` — MD2 walk-test state, only relevant during active tests
- `DetectionStateSensor` — MD2 detection-test state, only relevant during active tests
- `InstallationProfileSensor` — diagnostic, set once at device installation

`WalkStateSensor` and `DetectionStateSensor` also gain `EntityCategory.DIAGNOSTIC`.

### Documentation

New sections added to satisfy Gold docs rules:
- **Supported devices** — SHC controller compatibility table + full accessory table
- **Data updates** — long-poll push model, reconnect behaviour, timeout option
- **Use cases** — home security, presence-based comfort, energy monitoring
- **Automation examples** — 4 complete YAML examples
- **Troubleshooting** — common setup errors table
- **Known limitations** expanded from 4 to 8 items

### CI

- `scripts/check-quality-scale.py` — quality scale gate script (`--tier bronze|silver|gold|platinum`)
- `quality.yml`: Gold is now the hard gate (was Silver)
