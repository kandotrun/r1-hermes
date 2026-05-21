# Development checks

Run the local checks below before opening a release or CI hardening pull request:

```bash
python -m pip install -e '.[dev,qr]'
python -m pytest -q
python -m ruff check .
python -m compileall -q src tests
python -m pip_audit . --strict --progress-spinner off
python -m pip_audit --local --progress-spinner off --skip-editable
rm -rf dist build
python -m build --sdist --wheel
wheel_venv="${TMPDIR:-/tmp}/r1-hermes-wheel"
python -m venv "$wheel_venv"
"$wheel_venv/bin/python" -m pip install --upgrade pip
"$wheel_venv/bin/python" -m pip install dist/*.whl
"$wheel_venv/bin/python" -m pip check
"$wheel_venv/bin/r1-hermes" --help
wheel_artifacts=(dist/*.whl)
wheel_qr_venv="${TMPDIR:-/tmp}/r1-hermes-wheel-qr"
python -m venv "$wheel_qr_venv"
"$wheel_qr_venv/bin/python" -m pip install --upgrade pip
"$wheel_qr_venv/bin/python" -m pip install "${wheel_artifacts[0]}[qr]"
qr_smoke_token="$("$wheel_qr_venv/bin/python" -c 'import secrets; print(secrets.token_urlsafe(32))')"
qr_output="${TMPDIR:-/tmp}/r1-hermes-wheel-qr.png"
qr_log="${TMPDIR:-/tmp}/r1-hermes-wheel-qr.log"
rm -f "$qr_output" "$qr_log"
"$wheel_qr_venv/bin/r1-hermes" qr --host 127.0.0.1 --port 18789 --protocol ws --token "$qr_smoke_token" --output "$qr_output" >"$qr_log" 2>&1
"$wheel_qr_venv/bin/python" - "$qr_output" "$qr_log" "$qr_smoke_token" <<'PY'
import sys
from pathlib import Path

qr_output = Path(sys.argv[1])
qr_log = Path(sys.argv[2])
qr_smoke_token = sys.argv[3]
assert qr_output.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
output = qr_log.read_text(encoding="utf-8")
assert qr_smoke_token not in output
assert '"token"' not in output
assert "clawdbot-gateway" not in output
PY
sdist_venv="${TMPDIR:-/tmp}/r1-hermes-sdist"
python -m venv "$sdist_venv"
"$sdist_venv/bin/python" -m pip install --upgrade pip
"$sdist_venv/bin/python" -m pip install dist/*.tar.gz
"$sdist_venv/bin/python" -m pip check
"$sdist_venv/bin/r1-hermes" --help
sdist_artifacts=(dist/*.tar.gz)
sdist_qr_venv="${TMPDIR:-/tmp}/r1-hermes-sdist-qr"
python -m venv "$sdist_qr_venv"
"$sdist_qr_venv/bin/python" -m pip install --upgrade pip
"$sdist_qr_venv/bin/python" -m pip install "${sdist_artifacts[0]}[qr]"
qr_smoke_token="$("$sdist_qr_venv/bin/python" -c 'import secrets; print(secrets.token_urlsafe(32))')"
qr_output="${TMPDIR:-/tmp}/r1-hermes-sdist-qr.png"
qr_log="${TMPDIR:-/tmp}/r1-hermes-sdist-qr.log"
rm -f "$qr_output" "$qr_log"
"$sdist_qr_venv/bin/r1-hermes" qr --host 127.0.0.1 --port 18789 --protocol ws --token "$qr_smoke_token" --output "$qr_output" >"$qr_log" 2>&1
"$sdist_qr_venv/bin/python" - "$qr_output" "$qr_log" "$qr_smoke_token" <<'PY'
import sys
from pathlib import Path

qr_output = Path(sys.argv[1])
qr_log = Path(sys.argv[2])
qr_smoke_token = sys.argv[3]
assert qr_output.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
output = qr_log.read_text(encoding="utf-8")
assert qr_smoke_token not in output
assert '"token"' not in output
assert "clawdbot-gateway" not in output
PY
```

The two audit commands cover different failure modes. The project audit resolves the dependency
ranges declared in `pyproject.toml` and fails on vulnerable resolved versions or dependency
collection errors. The local audit checks the installed CI/development environment while skipping
the editable `r1-hermes` package itself, which is not published to PyPI during local development.

Do not add broad `pip-audit --ignore-vuln` suppressions for convenience. If an advisory appears,
prefer tightening the affected dependency range or upgrading the package. Use a narrow ignore only
when the advisory is demonstrably unreachable in this gateway, include a code comment or issue link
with the rationale, and remove it as soon as an upstream fix is available.

The QR extra smoke checks intentionally install fresh wheel and sdist environments with the
documented `[qr]` extra, run `r1-hermes qr --host 127.0.0.1 --port 18789 --protocol ws`, verify a
PNG signature, and inspect captured output for generated token or payload JSON leakage. Generate
the smoke token inside the temporary environment and do not add `--print-payload` to release or CI
smoke checks.

The build checks intentionally keep `dist/` local and do not upload distributions as CI artifacts.
Distributions, virtual environments, QR PNGs, state directories, environment files, and logs can
contain local state or secrets and should not be attached to issues or pull requests.
