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
