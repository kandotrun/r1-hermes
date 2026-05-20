from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_dev_extra_includes_audit_and_build_tools() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert "pip-audit>=" in pyproject
    assert "build>=" in pyproject


def test_ci_runs_dependency_audit() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "python -m pip_audit" in workflow
    assert "--strict" in workflow
    assert "--progress-spinner off" in workflow


def test_ci_builds_and_installs_distributions() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "python -m build" in workflow
    assert "dist/*.whl" in workflow
    assert "dist/*.tar.gz" in workflow
    assert "RUNNER_TEMP/r1-hermes-wheel" in workflow
    assert "RUNNER_TEMP/r1-hermes-sdist" in workflow
    assert "python -m pip check" in workflow


def test_ci_does_not_upload_distribution_artifacts() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "upload-artifact" not in workflow
