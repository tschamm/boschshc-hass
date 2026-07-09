# Contributing

Thanks for taking the time to contribute to the Bosch Smart Home Controller integration!

## Reporting issues

- Use the [issue tracker](https://github.com/tschamm/boschshc-hass/issues) and pick the matching template.
- Include HA version, integration version (from HACS), boschshcpy version, and SHC generation (SHC 1 or SHC 2).
- For bugs, attach DEBUG-level logs for `custom_components.bosch_shc` and `boschshcpy`.
- For device-related bugs, trigger a rawscan first (see below) and attach the output.
- **No PII, no real SHC serial numbers, no credentials** in issues or PRs. Use redacted/placeholder values.

### Enabling debug logging

In HA Developer Tools → Services, call:

```yaml
service: logger.set_level
data:
  custom_components.bosch_shc: debug
  boschshcpy: debug
```

Or use the UI: **Settings → Devices & Services → Bosch Smart Home Controller → Enable debug logging**.

### Collecting a rawscan

In HA Developer Tools → Services, call:

```yaml
service: bosch_shc.trigger_rawscan
```

This writes a JSON dump of the live SHC device state to the HA log. Attach the output to your issue.

## Pull requests

1. Fork the repo and create a focused feature branch.
2. Keep changes focused — one logical change per PR.
3. Reference the issue number in the PR body using bare `#N` or `Addresses #N`.
   **Never use `Fixes`, `Closes`, or `Resolves` #N** — GitHub auto-closes the issue on merge/push before the reporter confirms the fix.
4. Add or update tests in `tests/bosch_shc/` for behavioral changes.
5. Run the local CI gate before opening the PR (see below).
6. No PII, no real SHC serials, no credentials in commits or PR descriptions.

### Fork-based PR flow

```bash
# Add the upstream and your fork as remotes
git remote add upstream https://github.com/tschamm/boschshc-hass.git
git remote add fork https://github.com/<your-user>/boschshc-hass.git

# Create a branch from upstream master
git fetch upstream
git checkout -b my-fix upstream/master

# ... make changes ...

# Push to your fork and open a PR against tschamm/boschshc-hass master
git push fork my-fix
gh pr create --repo tschamm/boschshc-hass --base master --head <your-user>:my-fix
```

## Local CI gate

Run before every push:

```bash
scripts/local-ci.sh [lib|hass|all]
```

This mirrors the GitHub Actions checks (compile, tests, lint, optional build). Get it green before opening a PR.

## Linting

CI enforces `ruff` (lint + format), `pylint`, `mypy`, and `codespell` against
`custom_components/`. Run the full local gate before opening a PR:

```bash
scripts/local-ci.sh hass
```

Or run the linters individually:

```bash
ruff check custom_components
ruff format --check custom_components
pylint --rcfile=pyproject.toml custom_components/bosch_shc
mypy custom_components/bosch_shc/
```

## Running tests

Unit tests under `tests/bosch_shc/` can be run locally without a full HA harness:

```bash
PYTHONPATH="<path-to-boschshc-hass>:<path-to-boschshcpy>" \
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python3 -m pytest tests/bosch_shc/<file> -q -o addopts=""
```

Tests for `config_flow` and `device_trigger` require the full HA test harness — let upstream CI run those (they run on every PR via GitHub Actions).

## Library changes

If your fix requires a change to [boschshcpy](https://github.com/tschamm/boschshcpy):

1. Open a PR against `tschamm/boschshcpy` first.
2. Wait for a new PyPI release.
3. Bump `requirements` in `custom_components/bosch_shc/manifest.json` to the new version.
4. Reference both PRs in each other.

## Releases

Releases are cut by the maintainers. Do not bump `manifest.json` version or edit `CHANGELOG.md` in feature PRs — that is handled as part of the release.
