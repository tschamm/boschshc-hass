# Changelog

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
