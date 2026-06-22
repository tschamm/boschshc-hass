[![Validate][validatebadge]][validateworkflow]
[![Tests][testsbadge]][testsworkflow]
[![Quality][qualitybadge]][qualityworkflow]
[![hacs_badge][hacsbadge]][hacs]
[![Stars][stars-shield]][bosch_shc]

[![Buy tschamm a coffee][buymecoffeebadge-tschamm]][buymecoffee-tschamm]
[![Buy mosandlts a coffee][buymecoffeebadge-mosandlts]][buymecoffee-mosandlts]

# Bosch Smart Home Controller (SHC) for Home Assistant

![Bosch Smart Home](https://local.apidocs.bosch-smarthome.com/images/bosch_smart_home_logo.png)

A **local-only** Home Assistant integration for the Bosch Smart Home Controller (SHC I & II).
It talks directly to the controller over mutual-TLS on your LAN — **no cloud, no polling**
(`local_push`) — using [boschshcpy](https://github.com/tschamm/boschshcpy) as the API backend
(the exact version is pinned in [`manifest.json`](custom_components/bosch_shc/manifest.json)).

> Looking for the version that ships *inside* Home Assistant Core? This HACS repo is the
> **bleeding-edge** upstream — fixes and new devices land here first, then flow to Core later.

---

## Highlights

- 🔒 **Local & private** — mutual-TLS to the SHC, real-time push updates, nothing leaves your network.
- 🧩 **Broad device coverage** — thermostats, shutters/blinds (with tilt), micromodules, plugs,
  lights, cameras, Twinguard, smoke & intrusion, motion, contacts, water-leak, EMMA, and more.
- ⚙️ **Rich options flow** — expose scenarios as buttons, presence-based child lock, a device/room
  filter, and connection tuning — all with safe defaults (existing setups are never changed).
- 🌍 **30 languages** for the configuration UI.
- 🏅 **Home Assistant Bronze** quality scale.

### New in 0.7

- **Motion Detector II [+M] — full configuration parity with the Bosch app:**
  - **Detection (Walk) Test** — start/stop buttons + a state sensor, for verifying
    mounting/coverage. Works whether your device exposes the `DetectionTest` *or* the
    `WalkTest` service (the local API and the app use different names for the same feature).
  - **Tamper protection** — a switch to enable/disable it, a **Reset Tamper** button to
    clear an active tamper condition, and the existing tamper state sensor.
  - **Orientation-light response time** — a select (Long = lower battery use / Short =
    more responsive), backed by the `PollControl` service.
  - **Installation profile** — a read-only sensor showing the active environment
    (e.g. `GENERIC` / `OUTDOOR`).
- **Climate display polish** — preset icons (`auto`/`manual`/`eco`/`boost`) and a proper
  `translation_key`, plus `hvac_modes` ordered `[HEAT, (COOL), OFF]` so cards that hide
  modes after `OFF` (e.g. Mushroom thermostat) show the COOL button. *(thanks @jumlu)*
- Fully **async** integration (event-loop native; no executor round-trips for writes).

### New in 0.5

- **Presence-based child lock** with a simple on/off switch: pick the people who matter — child
  lock turns **on** when anyone is home and **off** when everyone leaves. "Home" is detected
  automatically, no extra knob.
- **Device / room exclusion filter** — hide individual Bosch devices or whole rooms from HA.
- **Scenarios as button entities** and an **opt-out toggle for the rawscan** diagnostic service.
- **Granular battery-level** diagnostic sensor, **Twinguard smoke-alarm** binary sensors,
  **Motion Detector II [+M]** support.
- **Reconfigure flow** — change the SHC host/IP or re-pair the certificate without deleting the
  integration.
- Many fixes: shutter/blinds direction on physical-switch & app operation, micromodules without a
  wall switch, climate `turn_off`/ECO/cooling, thread-safety, and entity-id hardening.

---

## Supported platforms

| Platform | Devices / features |
|---|---|
| `alarm_control_panel` | Intrusion Detection System |
| `binary_sensor` | Shutter Contact (Gen 1 + Gen 2), Motion Detector (Gen 1 + Gen 2 [+M]), Smoke Detector (Gen 1 + Gen 2), Smoke Detection System, Water Leakage Sensor, Shutter Contact 2 Plus (vibration), Twinguard smoke alarm, Battery state (all battery devices) |
| `button` | Micromodule Relay (impulse/momentary), Scenarios (optional), Smoke Detector self-test, Motion Detector II Walk/Detection Test (start/stop) & Reset Tamper |
| `climate` | Room Climate Control (thermostat valve groups), Heating Circuit |
| `cover` | Shutter Control (BBL), Micromodule Shutter, Micromodule Awning, Micromodule Blinds (with tilt) |
| `event` | Universal Switch (WRC2 / SWITCH2) button presses, Scenarios, Motion events, Smoke Detector & Smoke-Detection-System alarm events |
| `light` | LEDVANCE lights (on/off, brightness, color), Hue (via SHC), Micromodule Dimmer, Motion Detector II light |
| `number` | Thermostat temperature offset |
| `select` | Motion Detector II (motion sensitivity, smart-sensitivity comfort/security levels, orientation-light response time), Shutter Contact 2 Plus vibration sensitivity, Twinguard smoke sensitivity, thermostat/relay display & terminal config |
| `sensor` | Temperature, Humidity, CO₂/purity, Air-quality + rating (Twinguard), Energy + Power (Smart Plug / Compact, Light Control, Micromodule variants), Illuminance (Motion Detectors), Motion Detector II detection-test state & installation profile, EMMA grid power, Battery level (diagnostic, optional) |
| `switch` | Smart Plug, Smart Plug Compact, Light Control, Micromodule Relay, Camera Eyes / 360 / Outdoor Gen2 (privacy, light, notification), Presence Simulation, Bypass (Shutter Contact 2), Child Lock, Pet Immunity & Tamper Protection (Motion Detector II), Smart Sensitivity (Motion Detector II), Silent Mode (thermostat), Vibration detection, User-Defined States |
| `valve` | Thermostat radiator valve (position, diagnostic) |

## Services / actions

| Action | Description |
|---|---|
| `bosch_shc.trigger_scenario` | Trigger a scenario by name |
| `bosch_shc.trigger_rawscan` | Dump raw JSON from the SHC (devices, services, rooms, scenarios, …) — see [Creating a rawscan](#creating-a-rawscan-for-bug-reports) |
| `bosch_shc.smokedetector_check` | Trigger a self-test on a Smoke Detector entity |
| `bosch_shc.smokedetector_alarmstate` | Set the alarm state on a Smoke Detector entity |

Events are fired on the `bosch_shc.event` bus — button presses (Universal Switch: lower/upper,
short/long), scenario triggers, motion events, and smoke-detector alarms.

---

## Installation

### HACS (recommended)

1. Open **HACS → Integrations**, search for **Bosch SHC**, and install it.
2. Restart Home Assistant.
3. Go to **Settings → Devices & Services** and set up **Bosch SHC** (see below).

### Manual (no HACS)

1. Download the source zip of the [latest release](https://github.com/tschamm/boschshc-hass/releases/latest)
   and unzip it (the green **Code → Download ZIP** works too).
2. Copy `custom_components/bosch_shc/` into `<config>/custom_components/bosch_shc/`
   (create `custom_components/` if it doesn't exist).
3. Restart Home Assistant. On restart HA reads `manifest.json` and automatically
   pip-installs the pinned `boschshcpy` library — no manual Python steps needed,
   as long as the HA host has internet access.
4. Follow the setup steps below.

> **Updating:** delete the old `custom_components/bosch_shc/` folder first (so no stale
> files remain), copy the new one in, then restart. Your config entry and certificate
> pairing are preserved. Manual installs don't auto-update — re-copy the folder per release.

---

## Configuration

The SHC is auto-discovered via **zeroconf** — if it's on the same network it appears under
**Settings → Devices & Services → Discovered**. Otherwise add it with **+ Add integration →
Bosch SHC**.

### Initial setup

1. Press *Configure* on the discovered entry (or add it manually).
2. **Confirm host** — before submitting, press the button on the front of the SHC until the LEDs
   flash. This puts the controller into client-registration mode.
3. **System password** — the password you set when first setting up the SHC.
4. **Done** — the entry is created and all paired devices are discovered.

<img src='images/config_step1.png' alt='Discovered integration.' width='235pt'/>
<img src='images/config_step2.png' alt='Confirmation of host.' width='477pt'/>
<img src='images/config_step3.png' alt='Enter system password.' width='315pt'/>
<img src='images/config_step4.png' alt='Configuration complete.' width='474pt'/>

### Reconfigure (host change or certificate re-pair)

**Settings → Devices & Services → Bosch SHC → ⋮ → Reconfigure** offers:

- **Change host / IP** — update the SHC address without re-pairing.
- **Re-pair (regenerate certificate)** — full re-registration for a new SHC or after a factory reset.

### Options

**Settings → Devices & Services → Bosch SHC → Configure** — grouped into collapsible sections.
**Every option defaults to the previous behaviour, so existing setups are never changed.**

| Section | Option | What it does |
|---|---|---|
| **Features** | Scenarios as buttons | Expose each SHC scenario as a `button` entity (default off) |
| | Diagnostic entities | Create diagnostic sensors — battery level, valve-tappet, comm-quality (default on) |
| | Rawscan diagnostic service | Register `bosch_shc.trigger_rawscan`; turn off to hide it (default on) |
| **Presence-based child lock** | Enable | Master on/off switch |
| | Presence entities | Child lock turns **on** when **any** chosen entity is home, **off** when **all** are away. "Home" is auto-detected (`home` for person/device_tracker/zone/group, `on` for binary_sensor/input_boolean) |
| **Advanced** | Excluded devices / rooms | Hide specific Bosch devices or whole rooms — no entities are created for them |
| | Long-poll timeout | Seconds each long-poll waits before reconnecting (default 10) |
| | Verify SHC certificate hostname | Expert — the SHC cert hostname rarely matches its IP, so leave **off** |

### Translations

The configuration UI ships in **30 languages**: bg, ca, cs, de, el, en, es, es-419, et, fr, he,
hu, id, it, ja, ko, lv, nb, nl, no, pl, pt, pt-BR, ru, sk, sv, tr, uk, zh-Hans, zh-Hant.

---

## Quality

Targets the [Home Assistant **Bronze** quality scale](https://developers.home-assistant.io/docs/integration_quality_scale_index/).
All Bronze rules implemented except `brands` (pending in home-assistant/brands).
All assessed Silver rules complete; remaining Silver rules tracked in `quality_scale.yaml`.

- `local_push` IoT class — no cloud, no polling (camera-type devices update on poll only).
- Config flow with zeroconf discovery, re-auth, reconfigure and options flow.
- Unique config-entry enforcement, test-before-configure validation.
- `runtime_data`, `has_entity_name = True`, `PARALLEL_UPDATES` on every platform.
- Domain actions (`trigger_scenario`, `trigger_rawscan`) available even if an entry fails to load.
- CI: hassfest + HACS validation, unit tests, flake8/codespell/pip-audit, CodeQL, secret scan.

---

## Troubleshooting & bug reports

The fastest path to a fix is data. In order of convenience:

1. **Download diagnostics** — **Bosch SHC → ⋮ → Download diagnostics**. A redacted JSON snapshot
   of every device and its raw state (credentials, host/IP, MAC and serials removed automatically).
2. **A rawscan** of the misbehaving device — see below.
3. **Debug logs** captured while reproducing — see below.

Attach the file to your GitHub issue and remove anything you consider private.

### Creating a rawscan (for bug reports)

A rawscan is the raw JSON the controller exposes for a device — the single most useful thing to
attach when a device behaves wrong.

**1. Find the device id** — in **Developer Tools → Actions**:

```yaml
action: bosch_shc.trigger_rawscan
data:
  command: devices
```

Copy the device's `"id"` (e.g. `hdm:ZigBee:000d6f0000abcdef`).

**2. Dump that device's services:**

```yaml
action: bosch_shc.trigger_rawscan
data:
  command: device_services
  device_id: hdm:ZigBee:000d6f0000abcdef
```

Valid `command` values: `devices`, `device_services`, `device_service` (needs `device_id` +
`service_id`), `services`, `scenarios`, `rooms`, `information`, `public_information`,
`intrusion_detection`, `userdefinedstates`. For multiple controllers add `title: <name>`.

> The same data is available from the CLI via the `boschshc_rawscan` script shipped with
> [`boschshcpy`](https://github.com/tschamm/boschshcpy) (needs the client certificate + key).

### Enabling debug logs

**UI (easiest):** **Bosch SHC → ⋮ → Enable debug logging**, reproduce, then **Disable debug
logging** — HA downloads the log automatically (captures both the integration and `boschshcpy`).

**YAML:**

```yaml
logger:
  default: info
  logs:
    custom_components.bosch_shc: debug
    boschshcpy: debug
```

---

## Removal

1. **Bosch SHC → ⋮ → Delete**, then restart Home Assistant.
2. **Manual cleanup:** the client certificate/key files in `/config/bosch_shc/`
   (`bosch_shc-cert_<hostname>.pem` / `bosch_shc-key_<hostname>.pem`) are **not** removed
   automatically — delete them if you no longer use the integration.

## Known limitations

- Encrypted SSL private keys are not supported (a `requests` limitation).
- Not yet fully async (migration in progress).
- New devices on the SHC require reloading the integration before they appear in HA.
- The alarm control panel does not support a PIN code for arming/disarming.
- Client-certificate renewal is manual — a warning (log + notification) appears 30 days before
  expiry; after expiry, re-pair via the reconfigure flow.

---

## Maintainers & support

| Role | GitHub |
|---|---|
| Original author & maintainer | [@tschamm](https://github.com/tschamm) |
| Co-maintainer | [@mosandlt](https://github.com/mosandlt) |

Community discussion: [Bosch Smart Home thread](https://community.home-assistant.io/t/bosch-smart-home/115864)
on the Home Assistant forum. Bugs and feature requests:
[open an issue](https://github.com/tschamm/boschshc-hass/issues).

If this integration is useful to you, consider buying the maintainers a coffee — it's appreciated! ☕

[![Buy tschamm a coffee][buymecoffeebadge-tschamm]][buymecoffee-tschamm]
[![Buy mosandlts a coffee][buymecoffeebadge-mosandlts]][buymecoffee-mosandlts]

<!-- link references -->
[bosch_shc]: https://github.com/tschamm/boschshc-hass
[validateworkflow]: https://github.com/tschamm/boschshc-hass/actions/workflows/validate.yml
[validatebadge]: https://github.com/tschamm/boschshc-hass/actions/workflows/validate.yml/badge.svg
[testsworkflow]: https://github.com/tschamm/boschshc-hass/actions/workflows/tests.yml
[testsbadge]: https://github.com/tschamm/boschshc-hass/actions/workflows/tests.yml/badge.svg
[qualityworkflow]: https://github.com/tschamm/boschshc-hass/actions/workflows/quality.yml
[qualitybadge]: https://github.com/tschamm/boschshc-hass/actions/workflows/quality.yml/badge.svg
[hacs]: https://github.com/hacs/integration
[hacsbadge]: https://img.shields.io/badge/HACS-Default-orange.svg
[stars-shield]: https://img.shields.io/github/stars/tschamm/boschshc-hass
[buymecoffee-tschamm]: https://www.buymeacoffee.com/tschamm
[buymecoffeebadge-tschamm]: https://img.shields.io/badge/buy%20tschamm%20a%20double%20espresso-donate-yellow.svg
[buymecoffee-mosandlts]: https://buymeacoffee.com/mosandlts
[buymecoffeebadge-mosandlts]: https://img.shields.io/badge/buy%20mosandlts%20a%20coffee-donate-yellow.svg
