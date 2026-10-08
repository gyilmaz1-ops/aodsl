import importlib.util
from pathlib import Path

import pytest

from aodsl.certification.release_identity import (
    build_release_identity,
    verify_release_identity,
)

LEGACY = Path(__file__).with_name("test_inv052_release_identity_binding.py")
spec = importlib.util.spec_from_file_location("inv052_fixture_for_e8f", LEGACY)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def arguments(tmp_path):
    artifact, manifest, sbom, reproducibility, bundle_root, bundle = (
        module.fixture(tmp_path)
    )
    return (
        tmp_path,
        artifact,
        manifest,
        sbom,
        reproducibility,
        bundle_root,
        bundle,
    )


def metadata(tag):
    return dict(
        git_commit_sha=module.COMMIT,
        git_tag=tag,
        repository=module.REPO,
        workflow_ref=(
            f"{module.REPO}/.github/workflows/"
            f"production-release.yml@refs/tags/{tag}"
        ),
    )


def test_e8f_explicit_version_rejects_wrong_artifact(tmp_path):
    args = arguments(tmp_path)
    with pytest.raises(ValueError, match="artifact version/name mismatch"):
        build_release_identity(
            *args, **metadata("v1.0.1"), version="1.0.1"
        )


def test_e8f_explicit_version_rejects_wrong_tag(tmp_path):
    args = arguments(tmp_path)
    with pytest.raises(ValueError, match="git tag version mismatch"):
        build_release_identity(
            *args, **metadata("v1.2.3"), version="1.0.0"
        )


def test_e8f_explicit_version_rejects_wrong_sbom(tmp_path):
    args = list(arguments(tmp_path))
    args[1] = args[1].with_name("aodsl-1.0.1-production-source.zip")
    with pytest.raises(ValueError, match="SBOM version/name mismatch"):
        build_release_identity(
            *args, **metadata("v1.0.1"), version="1.0.1"
        )


def test_e8f_verify_rejects_wrong_artifact_version(tmp_path):
    args = arguments(tmp_path)
    identity_path = tmp_path / "dist/certified-release-identity.json"
    identity_path.write_text("{}", encoding="utf-8")
    errors = verify_release_identity(
        args[0],
        identity_path,
        *args[1:],
        **metadata("v1.0.1"),
        version="1.0.1",
    )
    assert errors
    assert any("artifact version/name mismatch" in e for e in errors)


def test_e8f_legacy_none_still_passes(tmp_path):
    args = arguments(tmp_path)
    identity = build_release_identity(
        *args, **metadata(module.TAG), version=None
    )
    assert identity["schema"] == "aodsl.certified-release-identity.v5"
