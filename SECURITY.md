# Security Policy

## Supported versions

Only the latest released version of `bosch_shc` (this HACS integration) is
supported. Please reproduce any issue on the current release before reporting.

## Reporting a vulnerability

**Please do not open a public issue for security problems.**

Report privately via GitHub's **"Report a vulnerability"** button under the
repository's *Security* tab (Security Advisories), or contact the maintainer
[@tschamm](https://github.com/tschamm) directly. We'll acknowledge the report
and work with you on a fix and coordinated disclosure.

## Scope / context

This integration talks to the Bosch Smart Home Controller **only over the local
network**, using a mutual-TLS client certificate (no cloud component). There is
no external/cloud attack surface in this project itself.

When reporting, please **never include** secrets or personal data — no client
certificates/keys, controller passwords, home IP addresses/SSIDs, or real device
serials/cloud IDs. Use redacted/fake values in any logs or rawscans you attach.
