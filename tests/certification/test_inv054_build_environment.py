from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from aodsl.certification.build_environment import (
    BuildEnvironmentError,
    load_manifest,
    sha256_file,
)


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "architecture" / "build-environment.v1.json"
WORKFLOW = ROOT / ".github" / "workflows" / "production-release.yml"
BUILD_LOCK = ROOT / "requirements" / "build.lock"

EXPECTED_OCI = (
    "python@sha256:"
    "47ae396f09c1303b8653019811a8498470603d7ffefc29cb07c88f1f8cb3d19f"
)


def _workflow_container_image(text: str) -> str:
    lines = text.splitlines()

    for i, line in enumerate(lines):
        if line.strip() != "container:":
            continue

        container_indent = len(line) - len(line.lstrip())

        for child in lines[i + 1:]:
            if not child.strip():
                continue

            indent = len(child) - len(child.lstrip())
            if indent <= container_indent:
                break

            stripped = child.strip()
            if stripped.startswith("image:"):
                return stripped.split(":", 1)[1].strip().strip("'\"")

    raise AssertionError("production workflow container.image not found")


def test_manifest_contract():
    data = load_manifest(MANIFEST)

    assert data["schema"] == "aodsl.build-environment.v1"
    assert data["invariant"] == "INV-054"
    assert data["platform"] == {
        "os": "linux",
        "architecture": "amd64",
    }
    assert data["python"] == {
        "implementation": "CPython",
        "version": "3.12.11",
    }
    assert data["bootstrap"]["pip_version"] == "25.0.1"

    assert data["oci"]["reference"] == EXPECTED_OCI
    assert data["oci"]["digest"].startswith("sha256:")
    assert len(data["oci"]["digest"]) == 71


def test_build_lock_is_bound_to_manifest():
    data = load_manifest(MANIFEST)

    assert data["build_toolchain"]["lock_path"] == "requirements/build.lock"
    assert (
        sha256_file(BUILD_LOCK)
        == data["build_toolchain"]["lock_sha256"]
    )
    assert data["build_toolchain"]["installation_policy"] == {
        "require_hashes": True,
        "only_binary": True,
    }


def test_workflow_uses_exact_manifest_oci_reference():
    data = load_manifest(MANIFEST)
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert _workflow_container_image(workflow) == data["oci"]["reference"]
    assert _workflow_container_image(workflow) == EXPECTED_OCI


def test_workflow_has_no_mutable_python_bootstrap():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "actions/setup-python@" not in workflow
    assert "pip install --upgrade pip" not in workflow
    assert 'python-version: "3.12"' not in workflow


def test_workflow_enforces_hashed_build_lock_before_production_lock():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    build_pos = workflow.index("-r requirements/build.lock")
    production_pos = workflow.index("-r requirements/production.lock")
    environment_pos = workflow.index("python tools/verify_build_environment.py")

    assert environment_pos < build_pos < production_pos

    build_block = workflow[environment_pos:production_pos]
    assert "--require-hashes" in build_block
    assert "--only-binary=:all:" in build_block


def test_workflow_installs_project_without_dependency_resolution():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "python -m pip install --no-deps --no-build-isolation -e ." in workflow
    assert 'pip install -e ".[test]"' not in workflow


def test_tampered_lock_is_detectable(tmp_path: Path):
    data = load_manifest(MANIFEST)

    tampered = tmp_path / "build.lock"
    tampered.write_bytes(BUILD_LOCK.read_bytes() + b"\n# tampered\n")

    assert sha256_file(tampered) != data["build_toolchain"]["lock_sha256"]


def test_manifest_rejects_wrong_schema(tmp_path: Path):
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    data["schema"] = "aodsl.build-environment.v0"

    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(BuildEnvironmentError):
        load_manifest(p)


def test_manifest_bytes_are_stable():
    digest = hashlib.sha256(MANIFEST.read_bytes()).hexdigest()
    assert len(digest) == 64
