# Rabbit R1/OpenClaw payload fixtures

This directory is for sanitized device capture samples used by parser tests.

Use the one-command sanitizer workflow in [`../../../docs/capture-replay.md`](../../../docs/capture-replay.md)
before committing anything derived from a real device capture.

Rules for adding captures:

- Replace gateway tokens, device tokens, QR secrets, API keys, and raw auth headers with obvious dummy values.
- Keep only the fields needed to exercise payload shape and alias compatibility.
- Do not include personal messages, account identifiers, exact network addresses, or full timestamps from a real device.
- Do not paste raw captures or QR payload JSON into public issues, PRs, chat, logs, or screenshots.
- Prefer one small JSON file per frame shape so parser regressions are easy to review.

Current fixtures cover the official helper QR payload shape, the standard helper `connect` shape,
OpenClaw/community `gateway.connect` variants, `chat.send` alias parsing, `chat.history`
session-key alias parsing, text content parts, accepted image content parts, unsupported media
content parts, and sanitized camera media flow fixtures. These are sanitized compatibility samples,
not raw captures; unsupported media fixtures use dummy placeholder strings or the tiny public
`r1-image` base64 sentinel, and accepted-image tests use a tiny public PNG.
