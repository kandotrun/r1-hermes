# Release packaging

This project is public release software for a gateway that can pair with a physical Rabbit R1.
Release handling must keep authentication material, local pairing state, and QR payloads out of
artifacts and logs.

## Versioning policy

`r1-hermes` uses SemVer tags in the form `vMAJOR.MINOR.PATCH`. `pyproject.toml` is the version source of truth.
A release tag must match the project version exactly, for example `v0.1.0` for
`version = "0.1.0"`.

Use patch releases for compatible bug fixes and documentation-only corrections. Use minor releases
for compatible CLI options, protocol compatibility improvements, and new security controls that
preserve hardened defaults. Reserve major releases for breaking CLI, state format, pairing, or
runtime behavior changes. Authentication bypass fixes, token disclosure fixes, unsafe network
exposure fixes, shell injection fixes, and command execution boundary fixes are security releases
even when the public API does not change.

## Release workflow

The GitHub release workflow runs on `v*.*.*` tags and on manual `workflow_dispatch` dry runs. It
builds from a clean checkout, verifies that a tag matches `pyproject.toml`, builds both
distributions, inspects archive contents, creates dependency reports, writes checksums, and asks
GitHub to create provenance attestations.

Local release-equivalent build:

```bash
rm -rf dist build
python -m build --sdist --wheel
```

Expected public artifacts:

- `r1_hermes-<version>-py3-none-any.whl`
- `r1_hermes-<version>.tar.gz`
- `SHA256SUMS`
- `r1-hermes-dependencies.txt`
- `r1-hermes-pip-inspect.json`
- GitHub artifact attestations for the uploaded files

The wheel contains the packaged systemd user-service templates under `r1_hermes/systemd/`, and the
sdist contains both those packaged copies and the source-tree copies under `packaging/systemd/`.
Release inspection fails if `r1_hermes/systemd/r1-hermes.service`,
`r1_hermes/systemd/r1-hermes.env.example`, `packaging/systemd/r1-hermes.service`, or
`packaging/systemd/r1-hermes.env.example` is missing from the artifact where it belongs.

Before publishing a tag, run the full local validation from `docs/development-checks.md`, including
`python -m pytest -q`, `python -m ruff check .`, `python -m compileall -q src tests`, and the build
and install smoke checks.

## Installing a release

Download the wheel and `SHA256SUMS` from the GitHub release page, then verify checksums before
installing:

```bash
sha256sum -c SHA256SUMS
pip install ./r1_hermes-<version>-py3-none-any.whl[qr]
r1-hermes --help
```

To install the systemd user service from a release artifact without cloning the repository, use the
packaged installer helper:

```bash
r1-hermes install-systemd-user
```

That writes `~/.config/systemd/user/r1-hermes.service` and
`~/.config/r1-hermes/r1-hermes.env`, refuses to overwrite existing files unless `--overwrite` is
set, and keeps the env example secret-free. Edit the env file locally and replace only the
placeholder token value with a freshly generated gateway token. Do not paste real gateway tokens,
QR payloads, or Rabbit R1 device IDs into release issues, PRs, or support logs.

When GitHub artifact attestation verification is available in your environment, verify provenance
against this repository before installing:

```bash
gh attestation verify ./r1_hermes-<version>-py3-none-any.whl --repo kandotrun/r1-hermes
gh attestation verify ./r1_hermes-<version>.tar.gz --repo kandotrun/r1-hermes
```

Use the sdist only when your deployment intentionally rebuilds from source:

```bash
sha256sum -c SHA256SUMS
pip install ./r1_hermes-<version>.tar.gz[qr]
```

Editable installs are for development and local agent handoff, not production release installs:

```bash
git clone https://github.com/kandotrun/r1-hermes.git
cd r1-hermes
pip install -e '.[dev,qr]'
```

## Dependency transparency

`r1-hermes-dependencies.txt` is a `pip freeze --all` report from a temporary environment with the
built wheel installed. `r1-hermes-pip-inspect.json` is the matching structured `pip inspect --local`
report. These files are not a vulnerability scan by themselves; use them to audit exactly what the
release workflow resolved for install smoke testing, and run your own advisory tooling for your
deployment environment.

## Secret and state exclusions

Do not upload gateway tokens, device tokens, QR payload JSON, QR PNG files, `.env` files, local
state directories, ready files, logs, or real Rabbit R1 captures as release artifacts. Release
archives must exclude `.env`, `.r1-hermes/`, `devices.json`, `device-token-hmac.key`, and `r1-hermes-secret*.png`.
`MANIFEST.in` and the release workflow both enforce those exclusions for the sdist, wheel, and
generated artifact checks.

If a release artifact, workflow log, checksum file, dependency report, or PR comment includes
gateway tokens, device tokens, QR payload JSON, raw authorization headers, or real device captures,
treat it as a credential incident: delete the artifact where possible, rotate the affected gateway
token, revoke paired device tokens, regenerate the QR, and document the incident privately.
