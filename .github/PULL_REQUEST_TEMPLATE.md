## What and why

<!-- Describe what this PR changes and why. -->

## Related issue

<!-- Reference the issue with bare #N or "Addresses #N".
     NEVER use "Fixes", "Closes", or "Resolves" — GitHub auto-closes the issue on
     merge/push before the reporter confirms the fix. -->

Addresses #

## Checklist

- [ ] `scripts/local-ci.sh` passes locally
- [ ] `flake8` is clean (`pipx run flake8 --max-line-length=88 --extend-ignore=E501,W503 <changed files>`)
- [ ] Tests added or updated in `tests/bosch_shc/` for behavioral changes
- [ ] If this requires a `boschshcpy` lib change: lib PR is opened/merged and `manifest.json` pin is bumped
- [ ] No PII, no real SHC serial numbers, no credentials in commits or PR description
