# Changelog

## 0.12.23-beta.3 — Fix keypad-bridge name collisions across same-family devices (#282)

- **Fixes a naming collision in the keypad-bridge feature (#395)** that
  silently dropped bridge entities for most devices sharing a name prefix.
  The SHC-side `UserDefinedState.name` is capped at 30 characters, and the
  previous code truncated the device's friendly name to fit — but many real
  device names differ only in a trailing number (e.g. "Licht-/Rollladen­
  steuerung II 17" vs "... II 3"), which the truncation chopped off
  entirely. Every device in such a family beyond the first then failed to
  create its state with `USERDEFINEDSTATE_NAME_EXISTS` (HTTP 400), leaving
  it with no keypad-bridge switch entity at all and only a WARNING log line
  as a symptom. Live-confirmed via a real user's debug log (#282 comment
  5470304865) showing 12 devices in a row failing this way. Fixed by
  appending a short device-id-derived tag before truncating, so the name
  stays unique per device regardless of where its distinguishing text sits.
  Affects the shading, Light Control II, and Door/Window Contact II (SWD2)
  keypad-bridge buckets alike. No config-entry migration needed — devices
  that already succeeded keep their existing bridge untouched; only
  previously-failing devices get (re-)created with the new naming on the
  next sync.

## 0.12.23-beta.2 — Bridge Light Control II devices with a live Keypad service too (#282)

- **Removes the `has_keypad` exclusion** added in beta.1's keypad-bridge
  eligibility check. beta.1 assumed no PUSHBUTTON-configured Light Control II
  ever reports a live `Keypad` service; #282 comments from a real two-button
  unit (distinguished only by `keyCode` 1 vs 2) disproved that, so those
  devices got zero bridge entities instead of the expected 4. Eligibility is
  now `switch_type == PUSHBUTTON` alone. The pre-existing `event.py`
  `LightControlButtonEvent` entity (which can't tell the two buttons apart)
  is now suppressed for the same PUSHBUTTON+`has_keypad` devices, since
  keypad-bridge's per-`keyCode` automations replace it for that case. **Still
  needs real-hardware confirmation** that the SHC's automation engine
  actually discriminates by `buttonId` for `KeypadMicromoduleLightTrigger` at
  fire time (unlike shading, this hasn't been live round-tripped) — if
  you're on #282 with a live-Keypad device, please test and confirm pressing
  each button only triggers its own automation.

## 0.12.23-beta.1 — Bridge Light Control II's physical pushbutton into HA (#282)

- **Extends the keypad-bridge feature (#395) to Light Control II devices**
  configured as a non-switching push-button. Investigation into #282 found
  that Light Control II never actually gets a live `Keypad` service from
  the SHC in practice, so the original 0.6.0 `event.py` entity
  (`LightControlButtonEvent`, gated on `has_keypad`) never fires for real
  users — matching reports that no button entity appears no matter how the
  device is configured. Instead of polling a service that isn't there,
  this reuses the same SHC-local-automation-rule mechanism already shipped
  for Shutter/Blinds Control II and Door/Window Contact II: a small
  automation is created directly on the Controller with trigger type
  `KeypadMicromoduleLightTrigger`, pulsing a `UserDefinedState` that
  surfaces as a switch entity usable as an HA automation trigger.
  Eligibility is gated on `SwitchConfiguration.switch_type == PUSHBUTTON`
  (the "detach" option in the Bosch app) instead of `has_keypad`, and
  explicitly skips any device that *does* report `has_keypad=True`, so the
  older entity and the new bridge can never both fire for the same button.
  The trigger's field shape (`deviceId`/`buttonId`/`buttonEvent`) was
  decompile-confirmed to be identical to the already-live-verified
  shading trigger — only the `@type` differs. **Needs real-hardware
  confirmation** — the button-count assumption (2 buttons per device,
  matching the shading convention) and the eligibility gate itself are
  not yet confirmed on real Light Control II hardware. If you're hitting
  #282, please test this beta and report back, ideally with a
  `bosch_shc.trigger_rawscan` dump of the device.

## 0.12.22-beta.2 — Shutter II calibration: remove the counterproductive priming step (#396)

- **Bumps `boschshcpy` to `0.6.9b2`**, which removes the "priming" PUT
  (fake `referenceMovingTimes` + `level: 0.0`, plus a forced 5s-sleep-
  then-STOP) that `0.6.9b1`/`0.12.19-beta.1` added for the Shutter II
  recalibration button. A real debug-log capture proved that priming step
  was actively counterproductive: the device accepted the fake values as
  literal calibration data without moving at all, and
  `resetCalibrationAndOpen` itself reset that fake state right back to
  `false` anyway — so priming accomplished nothing, while the follow-up
  write triggered an unrelated close move that got forcibly interrupted
  before the real calibration drive even started. The recalibrate button
  now just triggers `resetCalibrationAndOpen` directly, letting the
  device run its own sequence uninterrupted. **Needs real-hardware
  confirmation** — this removes a confirmed self-inflicted bug, but it
  isn't yet confirmed this alone makes calibration complete successfully.
  If you're hitting #396, please test this beta and report back.

## 0.12.21-beta.1 — Shutter covers no longer get stuck "opening"/"closing" forever (#406)

- **Shutter/blind cover entities now self-correct if the SHC drops the
  push confirming a move finished.** `async_open_cover`/`async_close_cover`/
  `async_set_cover_position` set `is_opening`/`is_closing` optimistically
  and normally rely on the SHC's own long-poll push to correct them —
  #406 showed the SHC can silently drop that push (seen there as a brief
  `CommunicationQuality` blip coinciding with a scene moving sibling
  shutters), leaving the cover stuck "opening" indefinitely with only a
  manual nudge as a workaround. Each HA-initiated move now arms a 90s
  safety-net timer that force-refreshes the device directly (bypassing
  long-poll) if no confirmation arrived, re-arming itself if the device
  is still genuinely moving/calibrating, and retrying rather than giving
  up if that refresh itself fails. **Needs real-hardware confirmation**;
  if you're hitting #406, please test this beta and report back.

## 0.12.20-beta.1 — Expose valve motor status as a diagnostic sensor (#410)

- **New opt-in diagnostic sensor "Valve motor status"** for every
  thermostat (TRV) device, exposing `ValveTappetService.State` (e.g.
  `VALVE_TOO_TIGHT`, `NO_MOTOR_ERROR`, `VALVE_ADAPTION_IN_PROGRESS`) as a
  first-class ENUM state. Previously this was only visible buried inside
  the "Valve position" sensor's `valve_tappet_state` extra attribute,
  which can't be used as an automation trigger/condition — this makes
  valve motor errors directly alertable in HA.

## 0.12.19-beta.1 — Shutter II calibration actually calibrates (#396)

- **Shutter Control II recalibration button now triggers a full
  calibration sweep** instead of the shutter just nudging toward open and
  stopping without ever entering `CALIBRATING`. Bumps `boschshcpy` to
  `0.6.9b1`, which fixes the underlying cause — see that library's
  changelog for the full root-cause writeup. **Needs real-hardware
  confirmation**; if you're hitting #396, please test this beta and report
  back.

## 0.12.18 — Switch HACS installs to a dedicated release zip

- **HACS now installs from a dedicated `bosch_shc.zip` release asset** instead
  of GitHub's auto-generated full-repo source zipball. `hacs.json` sets
  `zip_release: true` / `filename: bosch_shc.zip`, and the release workflow
  builds that zip from `custom_components/bosch_shc/` (contents at the zip
  root, not nested) and attaches it to every tagged release. Smaller,
  faster HACS installs/updates; no change to what's installed on disk.

## 0.12.17 — Allow removing a stale/"ghost" device from the HA UI (#401)

- **A device the SHC no longer reports (e.g. unpaired in the Bosch app,
  leaving a "ghost" entry behind) can now be removed manually from Home
  Assistant's own device-registry UI.** Implements the standard
  `async_remove_config_entry_device` hook: a device is only offered for
  removal once it's no longer among the devices the SHC currently reports;
  the Controller (bridge) device itself is never removable this way. This
  only covers the HA-side cleanup — actually telling the physical SHC
  controller to forget a device has no known local-API endpoint yet and is
  not part of this change; see #401 for the open follow-up.

## 0.12.16 — Fix untranslated unit on the Open Doors/Windows sensor (#400)

- **SmartHomeController "Open doors and windows" sensor no longer shows an
  untranslated unit suffix** (e.g. "0 doors/windows" for non-English users).
  `SHCOpenWindowsSensor` set `native_unit_of_measurement` to the hardcoded
  English literal `"doors/windows"`; Home Assistant does not localize custom
  (non-standard) units, so it always rendered as-is regardless of the user's
  language. The entity's name is already fully translated via its
  `translation_key`, so the fake unit was dropped entirely rather than
  translated — it added nothing but the untranslated suffix.

## 0.12.15 — Revert Home Assistant floor back to 2026.7 (#399)

- **Home Assistant floor reverted from 2026.8 back to 2026.7** (`hacs.json`,
  `manifest.json`, `README.md`, `requirements_test.txt`). The 2026.8 bump in
  0.12.14 (originally beta.24) was proactive only — verified clean against
  2026.8.0 but not actually required by any code change. Per #399, many users
  wait for a HA `.1`/`.2` patch release before upgrading, so a same-day floor
  bump to a brand-new HA release needlessly blocked HACS installs/updates for
  them. No code changes; Python floor unchanged (still >=3.14.2).

## 0.12.14 — Bosch-app terminology sweep across all 30 languages; Shutter II direction fix; new room-climate sensors

- **Beta→stable promotion is now a manual maintainer step**, not an automated
  weekly one. The `promote-beta.yml` (Friday auto-promotion) and
  `auto-tag-beta.yml` (auto-tag on manifest version bump) workflows have been
  removed after repeated reliability issues. README's "Beta releases" section
  updated accordingly — there's no longer a guaranteed weekly stable release
  cadence.
- **Keypad bridge: dedupe the reset-action builder.** `_build_automation`
  and `_build_swd2_automation` (added in beta.26) built an identical
  `UserDefinedStateAction` on/off pair inline; factored into a shared
  `_reset_actions()` helper. Cosmetic only, no behavior change.
- **Keypad bridge (#395/#245/#342/#376): extended to Door/Window Contact II
  (SWD2/SWD2_PLUS/SWD2_DUAL) physical pushbuttons.** These devices have no
  Keypad service and were previously invisible to the local API for their
  button — two independent rawscans (#245, #342) found nothing, and #376
  documented it as a known limitation. Live-confirmed today: mapping a real
  SWD2 button to a Scenario via the official Bosch app and reading it back
  via `GET /automation/rules` revealed a dedicated, previously-undocumented
  trigger type, `ShutterContactButtonPressTrigger` (fields
  `shutterContactId`/`buttonPressState`, values `ON_SHORT_PRESS`/
  `ON_LONG_PRESS`) — a different shape from the Keypad-service devices'
  `KeypadMicromoduleShadingTrigger`, but the same bridge mechanism: a small
  SHC-side automation pulses a `UserDefinedState`, already exposed as a
  regular HA `switch` entity. Covered by the same opt-in option
  (`keypad_ha_bridge`), same self-managed create/cleanup lifecycle. See
  `bosch-shc-api-docs/best_practice/undocumented-local-endpoints.md` §10.
- **Home Assistant floor bumped to 2026.8** (`hacs.json` + `requirements_test.txt`)
  — proactive, following HA 2026.8.0's release. Verified clean (full test
  suite + mypy + pylint) against the real 2026.8.0 package; no code changes
  needed, none of its breaking changes/deprecations (legacy Service class
  removal, vacuum battery properties, `CONCENTRATION_*` constants — already
  migrated to `UnitOfRatio`) touch anything this integration uses. **Breaking
  for users still on HA < 2026.8**: HACS will block installs/updates until
  they upgrade Home Assistant. Python floor unchanged (still >=3.14.2).
- **Keypad bridge (#395): short and long press now get separate entities.**
  Reported after the initial release: combining `PRESS_SHORT`/`PRESS_LONG`
  into one automation with two triggers meant both press types pulsed the
  *same* `UserDefinedState`, making them indistinguishable to a downstream
  HA automation. Each button now gets two independent bridge entities
  (`... Btn1S`/`... Btn1L`, etc.) — four per keypad-capable device instead
  of two, each with its own single-trigger automation. Live-tested: a long
  press now only pulses its own switch, confirmed not to affect the short-
  press one. `UserDefinedState` names still respect the Controller's
  30-character limit.
- **Bumps `boschshcpy` to 0.6.8** (promoted from the 0.6.8b1 beta pinned
  above to the now-stable release — no code change, same live-tested
  Automation-rule/UserDefinedState create+delete layer).
- **New opt-in: bridge a physical Shutter/Light Control II pushbutton into
  a regular HA entity** (#395). When enabled, for every device with a
  detached pushbutton (Keypad service), the integration creates a small
  automation directly on the Bosch Smart Home Controller — Bosch's own
  local rule engine (`automation_rules_as_entities` above exposes the read
  side of the same engine), entirely separate from Home Assistant's own
  automations — that pulses a `UserDefinedState` when the button is
  pressed. That state already appears as a regular `switch` entity (no new
  HA platform code needed), so its on/off transitions can be used directly
  as a trigger in your own Home Assistant automations. Uses two
  previously-undocumented local API endpoints, `POST /automation/rules`
  and `POST /userdefinedstates`, traced via APK decompile and confirmed
  live against a real Controller — see
  `bosch-shc-api-docs/best_practice/undocumented-local-endpoints.md` §10.
  Fully self-managed: disabling the option removes everything it created;
  a device becoming excluded cleans up just that device's entries. Default:
  off (no SHC-side objects created, no new entities). Requires
  `boschshcpy` 0.6.8+.
- **Keypad bridge (#385): the Bosch app rejected our created triggers as
  "Ungültiges Auslöseereignis" (invalid trigger event).** Root cause: the
  feature used the generic `KeypadButtonPressTrigger` type (fields
  `deviceId`/`keyName`/`keyCode`/`buttonEvent`, `keyName` hardcoded to a
  placeholder), but shading devices (Shutter/Blinds Control) require the
  device-class-specific `KeypadMicromoduleShadingTrigger` type (fields
  `deviceId`/`buttonId`/`buttonEvent`, no `keyName`) — confirmed against a
  real pre-existing automation on the same physical device. Fixed by
  switching to the correct trigger type. Light Control II support is
  dropped from this feature for now: its own `KeypadMicromoduleLightTrigger`
  type is confirmed to exist but its field shape wasn't found via decompile,
  and guessing risked repeating this exact bug — it needs its own
  ground-truth confirmation before being added back. A schema-version bump
  makes existing bridge entries (created with the wrong trigger type) get
  automatically recreated with the correct one; no manual cleanup needed.
- **Cover: `async_set_cover_position` overriding a position the cover is
  already at left `is_opening`/`is_closing` stuck on an earlier move's
  direction** (#395 follow-up to the fix below). Unlike `async_open_cover`/
  `async_close_cover`, `async_set_cover_position` never set the direction
  flags itself — it relied entirely on the next `MOVING` update's
  target-vs-`_last_position` comparison, which leaves flags untouched when
  target equals the current position (a no-op override, e.g. a limiting
  automation re-asserting a bound the cover already satisfies). Fixed by
  setting direction explicitly from the requested position vs. the live
  baseline, same as `open`/`close`, so a no-op correctly clears stale flags
  instead of leaving them from whatever real move happened before it.
- **Cover: overriding an in-progress move with the opposite direction left
  `is_opening`/`is_closing` stuck on the first command's direction** (#395
  follow-up). `async_open_cover`/`async_close_cover`/`async_set_cover_position`
  set the direction flags optimistically, but only `async_set_cover_position`
  (and only for `MICROMODULE_SHUTTER`) refreshed `_last_position` — and even
  then via `current_cover_position`, which echoes the *previous* command's own
  still-in-flight target while `_app_command` is set. The next long-poll
  `MOVING` update's level-comparison fallback then recomputed the OLD
  direction from that stale baseline, clobbering the flags just set. Fixed by
  snapshotting `_last_position` from the live device level at the start of
  all three write methods, on both `ShutterControlCover` (BBL +
  `MICROMODULE_SHUTTER`) and `BlindsControlCover` (which had no
  `_last_position` refresh at all). New regression test simulating a
  close-overriding-an-in-progress-open sequence end to end.
- **Bumps `boschshcpy` to 0.6.7** — pure diagnostics addition, no behavior
  change: outgoing PUT/POST requests and their responses (e.g. the Shutter
  Control II recalibrate button's `resetCalibrationAndOpen`) are now
  visible in the debug log when `boschshcpy: debug` is enabled. Previously
  only the long-poll read stream showed up at debug level, leaving no
  trace of what the SHC actually returned for a write (#396 investigation).
- **Shutter Control II: physical long-press buttons (`outputMode:
  DETACHED_LONG_PRESS`) now report movement direction** (#385 follow-up).
  Direction detection previously only recognized `SWITCH_ON`
  (toggle/rocker switchType) and `PRESS_SHORT` (PUSHBUTTON switchType)
  Keypad events; a pair of PUSHBUTTON-type switches wired in
  `DETACHED_LONG_PRESS` mode (one physical button per direction — the
  common wiring for a hardware up/down rocker) instead fires `PRESS_LONG`,
  which fell through unrecognized and left `is_opening`/`is_closing`
  frozen at whatever they were before the press. Confirmed against real
  hardware rawscans that this button pair uses the exact same keycode
  1=open/2=close convention as the already-handled event types, so
  `PRESS_LONG` was added to the existing keycode branch rather than
  guessed at — no new heuristic, no risk of showing the wrong direction.
- **Proactive bug-hunt round** (6 parallel finder agents across boschshc-hass
  + boschshcpy, each confirmed finding independently adversarially verified
  before being fixed) — 14 real bugs confirmed and fixed:
  - `__init__.py`: the daily cert-check timer and the presence/silent-mode
    state-change listeners were only ever unregistered inside
    `async_unload_entry` — never called when a *later* step of the same
    setup attempt (e.g. `start_polling()`) fails and raises
    `ConfigEntryNotReady`. Every failed setup retry before an eventual
    successful load permanently leaked one more timer/listener for the rest
    of the HA process's life. Now registered via `entry.async_on_unload` so
    HA's own failed-setup cleanup path tears them down too.
  - `__init__.py`: the `trigger_rawscan` service could crash uncaught on an
    API failure and didn't check the matched config entry was actually
    loaded; now raises a proper translated error instead.
  - `cover.py`: a HA-issued open/close command's optimistic
    `is_opening`/`is_closing` flag was being clobbered by the SHC's own
    unreliable first "STOPPED" echo that follows every command — in the
    same direction-tracking area as the still-open #385 report.
  - `sensor.py`: Outdoor Siren power-supply diagnostics and Motion Detector
    II's walk/detection-state sensors were created unconditionally instead
    of being gated behind the `diagnostic_enabled` option like their
    siblings.
  - `alarm_control_panel.py`: an armed intrusion system with an
    unrecognized/custom configuration profile fell through to no state at
    all instead of degrading sensibly.
  - `update.py` / `button.py`: stale-entity cleanup and capability-flag
    gating gaps for Device/Controller updates and Motion Detector II's
    walk/detection/tamper-reset buttons.
  - `event.py`: a Light/Shutter Control II's button-event entity was left
    permanently orphaned in the registry after reconfiguring its switch
    type away from push-button (which drops the underlying Keypad
    service).
  - boschshcpy (6 fixes, all the same bug class as the historical #351 fix):
    `BinarySwitchService`, `MultiLevelSwitchService`,
    `HueColorTemperatureService`/`HSBColorActuatorService`,
    `ThermostatService.childLock`, and `PowerSwitchProgramService` all
    indexed the raw SHC state dict directly instead of degrading
    gracefully, so any of them could raise `KeyError`/`ValueError` on a
    partial long-poll snapshot instead of falling back like their
    already-hardened siblings in the same file.
  - 10 further candidate findings were reviewed and rejected at
    verification (mostly inconsistent, but not crash-prone, error-handling
    conventions) — not included here.

- **Reverted the previous beta's #394 fix — it broke real heating
  control.** The theory (reorder the writes so `operation_mode` is set
  before clearing `summer_mode`, avoiding a momentary "auto" flicker in the
  activity log) passed every unit test, since the mocked device accepts any
  write order. Live-testing against the real SHC found the API itself
  rejects that order outright — `WRONG_THERMOSTAT_GROUP_MODE` (HTTP 400) —
  meaning `summer_mode` genuinely has to be cleared *before* the SHC will
  accept an `operation_mode` change, not just as this integration's
  convention. The original write order is restored; the momentary "auto"
  log entry is a real, unavoidable consequence of the SHC's own two-step
  API and not something this integration can fix without breaking the
  ability to turn heating back on at all.

- **Fixed the previous beta's own Universal Switch key-name fix — it didn't
  actually work.** Live-testing on real hardware after shipping the fix
  above found the key entities still showed just the device name, no key
  suffix at all. Root cause, traced by reading Home Assistant core's own
  `entity.py` on the live system: `SHCEntity.__init__` only clears its
  default (unnamed) placeholder when a translation key is set at the
  *class* level — this entity picks its translation key per key position at
  *instance* construction time, so that placeholder silently survived and
  won over the translation lookup every time. Fixed, and the regression
  test that should have caught this the first time now actually does.

- **Proactive follow-up audit** (five parallel passes cross-referencing every
  entity name against the decompiled Bosch app's own string tables, plus a
  cross-check against a real 61-device/435-entity Home Assistant install)
  found two real bugs and around a dozen more terminology corrections beyond
  the #393 fixes above:
  - **Two number entities' worth of raw English text were showing on every
    non-English installation**: the temperature-offset number (12 TRVs + 3
    THBs), the Heating Circuit's eco/comfort setpoint numbers, and the
    Micromodule Dimmer's min/max brightness and dimming-speed numbers never
    had a `translation_key` at all — just a hardcoded English `name=`,
    unlike every other entity in the integration. Now translated in all 30
    languages.
  - **Universal Switch key-press event entities showed a raw internal
    constant as their name** — e.g. "Button LOWER_LEFT_BUTTON" instead of a
    real label. Now "Lower left key" (and the 5 other key positions),
    translated in all 30 languages.
  - **The Door/Window Contact II "Bypass" switch/timeout was the wrong
    feature name entirely** — Bosch's own app calls this the "Break"/"Pause"
    function, never "Bypass", for this device generation. Renamed to "Break
    function" throughout, all 30 languages.
  - Smaller corrections, same APK-verified basis: the climate "Boost" preset
    is "Fast heating" in the real app; "Room climate control" is "Room
    temperature control"; the "Summer break" sensor is "Heating break" (Bosch
    renamed this feature in-app); the walk/detection-test sensors and buttons
    are unified around "Function test" wording; "Open doors/windows" is
    spelled out as "Open doors and windows"; the shutter recalibration button
    is "Start automatic calibration"; the energy-reset button is "Reset
    consumption value". All translated to all 30 languages.
  - Two English-only casing fixes the earlier sentence-case sweep missed:
    a few ENUM state values (`communication_quality`/`zigbee_routing_quality`)
    and the valve/actuator "Normally Closed"/"Normally Open" options were
    still Title Case.
  - One issue was investigated and deliberately left alone rather than
    guess-fixed: one of five real smart plugs is missing its
    state-after-power-outage selector, most likely because its value was
    still unset at integration startup on that poll cycle — the underlying
    entity-creation pattern (skip creating the entity if the value isn't
    available yet, with no re-check later) is shared by several selects in
    this file, so a blind fix risked a wider regression without a live
    rawscan to confirm the actual cause first.

- **Corrected the Alarm System / Water Alarm device names, again** (#393
  follow-up) — the reporter pointed out the previous round's German names
  were still too literal/formal: it should be "Alarmsystem", not
  "Einbruchmeldesystem", and "Wasseralarm", not "Wasseralarmsystem",
  matching how the official Bosch app itself names these features
  (confirmed against the decompiled APK string dump, which consistently
  uses the short form throughout its UI). Same correction applied to
  English ("Alarm system" / "Water alarm") and fanned out to all 28
  other languages, then independently semantic-audited language by
  language against the actual meaning of each device rather than just
  checked for valid grammar. The audit also caught `translations/en.json`
  silently duplicating the pre-fix long names (out of sync with
  `strings.json`, HA's actual English source) and a mistranslated
  Ukrainian water-alarm name that read as "watery alarm" rather than
  "water leak alarm" — both fixed. cs/et/ja/lv/sk/pt phrasing was flagged
  as acceptable-but-worth-a-native-speaker's-second-opinion; nothing
  found to be semantically wrong in any of the 30 languages.

- **Fixed the real remaining cause of #393's Intrusion Detection System
  still showing its raw English name after the translation fix above.**
  The device is shared by two entities — the alarm control panel itself,
  and a separate "mute alarm" button — and the button's `device_info`
  still passed the raw, untranslated device name instead of the same
  `translation_key="intrusion_system"` its sibling uses. Home Assistant's
  device registry overwrites a device's name on every entity's setup, so
  whichever of the two entities finishes setup last silently clobbered the
  correctly-translated name written by the other. Fixed by making both
  entities use `translation_key` consistently, which also makes the fix
  independent of platform setup order. (An earlier hypothesis in this same
  investigation — that Home Assistant's translation cache needed manual
  pre-warming — was tested and found unnecessary; the cache was already
  warm, this device-registry overwrite was the actual bug.)

- **Fixed: three virtual "system" devices showed their raw internal
  English name in Home Assistant's device list** (#393) — Presence
  Simulation, the Intrusion Detection System, and the Water Alarm System.
  Unlike a physical device, these are singleton system devices whose name
  is either hardcoded in `boschshcpy` or supplied verbatim by the Bosch
  controller as an internal service identifier, not something a user names
  themselves — so it's now translated via Home Assistant's own
  `DeviceInfo(translation_key=...)` mechanism, in all 30 languages.

- **Fixed: `EMMA` also showed its raw internal codename instead of a real
  name** — a follow-up to the fix above. It was initially left untranslated
  on the assumption it was a Bosch brand name, like "Smart Sensitivity".
  The reporter corrected this with a screenshot of the official Bosch app
  (screen titled "Energiemanager") and Bosch's own marketing page: EMMA is
  an internal codename never shown to users, the real product name is
  "Energiemanager" / "Energy Manager". Fixed the same way, in all 30
  languages — and while in there, corrected `README.md`'s own wrong,
  apparently invented expansion of the acronym ("Energy Management Module
  A" — not a real Bosch term) to "Energy Manager (EMMA)".

- **Proactive bug-hunt round** (not tied to a specific reporter issue): an
  unwrapped, non-`SHCException` error during setup (e.g. a malformed
  response deep in `boschshcpy`'s async API layer) crashed setup and leaked
  the underlying `aiohttp` session, since only the typed exception handlers
  closed it. Several `boschshcpy` service properties that index the raw
  Bosch API state dict directly, without a safe fallback, could crash-loop
  or abort a whole platform's setup on a partial API poll: the smart-plug /
  light-switch / relay `switch` state and the thermostat child-lock switch
  (`switch.py`), the impulse-relay pulse-length `number` entity, and the
  Twinguard temperature/purity `sensor` entities. Also: toggling the
  scenario-buttons or automation-rule options off (or narrowing the
  scenario filter) reloads the config entry but never removed the
  now-unwanted button from the entity registry — the same orphaned-entity
  bug class as #356's MD2 indicator light, just never applied to these two
  button types until now.

- **Fixed `Pre-Alarm` missed by the sentence-case sweep above** — the
  hyphenated compound wasn't matched by that pass's rename table. Now
  "Pre-alarm", matching Bosch's own wording exactly (lowercase "alarm").
  Caught by a pre-release verification pass, not a user report — also
  brought 23 test fixtures across 4 test files back in line with the
  shipped names (they were self-consistent and passing either way, since
  they set their own expected value and assert against it, but no longer
  reflected reality).

- **All English entity names now use Home Assistant's sentence-case
  convention** ("Battery level", not "Battery Level"). The Bosch-terminology
  sweep above had already moved 66 names to sentence case because that is how
  Bosch writes them; the remaining 68 were still Title Case, leaving the
  integration visibly inconsistent. Acronyms, brands and model designations
  keep their capitals (LED, AC/DC, Zigbee, Smart Sensitivity, Shutter Control
  II). Display text only — no entity IDs, keys or behaviour change, and other
  languages are untouched since capitalisation follows each language's own
  orthography rather than English convention.

- **Two ENUM diagnostic sensors were showing raw internal slugs.**
  `battery_level` (5 states) and the Twinguard `combined_rating` (4 states)
  declared their options in code but shipped no translated `state` block, so
  Home Assistant rendered values like `critically_low_battery` verbatim.
  Both now have proper labels in all 30 languages — the air-quality grades
  follow Bosch's own wording ("Good"/"Moderate"/"Poor"). The
  translation-completeness CI gate gained a check that cross-references every
  ENUM entity's `options=[...]` in code against its `state` block, so this
  class of gap fails the build instead of reaching users; key parity alone
  could never catch it, since a key missing from all 30 files is
  "consistent", just consistently untranslated. That new check immediately
  found the `combined_rating` gap nobody had reported.

- **All user-facing entity names and states re-checked against Bosch's own
  app** — 66 English source strings corrected and re-translated across all
  30 languages. Until now our wording was invented independently of Bosch;
  it is now taken from the official app's own string tables (extracted from
  two APK builds), so entity names match what users see in the Bosch app.
  Highlights: the Motion Detector II's own indicator LED is the
  **Orientation light** (Bosch's "motion light" is the separate service
  that switches *other* lamps — we had been naming the wrong feature);
  Shutter Contact II vibration sensitivity levels were **one step off**
  Bosch's own scale (low/very low are Bosch's "Moderate"/"Low");
  `communication_quality` is Bosch's **Signal strength**; the smart-plug
  `installation_profile` is **Purpose of use** with Bosch's real option
  names (Standard / Lamp / Heater / Central heating / Mini PV system), where
  "Indoor (Generic)" had been factually wrong for plugs and relays; and the
  Room Thermostat II terminal options now say what they actually do
  ("Cable temperature sensor (with regulation)" instead of "Floor Sensor
  Displayed and Used for Regulation"). Deliberately NOT adopted: Bosch's
  "Burglar alarm" for our Smoke Detector II `intrusion_alarm` switch — ours
  sounds that one detector's local siren, Bosch's term means the
  whole-house alarm system, and adopting it would imply the switch arms
  your alarm.

- **Fixed: a Shutter II (`MICROMODULE_SHUTTER`) moved by a Bosch-app
  scenario, routine or the app itself reported the direction of the last
  *physical* wall-switch press instead of the real one** (#385) — e.g. a
  shutter closed by a "close all shutters" scenario kept showing
  `is_opening` because the last button press had been an open press. The
  SHC's `Keypad` service is sticky: it keeps reporting the last press
  forever (and replays it on every long-poll resubscribe), so the
  keycode-based direction detection added for physical-switch moves
  applied that press to every later movement too, no matter what started
  it. The keypad direction is now only used for the movement the press
  actually started: a *new* `eventTimestamp` arms it, the end of the
  movement (or any Home Assistant command) consumes it again. Moves with
  no fresh press fall back to the level comparison as before — so
  physical-switch moves keep working exactly as they did, and
  app/scenario moves no longer show a wrong direction.

- **New: two diagnostic binary sensors on each thermostat/TRV room's virtual
  RoomClimateControl device** (#389): **Summer Mode** (heating disabled for
  the season — already folded into the climate entity's `hvac_mode=off`,
  but that conflates it with any other future OFF cause, so a dedicated
  sensor gives automations and the dashboard an explicit, unambiguous
  trigger) and **Ventilation Mode** (an open-window/airing state that
  suppresses heating — never surfaced anywhere before this release).
  `boost_mode`/`low` (eco) were deliberately left out of this pass — both
  are already exposed via the climate entity's `preset_mode`
  (`boost`/`eco`), so a dedicated sensor would just duplicate existing
  state. Required a small `boschshcpy` fix alongside: `ventilation_mode`
  was modeled on the underlying service but never wired through the
  `SHCClimateControl` device-model wrapper this integration actually
  consumes (bumped to `boschshcpy` 0.6.5). Translated to all 30 languages.

- **Terminology fixes, prompted by reporter feedback on the beta**: several
  translated names in this release didn't match Bosch's own product
  terminology. German `SilentMode` is now "Flüstermodus" (was "Stiller
  Modus", per the reporter — confirmed against the official app's own
  string table); `PetImmunity` is "Kleintiererkennung" (was a
  backwards-reading "Tiererkennung ignorieren"); `VibrationEnabled` is
  "Erschütterungserkennung" (was the bare word "Vibration"). Two English
  source strings were also corrected after the same terminology check —
  `smartplug_routing` is now "Range Extension" (was "Routing" — Bosch
  never surfaces that word to users, it's a Zigbee mesh range-extending
  feature) and `nightly_promise_enabled` is now "Heartbeat" (was "Nightly
  Promise" — a literal translation of the internal API field name; Bosch's
  own app uses the untranslated brand term "Heartbeat" for this Twinguard
  self-test feature in every language checked). Both source-string changes
  were re-translated to all 30 languages, incidentally fixing a factually
  wrong "Night Mode" mistranslation of `nightly_promise_enabled` present
  in 3 languages (it's a periodic self-test, not a night-only mode).
  A second round of German corrections followed after the reporter checked
  our wording against their *current* Bosch app (the APK string table used
  above turned out to be an older build that predates MD2's Smart
  Sensitivity entirely, so their reading wins): `TamperProtection` →
  "Sabotageerkennung", `SmartSensitivity` → "Automatische Sensitivität",
  `PetImmunity` → "Haustier / Saugroboter vorhanden", and the two Smart
  Sensitivity level selects → "Sensitivität für Alarmsystem" /
  "Sensitivität für sonstige Fälle". That also settled a pre-existing
  inconsistency in the tamper group — the two tamper binary sensors said
  "Manipulation" while the reset button said "Sabotage zurücksetzen"; both
  sensors are now "Sabotage", matching the app and each other.

- **Fix: several MD2/TRV config switches and diagnostic sensors showed raw,
  untranslated internal identifiers instead of a translated name/state**
  (#387, #388). Root cause: `SHCSwitch.__init__` only drops the literal
  `attr_name` fallback (letting `translation_key` drive the shown name)
  when the entity description actually carries a `translation_key` — 4
  switch descriptions (Pet Immunity, Smart Sensitivity, Tamper Protection,
  Silent Mode) never had one, so users saw raw strings like "PetImmunity"
  or "SilentMode" in the UI. The MD2 Detection Test State diagnostic
  sensor had the same gap one level up: its enum options were never given
  translated state labels, so it showed "detection_test_stopped" instead
  of "Stopped".
  A follow-up audit (prompted by two independent bug-hunt passes on the
  initial fix) found the identical bug pattern on 14 more switches that
  weren't in the original reports — smart-plug Zigbee routing, Light/
  Shutter Control II swap-input/output config, security-camera light/
  notification toggles, Twinguard nightly-promise/humidity-warning,
  smoke-detector pre-alarm/intrusion-alarm, and Shutter Contact II
  vibration-detection — affecting smart plugs, cameras, Twinguard, smoke
  detectors and Shutter Contact II owners, not just MD2/TRV. All 18
  switches now carry a proper `translation_key`; the Detection Test State
  and Walk Test State sensors now have translated state labels. Translated
  to all 30 languages. No behavior change — display names/states only.

## 0.12.13 — firmware update entity no longer leaks untranslated English text

- **Fix: the firmware update entity's version fields and lifecycle notes
  showed untranslated English regardless of the configured language**
  (#377 follow-up). Root cause: Home Assistant's `UpdateEntity` never
  translates `installed_version`/`latest_version`/`release_summary` — they
  render byte-for-byte in every locale. 0.12.11's fix for this same issue
  swapped one untranslated English marker (`"Up to date"`) for another
  (`"current"`), and additionally added two English disclaimer sentences
  (a low-battery precondition, a post-install manual calibration reminder)
  directly into `release_summary` — which, being an HA-level field, can
  never be localized no matter what string is put there. Both disclaimers
  are now proper, translated **Repair issues** instead (`update_battery_low`
  / `update_calibration_required`, translated to all 30 languages), created
  only when actually relevant (an update is pending and the battery reports
  low; a thermostat has finished installing and is awaiting calibration)
  and cleared automatically once resolved or when the entity is removed.
  `release_summary` now only ever carries the bare, technical, raw
  firmware-state token, matching the convention every other real ha-core
  `UpdateEntity` uses for this field. The `installed_version`/
  `latest_version` "no real version" marker was also changed from the
  word `"current"` to `"n/a"` — HA still can't translate it, but a short,
  widely-recognized abbreviation reads less like a leaked, forgotten
  translation than a full English word does.

## 0.12.12 — fix uncaught start_polling failures leaking the session

- **Fix: a MICROMODULE_SHUTTER's physical wall switch could not reliably
  drive `is_opening`/`is_closing` when configured as a `PUSHBUTTON`
  switchType**, unlike a toggle/rocker (`SWITCH`) switchType. Root cause
  (#385, confirmed against two real-hardware rawscans — one HA-UI-triggered
  move, one physical-button-triggered move): the direction-detection logic
  only recognized `Keypad` events of type `SWITCH_ON` (keycode 1=open/
  2=close); a `PUSHBUTTON`-configured device instead sends `PRESS_SHORT`
  events with the same keycode semantics, which fell through to a
  level-vs-last-position fallback that can't work here — the device's
  `level` attribute doesn't update until the move finishes. Fixed by
  recognizing `PRESS_SHORT` alongside `SWITCH_ON` for the same keycode
  mapping. 2 independent bug-hunt passes found no cross-device interference
  (WRC2/SWITCH2 button-press automations use unrelated Keypad service
  instances) and confirmed the fix is correctly scoped.
- **Fix: a Shutter Control II cover's `operation_state` attribute (`MOVING`/
  `STOPPED`/`OPENING`/`CLOSING`/`CALIBRATING`) could never be used in an
  automation state-trigger**, even though it displayed correctly in the UI.
  Root cause (#385): the attribute exposed the raw `boschshcpy` `Enum`
  member instead of its `.name` string — HA's frontend shows it correctly
  (orjson serializes `Enum`s by value for the websocket), but the backend
  state-trigger engine compares the actual Python attribute value against
  the string configured in the automation, and a plain `Enum` member is
  never `==` to a string. Fixed by exposing `.name`, matching the existing
  convention used by every other enum-valued attribute in this codebase
  (e.g. `binary_sensor.py`'s `alarm_state`). 2 independent bug-hunt passes
  confirmed this was the only instance of the pattern.
- **Docs: diagnostics.py no longer claims to be a "rawscan-equivalent".**
  `_device_dump` only includes services `boschshcpy` recognizes
  (`SUPPORTED_DEVICE_SERVICE_IDS`) — an unmapped/unknown service on a device
  is silently absent from the diagnostics download. Found via Copilot's
  review on the sibling ha-core PR (home-assistant/core#177390), which
  carries the same limitation. The module docstring now says so plainly and
  points to the existing `bosch_shc.trigger_rawscan` action for a truly
  unfiltered dump. No behavior change.
- **Fix: a network drop during setup's long-poll subscribe step could crash
  integration setup uncaught and leak the underlying aiohttp session**,
  instead of triggering Home Assistant's normal `ConfigEntryNotReady` retry.
  Found while addressing the same class of finding on the sibling ha-core
  PR's Copilot review (home-assistant/core#177379): `start_polling()`'s
  initial subscribe call is a real network request and can raise
  `SHCConnectionError`/`SHCSessionError`/`JSONRPCError`, none of which were
  previously caught. Also broadened the existing `async_init()` error
  handling to catch `SHCSessionError` alongside `SHCConnectionError` — same
  gap, one step earlier in setup. Both paths now log a warning, close the
  session cleanly, and raise `ConfigEntryNotReady`. New regression tests
  covering all three exception types on both call sites.

## 0.12.11 — translate TRV_GEN2 select states + ChildLock/Valve entity names (#377)

**Breaking (select option values, not entity IDs):** if you automate against
the raw option string of any of the 16 selects listed below, update those
automations — the values are now lowercase (e.g. `SETPOINT` → `setpoint`).

- **Fix: several entity names and select-entity option values showed up
  untranslated (raw internal identifiers) in the UI** instead of a
  translated label — reported on a TRV_GEN2 (radiator thermostat) with
  screenshots showing "ChildLock", "Valve", and raw enum values like
  `SETPOINT`/`NORMAL` for the "Displayed Temperature"/"Display Direction"
  selects. Root-caused two independent bugs, both fixed at the source
  rather than patched per-entity:
  - The `child_lock`/`child_lock_thermostat` switch descriptions and
    `SHCValve` never set a `translation_key`, so their `attr_name`
    ("ChildLock"/"Valve") rendered literally. Both now translate (`Child
    Lock` / `Valve`).
  - Every select entity built from the shared enum-reading helper
    (`_enum_attr_current_option_fn`/`_enum_attr_select_option_fn`) exposed
    its options as raw uppercase Python enum member names
    (`SETPOINT`/`FLOOR_SENSOR_CONNECTED`/…) with no matching `state`
    translation block — this affected 16 selects total, not just the 2
    visible on a TRV_GEN2: `motion_sensitivity`, `vibration_sensitivity`,
    `orientation_light_response_time`, `state_after_power_outage`,
    `smoke_sensitivity`, `display_direction`, `displayed_temperature`,
    `terminal_type`, `valve_type`, `heater_type`, `switch_type`,
    `actuator_type`, `output_mode`, `smart_sensitivity_security_level`,
    `smart_sensitivity_comfort_level`, and `dimmer_phase_control`. Fixed by
    lowercasing the option values (matching the convention this integration
    already uses for `siren_sound_level`/`installation_profile`, the two
    selects that were already translated correctly) and adding the missing
    `state` translations across all 30 languages.
- **Fix: the per-device Firmware update entity's "Up to date"/"Update
  available" text always showed up in English**, even on a fully translated
  UI (#377 follow-up, reported after confirming the fix above). Root
  cause: those were literal English sentences used as this integration's
  synthetic `installed_version`/`latest_version` values — Home Assistant's
  `update` entity always renders those fields verbatim, in every
  integration, since they're meant to be real version numbers, never
  translated text. Replaced the sentence markers with the device's own raw
  firmware-state token (already shown untranslated in the entity's detail
  view below, e.g. `AwaitingActivation`) so the field no longer looks like
  a broken translation.
- **CI: fixed the weekly beta→stable promotion workflow shipping every
  "promoted" release still reporting itself as a beta in HACS** (#378),
  regardless of the user's HACS channel setting. `promote-beta.yml` was
  re-tagging the beta commit as-is, so `manifest.json`'s own `"version"`
  field kept its `-beta.N` suffix forever — HACS reads that field directly.
  The promotion job now corrects the version field in a new commit before
  tagging it as stable. (v0.12.10's already-published tag is unaffected by
  this fix and stays as-is per this repo's never-retag-a-published-release
  rule; superseded once this release promotes.)

## 0.12.10 — fix ~150s startup delay from a blocking Zigbee-routing refresh

- **Docs/UX: surfaced two SHC-enforced firmware-update preconditions that
  this integration can't detect or override** (#373 follow-up, reported
  live by a user working through a real install): a low battery level
  blocks the SHC from starting an update at all, and radiator thermostats
  (`TRV`/`TRV_GEN2`/`TRV_GEN2_DUAL`) require a manual on-device (or
  Bosch-app) calibration step after install that Home Assistant has no way
  to represent — it just shows "Update pending" until you do it. Both are
  now shown as disclaimers in the per-device `update` entity's more-info
  dialog (`release_summary`, alongside the raw lifecycle state) and
  documented in the README's Firmware updates section.

- **Fix: the per-device firmware update entity's "Installed version"/"Latest
  version" fields showed the raw internal marker strings `up_to_date` /
  `update_available`** instead of anything version-like, making them look
  like broken data in the more-info dialog (reported on #373 with
  screenshots comparing against the SHC controller's own update entity,
  which does show a real version number). The per-device firmware probe
  (`devicemanagement/firmware/{id}`) genuinely has no real version number to
  report — only a bare lifecycle-state string — so these fields were always
  fake placeholders used solely to make HA detect "update available" via
  inequality; they're now human-readable text (`Up to date` / `Update
  available`) instead of the raw snake_case state name.
- **Fix: a corrupted or missing client certificate/key crashed setup with a
  raw, cryptic `ssl.SSLError: [SSL] PEM lib`** instead of offering the
  existing "reconfigure the integration" recovery flow. Reported via a
  community-forum traceback after an unrelated controller restart coincided
  with (unrelated to it) a corrupted on-disk PEM file. The pre-flight
  certificate check already existed but only ever validated the
  certificate, never the key, and deliberately doesn't block setup on parse
  failures — so a bad cert *or* key fell straight through to an unguarded
  `build_ssl_context()` call. That call is now wrapped and raises
  `ConfigEntryAuthFailed` on `ssl.SSLError`/`OSError`/`ValueError`,
  triggering the integration's existing reauth/"repair credentials" flow
  (re-pairing writes fresh, valid PEM files). Bumped `boschshcpy` to 0.6.4,
  which fixes the same crash shape one layer down (`SHCAPIAsync`'s
  ssl_context fallback path) and hardens the cert/key write path with
  `os.fsync()` against a possible root cause (a torn write surviving until
  a much later, unrelated restart).
- **Fix: the integration could take minutes to load after a restart**,
  reported live via a real user's "integration startup time" diagnostics
  (147s for Bosch SHC vs. under 22s for every other integration). Root
  cause: `async_setup_entry` `await`ed the Zigbee-routing coordinator's
  first refresh inline — that coordinator deliberately queries every
  Zigbee-attached device sequentially, live over the air (never cached
  SHC-side, to avoid bursting the mesh), so a setup with many or
  slow/sleepy Zigbee devices could block the entry from reaching `LOADED`
  for a long time over data that only backs an opt-in diagnostic sensor.
  Now scheduled as a config-entry-scoped background task instead — the
  entry no longer waits on it, and the task is explicitly cancelled during
  unload/reload (before the shared HTTP session closes) so an in-flight
  refresh can't race a closed session.

## 0.12.9 — update-entity asymmetry fixes + HA convention alignment (#373)

**Breaking (opt-in feature re-defaulted to off):** if you use the
temperature-drop switch/number entities, re-enable them under
**Settings → Devices & Services → Bosch SHC → Configure → Features**.

- **Fix: `ControllerUpdate.async_install` had no state guard**, unlike its
  sibling `DeviceUpdate` — the entire point of the #373 fix was "only
  activate from the ready state, everything else 409s", but that guard was
  only ever added to `DeviceUpdate`. A service-call install (or a user
  double-clicking) while the controller was already `DOWNLOADING`/
  `INSTALLING` would reproduce the same raw-409 bug. Both classes now use
  the same guard pattern.
- **Fix: the `update_install_failed` error discarded the real failure
  detail** — the frontend showed a generic "Failed to start the firmware
  update.", with the actual device name/error only visible in logs. Now
  threads both through via `translation_placeholders`, matching how
  `update_not_ready` already worked. All 29 languages.
- **Fix: a non-`SHCException` error during the background poll (e.g. a bare
  timeout) could propagate out of `async_update`** and, via `async_install`'s
  `finally` re-poll, mask the real install error. Both `async_update`
  methods now catch every exception, matching their own documented "never
  raise from a poll" contract.
- **New: both entities now declare `device_class = UpdateDeviceClass.FIRMWARE`**,
  matching the convention used by every comparable reference integration
  (shelly/esphome/zha/matter) — found via a dedicated research pass
  comparing our `update.py` against HA core's own reference integrations.
- Two more independent research/bughunt passes reviewed the whole file
  against upstream HA conventions and reference integrations; besides the
  above, everything else checked (feature flags, entity_category,
  percentage handling, translation shape, `EntityDescription` applicability,
  `should_poll`/`SCAN_INTERVAL`, stale-entity cleanup) was already correct
  or genuinely not applicable to this integration's shape.
- Docs: README now explains the beta release train and how to opt in to
  beta versions via HACS.
- **Fix: some Zigbee devices could be silently missing from the
  `bosch_shc.export_zigbee_topology` map entirely**, with no indication
  they even exist — reported live by real users (motion detectors,
  Twinguards, and window contacts missing from their exported map). Root
  cause: a device only became a node in the graph if its on-demand routing
  query succeeded that poll cycle; a sleepy battery end device that didn't
  answer in time (a real, expected Zigbee behavior, not a malfunction) was
  simply omitted. Every currently-paired Zigbee device is now always shown
  as a node — unconnected if it has no routing data yet, instead of
  invisible.
- Docs: the "Visualizing your Zigbee mesh" README section is now
  "Visualizing your mesh", explaining both radio generations — the real
  Zigbee mesh export, and why an equivalent map isn't possible for 868 MHz
  (gen-1, `hdm:HomeMaticIP:`) devices (confirmed via decompiling the
  official Bosch app: this protocol has no per-device routing telemetry at
  all, only a plain on/off repeater-role flag on Plug+ units).
- Reviewed this integration's REST polling patterns for unnecessary load on
  the SHC and battery-powered Zigbee end devices — the following points were
  found and fixed:
- **Fix: Zigbee routing info no longer polls periodically at all.** It now
  fetches once at Home Assistant startup only; a new `refresh_zigbee_routing`
  action lets you pull a fresh reading on demand (e.g. right before
  `export_zigbee_topology`). Each query is a live over-the-air round-trip to
  the physical device — not cached SHC-side — so even the previous slower
  interval was needless drain on battery-powered Zigbee end devices. The
  per-device queries are also now sequential, not concurrent, avoiding a
  request burst against the SHC and the mesh itself.
- **Fix: several entities silently defaulted to Home Assistant's 15-second
  poll interval** (automation-rule status, thermostat regulation algorithm,
  the doors/windows summary sensor) because no explicit interval had been
  set. Now 5–15 minutes depending on how often the underlying data actually
  changes.
- **New: the temperature-drop switch/number entities are now opt-in**
  (`OPT_TEMPERATURE_DROP_ENTITIES`, default off) — most setups don't use
  this feature, and it was one of the sources of unnecessary 15-second
  polling above.
- Investigated, confirmed NOT a bug: `CameraLight`/`CameraAmbientLight`/
  `CameraFrontLight`/`PrivacyMode` switches carry an explicit
  `should_poll=True` since ~2023. Confirmed these camera services are, like
  every other device service, delivered via the long-poll stream — the SHC
  only ever serves its own cached value regardless of polling, so no change
  is needed here.
- **Fix: `OPT_SSL_VERIFY_HOSTNAME` silently did nothing on the async
  connection path**, unlike its sibling `OPT_SSL_SKIP_VERIFY` which at
  least logs a warning when its setting can't be honored. A user enabling
  this option got no feedback that it was a no-op. Now warns consistently
  with the sibling option.
- **Fix: `TwinguardSmokeAlarmSensor.async_request_smoketest` raised an
  error with no message** — every other write-path error in this
  integration includes the device name and underlying exception text;
  this one silently discarded both, leaving only a generic translated
  string with zero diagnostic info.
- Docs: added the missing `temperature_drop_entities` row to the README's
  options table (the feature itself shipped earlier this week, the docs
  entry was missed).
- Pins `boschshcpy==0.6.3`, which fixes a real long-poll robustness gap:
  any error other than "unknown poll id" (code -32001) on the poll call
  never invalidated the poll id, so the poll loop kept retrying with the
  same broken id and repeating the identical error indefinitely instead of
  recovering via resubscribe. Now any poll error triggers the same
  resubscribe-and-refresh recovery already used for -32001.

## 0.12.8 — more firmware update-entity fixes from a 2-agent bughunt (#373)

**No breaking changes.**

- **Fix: `UpdateAvailable` wasn't counted as "in progress".** Live-confirmed
  on #373: the Bosch app showed "updating" continuously for 7+ minutes
  while the probe sat on `UpdateAvailable`, not just a transfer starting
  soon. It's now bucketed alongside `UpdateRunning`/`TransferringUpdate`/
  `Unknown`.
- **Fix: a second Install click before the next scheduled poll (up to 6h
  later) could re-trigger the exact raw-409 bug 0.12.4 fixed.** Neither
  `DeviceUpdate.async_install` nor `ControllerUpdate.async_install`
  re-polled the firmware state after activating — so the entity kept
  reporting the pre-activation state until its next poll, and a second
  click during that window would re-send `activate` against a device that
  had since moved on. Both now re-poll immediately after the install call
  (success or failure).
- **Fix: `ControllerUpdate.async_update` had no error handling**, unlike
  its sibling `DeviceUpdate.async_update` — a transient probe failure
  would have made the entity unavailable instead of just keeping its
  last-known state. Now matches the same guard.
- Softened the `update_not_ready` error message (no longer implies "the
  controller is still preparing it", which was misleading for e.g. a
  genuinely `Failed` or `AwaitingActivationTimeout` state) — all 29
  languages.
- Two independent bughunt passes reviewed the whole file; one open,
  unconfirmed item was deliberately left alone: whether `UpdatePending`
  (the state immediately after a successful activation) should also count
  as "in progress" — plausible from the module's own documented live
  trace, but not yet directly evidenced the way `UpdateAvailable`/
  `Unknown` were, so it wasn't blind-fixed.

## 0.12.7 — firmware update entities now poll immediately on startup (#373)

**No breaking changes.**

- **Fix: firmware update entities showed stale/unset state for up to 6
  hours after every restart.** `async_add_entities()` was called without
  `update_before_add=True` — confirmed against HA core's own
  `entity_platform.py`, a polling entity's *first* poll is scheduled a full
  `SCAN_INTERVAL` from the moment it's added, not immediately. For these
  entities (`SCAN_INTERVAL = 6h`), that meant every restart/reload left
  `DeviceUpdate`/`ControllerUpdate` sitting on their initial `None` state
  — reported as "up to date" with no progress shown — until the next
  scheduled poll up to 6h later. This is what #373's reporter saw right
  after updating to 0.12.6: the entity hadn't actually re-polled yet, it
  wasn't a regression in the `Unknown`-state fix itself. Entities now poll
  once immediately when added.

## 0.12.6 — fix firmware update entity hiding an update still actually in progress (#373)

**No breaking changes.**

- **Fix: the per-device firmware update entity could silently claim
  "up to date" while the device was still actively updating.** The
  `Unknown` firmware lifecycle state was bucketed together with "nothing
  to install", but our own live-confirmed transfer trace
  (`AwaitingActivation` → `UpdatePending` → `Unknown` (mid-transfer) →
  `UpToDateAwaitingUserInteraction`) shows `Unknown` genuinely occurs
  *during* an active transfer, not once it's done. Reported live on #373:
  the Bosch app still showed "Firmware wird aktualisiert ..." while our
  entity had already dropped to "up to date" and hidden the update
  entirely. `Unknown` now counts as in-progress/pending, matching reality.

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
