# Release process — boschshc-hass (+ boschshcpy)

Two repos: `boschshc-hass` (HACS integration) pins `boschshcpy` (PyPI lib) in `manifest.json`.

## CI (must stay green on master)
boschshc-hass: `validate.yml` (hassfest + HACS), `quality.yml` (flake8 + codespell + pip-audit, scoped to `custom_components/`), `tests.yml` (pytest unit leg), `codeql.yml` / `dependency-review.yml` / `secret-scan.yml`.
boschshcpy: `tests.yml` (pytest matrix 3.11–3.13), `publish.yml` (OIDC Trusted Publishing, 2-job build→publish, `environment: pypi`, `skip-existing`).
Local gate (mirrors remote): `scripts/local-ci.sh [lib|hass|all]` — get GREEN before any push/tag/release.

## Hard rules
- POST_FIX_BUGHUNT: every fix gets 2 independent read-only reviews vs the API spec before shipping.
- NEVER_BLIND_FIX: verify against `bosch-shc-api-docs/` + rawscan before touching shared code.
- VERIFY_PIP_MIRRORED: after publishing a lib version, confirm it resolves cache-cold on ≥2 independent systems (`pip index versions boschshcpy`) BEFORE bumping the manifest pin. PyPI CDN edges mirror unevenly; an unmirrored pin breaks setup elsewhere.
- NEVER auto-close: no `Fix/Closes #N` in commits/PRs touching master (GitHub auto-closes before the reporter confirms). Use bare `#N`. Close manually after confirmation.
- SQUASH_RELEASE: each release lands as clean squashed commit(s); never force-push shared master.

## Lib release (boschshcpy X.Y.Z)
1. Build fixes in an isolated worktree off the latest tag; tests + flake8 GREEN.
2. Bump `setup.py` version; commit (author + `Co-Authored-By`), tag `vX.Y.Z`, push master + tag.
3. `pipx run --spec build pyproject-build` → `pipx run twine upload -r boschshcpy dist/*` (until the OIDC `push: tags` trigger is armed; then a tag push publishes automatically, `skip-existing` keeps it green).
4. VERIFY_PIP_MIRRORED on ≥2 systems.

## Integration release (boschshc-hass X.Y.Z)
1. Build fixes in an isolated worktree off master; full unit suite + flake8 GREEN (config_flow/device_trigger need the HA harness → upstream CI).
2. Bump `manifest.json` `version` (+ `requirements` pin only after the lib is mirror-verified).
3. Commit, tag `vX.Y.Z`, push master + tag, create the GitHub release (HACS serves it).
4. Optional deploy to a live SHC: backup → rsync `custom_components/bosch_shc/` → `ha core restart` → ~75 s log watchdog → confirm integration `loaded` (MCP `ha_get_integration`).
