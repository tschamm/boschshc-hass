## What and why

<!-- Describe what this PR changes and why. -->

## Related issue

<!-- Reference the issue with bare #N or "Addresses #N".
     NEVER use "Fixes", "Closes", or "Resolves" — GitHub auto-closes the issue on
     merge/push before the reporter confirms the fix. -->

Addresses #

## Checklist

- [ ] `scripts/local-ci.sh` passes locally (ruff, pylint, mypy, tests)
- [ ] Tests added or updated in `tests/bosch_shc/` for behavioral changes
- [ ] If this requires a `boschshcpy` lib change: lib PR is opened/merged and `manifest.json` pin is bumped
- [ ] No PII, no real SHC serial numbers, no credentials in commits or PR description
