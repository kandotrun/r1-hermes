# Security Policy

## Supported Versions

`r1-hermes` currently supports Python 3.10 and newer. The GitHub Actions CI matrix
exercises Python 3.10, 3.11, and 3.12 before release-facing changes are merged.

## Reporting a Vulnerability

Report suspected vulnerabilities privately. Prefer GitHub private vulnerability reporting or a
draft GitHub Security Advisory when available. If neither is available to you, open a minimal
GitHub issue asking for a private maintainer contact and omit exploit details.

Do not include gateway tokens, device tokens, QR payload contents, API keys, raw authorization
headers, logs containing secrets, or real Rabbit R1 captures in public reports.

Authentication bypasses, token disclosure, unsafe network exposure, shell injection, and command
execution boundary failures are handled as release blockers.

## Release Artifact Security

GitHub release artifacts must include wheel and sdist distributions, `SHA256SUMS`, dependency
reports, and GitHub release provenance where available. Verify release downloads with `SHA256SUMS`
and, when supported, `gh attestation verify` before installing on a gateway host.

Release archives and dependency reports must not contain gateway tokens, device tokens, QR payload
JSON, QR PNG files, raw authorization headers, `.env` files, `.r1-hermes/` state, `devices.json`,
or `device-token-hmac.key`. If any GitHub release artifacts or workflow logs expose those values,
treat the release as compromised, rotate the gateway token, revoke affected device tokens, and
regenerate pairing QR files.
