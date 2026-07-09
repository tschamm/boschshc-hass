# Pre-commit / Pre-release Checklist (boschshc-hass)

Run through this before every commit and before cutting a release. Adapted from the
`pr-review-checklist-ha-core.md` built for the sibling `ha-core` PR work — this file is the HACS-side
counterpart. Add a new entry every time something costs a round-trip that this file could have
prevented; don't let it go stale.

---

## 0. Always run the existing local CI first

This repo already has strong automated tooling — use it before anything manual:

```
bash scripts/local-ci.sh hass   # ruff, codespell, pylint, quality-scale (gold), translations,
                                 # comment-length, pytest (3000+ tests)
bash scripts/local-ci.sh lib    # same, for the boschshcpy library if touching it
```

All 8 checks must pass. If `comment length` fails after adding/removing lines above an existing
flagged comment block, the line numbers in `scripts/comment_length_baseline.txt` have shifted —
update the shifted entries (diff old vs new failing line numbers), don't just re-add them
blindly at whatever new number the checker reports without confirming they're the *same* comment.

## 1. Pin verification before trusting any local check

This repo and `ha-core`'s bosch_shc work often share machines/sessions. Before trusting a mypy or
pytest result, confirm the installed `boschshcpy` matches `custom_components/bosch_shc/manifest.json`'s
pin:

```
pip show boschshcpy | head -2
grep '"requirements"' custom_components/bosch_shc/manifest.json
```

A clean result against the *wrong* (especially untyped/older) version proves nothing — same
lesson as `pr-review-checklist-ha-core.md` §19, generalized to this repo.

## 2. Enum comparisons

Use `is`/`is not`, never `==`/`!=`, for `*Service.State` enum members (identity comparison is both
correct — these are singletons — and matches the convention `ha-core`'s custom mypy plugin
enforces on any platform once it migrates there). Only exception: comparing against `.name`
(a string), where `==` is correct.

```
grep -rEn "(==|!=) *[A-Za-z_]+Service\.State\.[A-Z_]+" custom_components/bosch_shc/*.py | grep -v "\.name"
```

Should return nothing. Fixed across `binary_sensor.py`/`cover.py`/`light.py` 2026-07-08 —
recheck after adding new platforms/states.

## 3. Recorder: unrecorded high-cardinality attributes

Any `extra_state_attributes` entry holding a timestamp, counter, or other unique-per-event value
needs the entity class to declare `_unrecorded_attributes = frozenset({"key_name"})`, or every
poll/event writes a new `state_attributes` row to the recorder DB even when nothing user-visible
changed.

```
grep -n "_unrecorded_attributes" custom_components/bosch_shc/*.py
```

Cross-check against every `extra_state_attributes` implementation — bounded enum/category values
(state names, modes) don't need this; only unbounded/timestamp-like ones do. Fixed for
`MotionDetectionSensor`, `OccupancyDetectionSensor`, `TamperSensor`,
`NextSetpointTemperatureSensor` 2026-07-08 — recheck when adding a new `extra_state_attributes`
that returns a time value.

## 4. Test mock hygiene

- `patch.object(type(device), name, PropertyMock(...), create=True)` — not raw
  `setattr(type(device), name, ...)` — when overriding a property on an autospec'd
  (`create_autospec(..., instance=True)`) mock class. Needs `create=True` since the attribute
  isn't in the mock class's `__dict__`. Using `patch.object` (vs raw `setattr`) gets automatic
  teardown so the override doesn't leak into later tests reusing the same spec.
- `unittest.mock.patch()` on an async function auto-creates an `AsyncMock` since Python 3.8 —
  don't add an explicit `AsyncMock` import/wrapper unless you've confirmed the plain `patch()`
  actually fails first (it usually doesn't).

## 5. Docstring / comment accuracy

- Every `__init__`/class docstring names the actual entity/class it's on — watch for copy-paste
  drift when adding a new sensor/switch/binary_sensor by copying an existing one.
- Comments explain WHY, one line, no design-memo paragraphs — this repo already gates this via
  `scripts/check-comment-length.py`, so violations should be rare; if the gate passes but a
  comment still restates the code instead of explaining a non-obvious constraint, fix it anyway
  (the gate catches length, not content quality).

## 6. Error handling

- Setup: `ConfigEntryNotReady` (transient) vs `ConfigEntryAuthFailed`/`ConfigEntryError`
  (permanent) — already followed in `__init__.py`, keep it that way for new setup paths.
- Actions/services: `ServiceValidationError` (user error) vs `HomeAssistantError` (device/comms
  error) — don't put raw/stringified library exception text into a translated user-facing
  message; log the detail, translate only the summary.
- Prefer `try/except/else` over a large `try` block: only the call(s) that can actually raise go
  in `try`; anything using the result on the happy path goes in `else`.
- Don't hardcode a unit (e.g. `"°C"`) in a log message or comparison when the domain/device
  supports more than one — read the real unit off the device/entity instead.

## 7. Platinum-tier patterns worth adopting (2026-07-08 sample scan)

Sampled a handful of Platinum-quality-scale `ha-core` integrations (androidtv_remote, smlight,
watts, liebherr) for patterns worth copying here if/when the relevant code path is touched:

- Options flow: `OptionsFlowWithReload` as the base class instead of hand-rolled
  `add_update_listener` + manual `async_reload()`.
- Custom entity services: a dedicated `services.py` using
  `service.async_register_platform_entity_service(...)`, registered once from `async_setup()`
  (which runs once per process, not per config-entry reload — no "already registered" guard
  needed).
- Device/coordinator cleanup ordering: shut a coordinator down *before* unlinking it from the
  device registry (not after), diff stale devices against the **device registry**, not an
  in-memory coordinators dict (a device removed while HA was offline never had a coordinator this
  session), and remember `async_update_device(remove_config_entry_id=...)` only unlinks — the
  device row itself is removed later, debounced. This integration doesn't use a
  `DataUpdateCoordinator` (push/long-poll model), so these specific device-cleanup mechanics don't
  directly apply, but the *ordering* principle (shutdown before unlink, diff against the
  authoritative registry not a local cache) is general.
- Full detail + PR citations: `/home/thomas/projects/bosch shc/pr-review-checklist-ha-core.md` §11–20.

## 8. Before a release

1. `bash scripts/local-ci.sh all` (hass + lib) — must be 100% green.
2. `manifest.json`'s `"version"` matches the version in the release commit/tag.
3. `CHANGELOG.md` entry accurately describes what changed (not what changed in a *previous*
   release — easy to drift when a release commit gets amended/extended before tagging).
4. Confirm no uncommitted/untracked files are being silently left out of the release
   (`git status`) — check untracked files aren't accidentally-important, not just noise.
5. Remember: pushing to `tschamm/boschshc-hass` needs either explicit user confirmation per-push
   or the user running it themselves — don't assume a prior push authorization carries forward.

---

## Meta

- Sibling checklist: `/home/thomas/projects/bosch shc/pr-review-checklist-ha-core.md` (ha-core PR side).
- This file should be updated any time a review/CI/self-caught issue here would have been
  prevented by knowing something in advance — same standing rule as the ha-core checklist.
