from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
RELEASE_DOC = ROOT / "docs" / "release.md"
README = ROOT / "README.md"
SECURITY = ROOT / "SECURITY.md"
MANIFEST = ROOT / "MANIFEST.in"

SKIP_SOURCE_COPY = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".serena",
    ".symphony",
    ".venv",
    "build",
    "dist",
}

FORBIDDEN_ARCHIVE_NAME_PARTS = (
    ".env",
    ".r1-hermes",
    "device-token-hmac",
    "devices.json",
    "r1-hermes-secret",
    "r1-hermes.ready",
    "tests/fixtures/r1_payloads",
)

SECRET_EXCLUSION_DOC_TEXT = (
    "`.env`, `.r1-hermes/`, `devices.json`, `device-token-hmac.key`, and "
    "`r1-hermes-secret*.png`"
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _copy_worktree_for_build(destination: Path) -> None:
    git = shutil.which("git")
    assert git is not None
    result = subprocess.run(  # noqa: S603 - trusted git read of this repository's file list.
        [git, "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
    )
    for raw_name in result.stdout.split(b"\0"):
        if not raw_name:
            continue
        relative_path = Path(os.fsdecode(raw_name))
        if relative_path.parts and relative_path.parts[0] in SKIP_SOURCE_COPY:
            continue
        source = ROOT / relative_path
        if not source.is_file():
            continue
        target = destination / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _write_dummy_local_state(source_tree: Path) -> None:
    dummy_text_files = {
        ".env": "R1_HERMES_GATEWAY_TOKEN=dummy-test-token\n",
        ".env.local": "R1_HERMES_GATEWAY_TOKEN=dummy-test-token\n",
        ".r1-hermes/devices.json": "{}\n",
        ".r1-hermes/device-token-hmac.key": "dummy-test-key\n",
        "r1-hermes.ready": "ready\n",
        "src/r1_hermes/.env": "R1_HERMES_GATEWAY_TOKEN=dummy-test-token\n",
        "src/r1_hermes/devices.json": "{}\n",
    }
    for relative_name, content in dummy_text_files.items():
        path = source_tree / relative_name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    for relative_name in ("r1-hermes-secret.png", "src/r1_hermes/r1-hermes-secret.png"):
        path = source_tree / relative_name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"dummy png bytes")


def _archive_names(archive_path: Path) -> list[str]:
    if archive_path.suffix == ".whl":
        with zipfile.ZipFile(archive_path) as wheel:
            return wheel.namelist()
    with tarfile.open(archive_path, "r:gz") as sdist:
        return sdist.getnames()


def test_release_workflow_builds_auditable_artifacts_from_tags() -> None:
    workflow = _read(RELEASE_WORKFLOW)

    for required in (
        "workflow_dispatch:",
        "tags:",
        "v*.*.*",
        "contents: write",
        "id-token: write",
        "attestations: write",
        "actions/checkout@v4",
        "fetch-depth: 0",
        "git status --short",
        "GITHUB_REF_NAME",
        "python -m build --sdist --wheel",
        "pip inspect --local",
        "r1-hermes-dependencies.txt",
        "SHA256SUMS",
        "sha256sum",
        "actions/attest-build-provenance",
        "gh release create",
        "dist/*.whl",
        "dist/*.tar.gz",
    ):
        assert required in workflow

    assert "pull_request" not in workflow
    assert "R1_HERMES_GATEWAY_TOKEN" not in workflow
    assert "--print-payload" not in workflow


def test_manifest_excludes_secret_local_state_and_fixture_payloads() -> None:
    manifest = _read(MANIFEST)

    for required in (
        "prune .r1-hermes",
        "prune .venv",
        "prune build",
        "prune dist",
        "prune packaging",
        "prune tests",
        "global-exclude .env",
        "global-exclude .env.*",
        "global-exclude r1-hermes-secret*.png",
        "global-exclude devices.json",
        "global-exclude device-token-hmac.key",
        "global-exclude *.ready",
    ):
        assert required in manifest


def test_wheel_and_sdist_exclude_local_secret_state(tmp_path: Path) -> None:
    source_tree = tmp_path / "source"
    dist_dir = tmp_path / "dist"
    source_tree.mkdir()
    _copy_worktree_for_build(source_tree)
    _write_dummy_local_state(source_tree)

    subprocess.run(  # noqa: S603 - trusted test build command against a temporary source tree.
        [
            sys.executable,
            "-m",
            "build",
            "--sdist",
            "--wheel",
            "--outdir",
            str(dist_dir),
        ],
        cwd=source_tree,
        check=True,
    )

    wheel_path = next(dist_dir.glob("*.whl"))
    sdist_path = next(dist_dir.glob("*.tar.gz"))
    wheel_names = _archive_names(wheel_path)
    sdist_names = _archive_names(sdist_path)
    all_archive_names = [name.replace("\\", "/") for name in (*wheel_names, *sdist_names)]

    for forbidden_part in FORBIDDEN_ARCHIVE_NAME_PARTS:
        assert not any(forbidden_part in name for name in all_archive_names), forbidden_part

    assert "r1_hermes/cli.py" in wheel_names
    assert any(name.endswith(".dist-info/METADATA") for name in wheel_names)
    assert any(name.endswith("/pyproject.toml") for name in sdist_names)
    assert any(name.endswith("/README.md") for name in sdist_names)
    assert any(name.endswith("/docs/release.md") for name in sdist_names)
    assert not any("/tests/" in name for name in sdist_names)

    metadata_name = next(name for name in wheel_names if name.endswith(".dist-info/METADATA"))
    with zipfile.ZipFile(wheel_path) as wheel:
        metadata = wheel.read(metadata_name).decode("utf-8")

    assert "Name: r1-hermes" in metadata
    assert "Requires-Python: >=3.10" in metadata
    assert "Requires-Dist: aiohttp" in metadata
    assert "Provides-Extra: qr" in metadata


def test_release_docs_cover_versioning_install_verification_and_secret_handling() -> None:
    release_doc = _read(RELEASE_DOC)
    readme = _read(README)
    security = _read(SECURITY)

    for required in (
        "vMAJOR.MINOR.PATCH",
        "`pyproject.toml` is the version source of truth",
        "python -m build --sdist --wheel",
        "r1_hermes-<version>-py3-none-any.whl",
        "SHA256SUMS",
        "sha256sum -c SHA256SUMS",
        "gh attestation verify",
        "pip install ./r1_hermes-<version>-py3-none-any.whl[qr]",
        "pip install -e '.[dev,qr]'",
        "Do not upload gateway tokens, device tokens, QR payload JSON, QR PNG files",
        SECRET_EXCLUSION_DOC_TEXT,
    ):
        assert required in release_doc

    assert "docs/release.md" in readme
    assert "GitHub release artifacts" in security
    assert "SHA256SUMS" in security
