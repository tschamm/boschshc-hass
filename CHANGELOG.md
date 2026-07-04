# Changelog

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
