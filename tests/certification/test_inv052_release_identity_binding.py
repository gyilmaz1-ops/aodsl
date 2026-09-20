import json
from pathlib import Path

from aodsl.certification.sbom import write_sbom
from aodsl.certification.release_identity import (
    build_release_identity,
    verify_release_identity,
)

COMMIT = "a" * 40
TAG = "v1.2.3"
REPO = "owner/aodsl"
WORKFLOW_REF = (
    f"{REPO}/.github/workflows/production-release.yml@refs/tags/{TAG}"
)
SOURCE = "b" * 64


def fixture(tmp_path: Path):
    (tmp_path / ".github/workflows").mkdir(parents=True)
    (tmp_path / ".github/workflows/production-release.yml").write_text(
        "name: release\n"
    )

    (tmp_path / "dist").mkdir()

    (tmp_path / "requirements").mkdir()
    build_lock = tmp_path / "requirements/build.lock"
    build_lock.write_text(
        "setuptools==84.0.0 "
        "--hash=sha256:"
        + "d" * 64
        + "\n",
        encoding="utf-8",
    )

    import hashlib

    build_lock_sha = hashlib.sha256(
        build_lock.read_bytes()
    ).hexdigest()

    (tmp_path / "architecture").mkdir()
    (
        tmp_path / "architecture/build-environment.v1.json"
    ).write_text(
        json.dumps(
            {
                "schema": "aodsl.build-environment.v1",
                "invariant": "INV-054",
                "platform": {
                    "os": "linux",
                    "architecture": "amd64",
                },
                "oci": {
                    "image": "python",
                    "tag": "3.12.11-slim",
                    "digest": "sha256:47ae396f09c1303b8653019811a8498470603d7ffefc29cb07c88f1f8cb3d19f",
                    "reference": "python@sha256:47ae396f09c1303b8653019811a8498470603d7ffefc29cb07c88f1f8cb3d19f",
                },
                "python": {
                    "implementation": "CPython",
                    "version": "3.12.11",
                },
                "bootstrap": {
                    "pip_version": "25.0.1",
                },
                "build_toolchain": {
                    "lock_path": "requirements/build.lock",
                    "lock_sha256": build_lock_sha,
                    "installation_policy": {
                        "require_hashes": True,
                        "only_binary": True,
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    (tmp_path / "requirements/production.lock").write_text(
        "psycopg==3.3.6 "
        "--hash=sha256:"
        + "a" * 64
        + "\n"
        + "psycopg-binary==3.3.6 "
        "--hash=sha256:"
        + "b" * 64
        + "\n"
        + "typing-extensions==4.16.0 "
        "--hash=sha256:"
        + "c" * 64
        + "\n",
        encoding="utf-8",
    )

    sbom = tmp_path / "dist/aodsl-1.0.0.cdx.json"
    write_sbom(tmp_path, sbom)

    artifact = tmp_path / "dist/aodsl-1.0.0-production-source.zip"
    artifact.write_bytes(b"artifact")

    (tmp_path / "certification").mkdir()
    manifest = (
        tmp_path
        / "certification/production-certification-manifest.json"
    )
    manifest.write_text(
        json.dumps(
            {
                "source": {
                    "canonical_tree_sha256": SOURCE,
                }
            }
        )
    )

    return artifact, manifest, sbom


def build(tmp_path):
    artifact, manifest, sbom = fixture(tmp_path)

    identity = build_release_identity(
        tmp_path,
        artifact,
        manifest,
        sbom,
        git_commit_sha=COMMIT,
        git_tag=TAG,
        repository=REPO,
        workflow_ref=WORKFLOW_REF,
    )

    identity_path = tmp_path / "dist/certified-release-identity.json"
    identity_path.write_text(json.dumps(identity))

    return artifact, manifest, sbom, identity_path, identity


def verify(
    tmp_path,
    artifact,
    manifest,
    sbom,
    identity_path,
    **overrides,
):
    args = dict(
        git_commit_sha=COMMIT,
        git_tag=TAG,
        repository=REPO,
        workflow_ref=WORKFLOW_REF,
    )
    args.update(overrides)

    return verify_release_identity(
        tmp_path,
        identity_path,
        artifact,
        manifest,
        sbom,
        **args,
    )


def test_binds_certification_commit_tag_workflow_and_artifact(tmp_path):
    artifact, manifest, sbom, identity_path, identity = build(tmp_path)

    assert identity["certification"]["source_tree_sha256"] == SOURCE
    assert identity["git"] == {
        "commit_sha": COMMIT,
        "tag": TAG,
    }
    assert (
        identity["artifact"]["sha256"]
        == identity["artifact"]["provenance_subject_digest"]["sha256"]
    )

    assert verify(
        tmp_path,
        artifact,
        manifest,
        sbom,
        identity_path,
    ) == []


def test_artifact_tamper_fails_closed(tmp_path):
    artifact, manifest, sbom, identity_path, _ = build(tmp_path)

    artifact.write_bytes(b"tampered")

    assert verify(
        tmp_path,
        artifact,
        manifest,
        sbom,
        identity_path,
    )


def test_commit_or_tag_mismatch_fails_closed(tmp_path):
    artifact, manifest, sbom, identity_path, _ = build(tmp_path)

    assert verify(
        tmp_path,
        artifact,
        manifest,
        sbom,
        identity_path,
        git_commit_sha="c" * 40,
    )

    assert verify(
        tmp_path,
        artifact,
        manifest,
        sbom,
        identity_path,
        git_tag="v1.2.4",
        workflow_ref=(
            f"{REPO}/.github/workflows/production-release.yml"
            "@refs/tags/v1.2.4"
        ),
    )


def test_workflow_content_or_repository_mismatch_fails_closed(tmp_path):
    artifact, manifest, sbom, identity_path, _ = build(tmp_path)

    (tmp_path / ".github/workflows/production-release.yml").write_text(
        "name: tampered\n"
    )

    assert verify(
        tmp_path,
        artifact,
        manifest,
        sbom,
        identity_path,
    )

    (tmp_path / ".github/workflows/production-release.yml").write_text(
        "name: release\n"
    )

    assert verify(
        tmp_path,
        artifact,
        manifest,
        sbom,
        identity_path,
        repository="other/aodsl",
        workflow_ref=(
            "other/aodsl/.github/workflows/"
            "production-release.yml@refs/tags/v1.2.3"
        ),
    )


def test_v3_binds_build_environment_manifest_and_lock(tmp_path):
    artifact, manifest, sbom, identity_path, identity = build(tmp_path)

    build_environment = (
        tmp_path / "architecture/build-environment.v1.json"
    )
    build_lock = tmp_path / "requirements/build.lock"

    import hashlib

    assert identity["schema"] == "aodsl.certified-release-identity.v3"
    assert (
        identity["build_environment"]["manifest_sha256"]
        == hashlib.sha256(build_environment.read_bytes()).hexdigest()
    )
    assert (
        identity["build_environment"]["build_lock_sha256"]
        == hashlib.sha256(build_lock.read_bytes()).hexdigest()
    )
    assert identity["build_environment"]["oci_reference"].startswith(
        "python@sha256:"
    )
    assert identity["build_environment"]["oci_digest"].startswith(
        "sha256:"
    )

    assert verify(
        tmp_path,
        artifact,
        manifest,
        sbom,
        identity_path,
    ) == []


def test_v3_build_lock_tamper_fails_closed(tmp_path):
    artifact, manifest, sbom, identity_path, _ = build(tmp_path)

    build_lock = tmp_path / "requirements/build.lock"
    build_lock.write_bytes(
        build_lock.read_bytes() + b"\n# tampered\n"
    )

    errors = verify(
        tmp_path,
        artifact,
        manifest,
        sbom,
        identity_path,
    )

    assert errors
    assert any(
        "build environment lock SHA-256 mismatch" in error
        for error in errors
    )


def test_v3_build_environment_manifest_tamper_fails_closed(tmp_path):
    artifact, manifest, sbom, identity_path, _ = build(tmp_path)

    env_path = (
        tmp_path / "architecture/build-environment.v1.json"
    )
    data = json.loads(env_path.read_text(encoding="utf-8"))

    # Change a security-relevant field while keeping the manifest
    # internally structurally valid.
    data["python"]["version"] = "3.12.10"

    env_path.write_text(
        json.dumps(data),
        encoding="utf-8",
    )

    errors = verify(
        tmp_path,
        artifact,
        manifest,
        sbom,
        identity_path,
    )

    assert errors
    assert any(
        "release identity does not match" in error
        for error in errors
    )


def test_v3_oci_reference_digest_inconsistency_fails_closed(tmp_path):
    artifact, manifest, sbom, identity_path, _ = build(tmp_path)

    env_path = (
        tmp_path / "architecture/build-environment.v1.json"
    )
    data = json.loads(env_path.read_text(encoding="utf-8"))

    data["oci"]["reference"] = (
        "python@sha256:" + "f" * 64
    )

    env_path.write_text(
        json.dumps(data),
        encoding="utf-8",
    )

    errors = verify(
        tmp_path,
        artifact,
        manifest,
        sbom,
        identity_path,
    )

    assert errors
    assert any(
        "OCI reference mismatch" in error
        for error in errors
    )


def test_v3_stored_build_environment_identity_tamper_fails_closed(
    tmp_path,
):
    artifact, manifest, sbom, identity_path, identity = build(tmp_path)

    identity["build_environment"]["pip_version"] = "26.2.1"
    identity_path.write_text(
        json.dumps(identity),
        encoding="utf-8",
    )

    errors = verify(
        tmp_path,
        artifact,
        manifest,
        sbom,
        identity_path,
    )

    assert errors
    assert any(
        "release identity does not match" in error
        for error in errors
    )
