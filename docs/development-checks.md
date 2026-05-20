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
sdist_venv="${TMPDIR:-/tmp}/r1-hermes-sdist"
python -m venv "$sdist_venv"
"$sdist_venv/bin/python" -m pip install --upgrade pip
"$sdist_venv/bin/python" -m pip install dist/*.tar.gz
"$sdist_venv/bin/python" -m pip check
"$sdist_venv/bin/r1-hermes" --help
```

The two audit commands cover different failure modes. The project audit resolves the dependency
ranges declared in `pyproject.toml` and fails on vulnerable resolved versions or dependency
collection errors. The local audit checks the installed CI/development environment while skipping
the editable `r1-hermes` package itself, which is not published to PyPI during local development.

Do not add broad `pip-audit --ignore-vuln` suppressions for convenience. If an advisory appears,
prefer tightening the affected dependency range or upgrading the package. Use a narrow ignore only
when the advisory is demonstrably unreachable in this gateway, include a code comment or issue link
with the rationale, and remove it as soon as an upstream fix is available.

The build checks intentionally keep `dist/` local and do not upload distributions as CI artifacts.
Distributions, virtual environments, QR PNGs, state directories, environment files, and logs can
contain local state or secrets and should not be attached to issues or pull requests.
