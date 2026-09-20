import json
from pathlib import Path

from aodsl.certification.release_identity import build_release_identity, verify_release_identity

COMMIT = "a" * 40
TAG = "v1.2.3"
REPO = "owner/aodsl"
WORKFLOW_REF = f"{REPO}/.github/workflows/production-release.yml@refs/tags/{TAG}"
SOURCE = "b" * 64


def fixture(tmp_path: Path):
    (tmp_path / ".github/workflows").mkdir(parents=True)
    (tmp_path / ".github/workflows/production-release.yml").write_text("name: release\n")
    (tmp_path / "dist").mkdir()
    artifact = tmp_path / "dist/aodsl-1.0.0-production-source.zip"
    artifact.write_bytes(b"artifact")
    (tmp_path / "certification").mkdir()
    manifest = tmp_path / "certification/production-certification-manifest.json"
    manifest.write_text(json.dumps({"source": {"canonical_tree_sha256": SOURCE}}))
    return artifact, manifest


def build(tmp_path):
    artifact, manifest = fixture(tmp_path)
    identity = build_release_identity(
        tmp_path, artifact, manifest,
        git_commit_sha=COMMIT, git_tag=TAG, repository=REPO, workflow_ref=WORKFLOW_REF,
    )
    identity_path = tmp_path / "dist/certified-release-identity.json"
    identity_path.write_text(json.dumps(identity))
    return artifact, manifest, identity_path, identity


def verify(tmp_path, artifact, manifest, identity_path, **overrides):
    args = dict(git_commit_sha=COMMIT, git_tag=TAG, repository=REPO, workflow_ref=WORKFLOW_REF)
    args.update(overrides)
    return verify_release_identity(tmp_path, identity_path, artifact, manifest, **args)


def test_binds_certification_commit_tag_workflow_and_artifact(tmp_path):
    artifact, manifest, identity_path, identity = build(tmp_path)
    assert identity["certification"]["source_tree_sha256"] == SOURCE
    assert identity["git"] == {"commit_sha": COMMIT, "tag": TAG}
    assert identity["artifact"]["sha256"] == identity["artifact"]["provenance_subject_digest"]["sha256"]
    assert verify(tmp_path, artifact, manifest, identity_path) == []


def test_artifact_tamper_fails_closed(tmp_path):
    artifact, manifest, identity_path, _ = build(tmp_path)
    artifact.write_bytes(b"tampered")
    assert verify(tmp_path, artifact, manifest, identity_path)


def test_commit_or_tag_mismatch_fails_closed(tmp_path):
    artifact, manifest, identity_path, _ = build(tmp_path)
    assert verify(tmp_path, artifact, manifest, identity_path, git_commit_sha="c" * 40)
    assert verify(tmp_path, artifact, manifest, identity_path, git_tag="v1.2.4",
                  workflow_ref=f"{REPO}/.github/workflows/production-release.yml@refs/tags/v1.2.4")


def test_workflow_content_or_repository_mismatch_fails_closed(tmp_path):
    artifact, manifest, identity_path, _ = build(tmp_path)
    (tmp_path / ".github/workflows/production-release.yml").write_text("name: tampered\n")
    assert verify(tmp_path, artifact, manifest, identity_path)
    (tmp_path / ".github/workflows/production-release.yml").write_text("name: release\n")
    assert verify(tmp_path, artifact, manifest, identity_path, repository="other/aodsl",
                  workflow_ref="other/aodsl/.github/workflows/production-release.yml@refs/tags/v1.2.3")
