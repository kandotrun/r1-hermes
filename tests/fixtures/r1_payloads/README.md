# Rabbit R1/OpenClaw payload fixtures

This directory is for sanitized device capture samples used by parser tests.

Rules for adding captures:

- Replace gateway tokens, device tokens, QR secrets, API keys, and raw auth headers with obvious dummy values.
- Keep only the fields needed to exercise payload shape and alias compatibility.
- Do not include personal messages, account identifiers, exact network addresses, or full timestamps from a real device.
- Prefer one small JSON file per frame shape so parser regressions are easy to review.
