import hashlib
import importlib.util
import json
import shutil
from pathlib import Path

import pytest

from aodsl.certification.sbom import write_sbom
from aodsl.certification.certified_bundle import (
    BUNDLE_MANIFEST_PATH,
    build_certified_bundle_manifest,
    verify_certified_bundle_manifest,
)
from aodsl.certification.release_identity import (
    build_release_identity,
    verify_release_identity,
)
from aodsl.certification.version_policy import artifact_names


VERSION = "1.0.1"
TAG = f"v{VERSION}"


def legacy_module():
    path = Path(__file__).with_name(
        "test_inv052_release_identity_binding.py"
    )
    spec = importlib.util.spec_from_file_location(
        "inv052_fixture_e8f_e2e", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def versioned_chain(tmp_path):
    legacy = legacy_module()

    (
        old_artifact,
        manifest,
        old_sbom,
        reproducibility,
        bundle_root,
        bundle,
    ) = legacy.fixture(tmp_path)

    artifact_name, sbom_name = artifact_names(VERSION)
    artifact = old_artifact.with_name(artifact_name)
    sbom = old_sbom.with_name(sbom_name)

    artifact.write_bytes(old_artifact.read_bytes())
    write_sbom(tmp_path, sbom, version=VERSION)

    old_artifact.unlink()
    old_sbom.unlink()

    data = json.loads(reproducibility.read_text())
    data["artifact"]["path"] = f"dist/{artifact_name}"
    data["artifact"]["sha256"] = sha256(artifact)
    data["sbom"]["path"] = f"dist/{sbom_name}"
    data["sbom"]["sha256"] = sha256(sbom)

    reproducibility.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n"
    )

    for relative in (
        f"dist/{artifact_name}",
        f"dist/{sbom_name}",
        "dist/reproducibility-manifest.json",
    ):
        destination = bundle_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(tmp_path / relative, destination)

    for relative in (
        "dist/aodsl-1.0.0-production-source.zip",
        "dist/aodsl-1.0.0.cdx.json",
    ):
        stale = bundle_root / relative
        if stale.exists():
            stale.unlink()

    staged_manifest = bundle_root / BUNDLE_MANIFEST_PATH
    staged_manifest.write_text(
        json.dumps(
            build_certified_bundle_manifest(
                bundle_root, version=VERSION
            ),
            indent=2,
            sort_keys=True,
        ) + "\n"
    )
    bundle.write_bytes(staged_manifest.read_bytes())

    metadata = dict(
        git_commit_sha=legacy.COMMIT,
        git_tag=TAG,
        repository=legacy.REPO,
        workflow_ref=(
            f"{legacy.REPO}/.github/workflows/"
            f"production-release.yml@refs/tags/{TAG}"
        ),
        version=VERSION,
    )

    args = (
        tmp_path,
        artifact,
        manifest,
        sbom,
        reproducibility,
        bundle_root,
        bundle,
    )

    return {
        "args": args,
        "metadata": metadata,
        "bundle_root": bundle_root,
        "staged_manifest": staged_manifest,
        "artifact": artifact,
        "sbom": sbom,
    }


def test_valid_version_101_release_identity(versioned_chain):
    chain = versioned_chain
    assert verify_certified_bundle_manifest(
        chain["bundle_root"],
        chain["staged_manifest"],
        version=VERSION,
    ) == []

    identity = build_release_identity(
        *chain["args"], **chain["metadata"]
    )
    identity_path = (
        chain["args"][0] / "dist/certified-release-identity.json"
    )
    identity_path.write_text(
        json.dumps(identity, indent=2, sort_keys=True) + "\n"
    )

    assert verify_release_identity(
        chain["args"][0],
        identity_path,
        *chain["args"][1:],
        **chain["metadata"],
    ) == []


def test_bundle_rejects_payload_tampering(versioned_chain):
    chain = versioned_chain
    artifact_name, _ = artifact_names(VERSION)
    staged_artifact = (
        chain["bundle_root"] / "dist" / artifact_name
    )
    staged_artifact.write_bytes(
        staged_artifact.read_bytes() + b"tampered"
    )

    errors = verify_certified_bundle_manifest(
        chain["bundle_root"],
        chain["staged_manifest"],
        version=VERSION,
    )
    assert errors == [
        "certified bundle size mismatch: "
        "dist/aodsl-1.0.1-production-source.zip"
    ]


def test_bundle_rejects_missing_payload(versioned_chain):
    chain = versioned_chain
    _, sbom_name = artifact_names(VERSION)
    (chain["bundle_root"] / "dist" / sbom_name).unlink()

    errors = verify_certified_bundle_manifest(
        chain["bundle_root"],
        chain["staged_manifest"],
        version=VERSION,
    )
    assert len(errors) == 1
    assert "certified bundle physical closure mismatch" in errors[0]
    assert "missing=[\'dist/aodsl-1.0.1.cdx.json\']" in errors[0]
    assert "unexpected=[]" in errors[0]


def test_bundle_rejects_wrong_version(versioned_chain):
    chain = versioned_chain
    errors = verify_certified_bundle_manifest(
        chain["bundle_root"],
        chain["staged_manifest"],
        version="1.0.0",
    )
    assert len(errors) == 1
    assert "certified bundle physical closure mismatch" in errors[0]
    assert "dist/aodsl-1.0.0-production-source.zip" in errors[0]
    assert "dist/aodsl-1.0.0.cdx.json" in errors[0]
    assert "dist/aodsl-1.0.1-production-source.zip" in errors[0]
    assert "dist/aodsl-1.0.1.cdx.json" in errors[0]
    assert "missing=" in errors[0]
    assert "unexpected=" in errors[0]


def test_identity_rejects_sbom_tampering(versioned_chain):
    chain = versioned_chain
    identity = build_release_identity(
        *chain["args"], **chain["metadata"]
    )

    identity_path = (
        chain["args"][0] / "dist/certified-release-identity.json"
    )
    identity_path.write_text(
        json.dumps(identity, indent=2, sort_keys=True) + "\n"
    )

    chain["sbom"].write_bytes(
        chain["sbom"].read_bytes() + b"tampered"
    )

    errors = verify_release_identity(
        chain["args"][0],
        identity_path,
        *chain["args"][1:],
        **chain["metadata"],
    )
    assert errors == [
        "reproducibility SBOM SHA-256 mismatch"
    ]
