import hashlib
import json
import shutil
from pathlib import Path

from aodsl.certification.certified_bundle import (
    BUNDLE_MANIFEST_PATH,
    build_certified_bundle_manifest,
)
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

    reproducibility = tmp_path / "dist/reproducibility-manifest.json"
    reproducibility.write_text(
        json.dumps(
            {
                "schema": "aodsl.reproducible-release-artifact.v1",
                "invariant": "INV-055",
                "comparison": {
                    "algorithm": "sha256-and-byte-equality",
                    "independent_builds": 2,
                    "result": "IDENTICAL",
                },
                "artifact": {
                    "path": "dist/aodsl-1.0.0-production-source.zip",
                    "sha256": hashlib.sha256(
                        artifact.read_bytes()
                    ).hexdigest(),
                },
                "sbom": {
                    "path": "dist/aodsl-1.0.0.cdx.json",
                    "sha256": hashlib.sha256(
                        sbom.read_bytes()
                    ).hexdigest(),
                },
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

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

    # Remaining INV-056 certified payload/evidence files.
    (tmp_path / "certification/evidence").mkdir(parents=True)

    (tmp_path / "dist/release-manifest.json").write_text(
        json.dumps({"release": "fixture"}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (
        tmp_path
        / "certification/production-certification-attestation.json"
    ).write_text(
        json.dumps({"attestation": "fixture"}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (
        tmp_path
        / "certification/evidence/live-certification-status.json"
    ).write_text(
        json.dumps({"status": "CERTIFIED"}, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    bundle_root = tmp_path / "bundle-stage"

    from aodsl.certification.certified_bundle import (
        REQUIRED_PAYLOAD_PATHS,
    )

    for relative in REQUIRED_PAYLOAD_PATHS:
        source = tmp_path / relative
        target = bundle_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)

    staged_bundle = bundle_root / BUNDLE_MANIFEST_PATH
    staged_bundle.parent.mkdir(parents=True, exist_ok=True)
    staged_bundle.write_text(
        json.dumps(
            build_certified_bundle_manifest(bundle_root),
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    bundle = tmp_path / BUNDLE_MANIFEST_PATH
    bundle.write_bytes(staged_bundle.read_bytes())

    return (
        artifact,
        manifest,
        sbom,
        reproducibility,
        bundle_root,
        bundle,
    )


def build(tmp_path):
    artifact, manifest, sbom, reproducibility, bundle_root, bundle = fixture(tmp_path)

    identity = build_release_identity(
        tmp_path,
        artifact,
        manifest,
        sbom,
        reproducibility,
        bundle_root,
        bundle,
        git_commit_sha=COMMIT,
        git_tag=TAG,
        repository=REPO,
        workflow_ref=WORKFLOW_REF,
    )

    identity_path = tmp_path / "dist/certified-release-identity.json"
    identity_path.write_text(json.dumps(identity))

    return (
        artifact,
        manifest,
        sbom,
        reproducibility,
        bundle_root,
        bundle,
        identity_path,
        identity,
    )


def verify(
    tmp_path,
    artifact,
    manifest,
    sbom,
    reproducibility,
    bundle_root,
    bundle,
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
        reproducibility,
        bundle_root,
        bundle,
        **args,
    )


def test_binds_certification_commit_tag_workflow_and_artifact(tmp_path):
    artifact, manifest, sbom, reproducibility, bundle_root, bundle, identity_path, identity = build(tmp_path)

    assert identity["certification"]["source_tree_sha256"] == SOURCE
    assert identity["git"] == {
        "commit_sha": COMMIT,
        "tag": TAG,
    }
    assert (
        identity["artifact"]["sha256"]
        == identity["artifact"]["provenance_subject_digest"]["sha256"]
    )

    bundle_block = identity["certified_bundle"]
    assert bundle_block["manifest_path"] == BUNDLE_MANIFEST_PATH
    assert bundle_block["manifest_sha256"] == hashlib.sha256(
        bundle.read_bytes()
    ).hexdigest()
    assert bundle_block["schema"] == "aodsl.certified-bundle.v1"
    assert bundle_block["invariant"] == "INV-056"
    assert bundle_block["hash_algorithm"] == "sha256"
    assert bundle_block["closure_policy"] == "exact-physical-payload-set"
    assert bundle_block["payload_count"] == 7

    assert verify(
        tmp_path,
        artifact,
        manifest,
        sbom,
        reproducibility,
        bundle_root,
        bundle,
        identity_path,
    ) == []


def test_artifact_tamper_fails_closed(tmp_path):
    artifact, manifest, sbom, reproducibility, bundle_root, bundle, identity_path, _ = build(tmp_path)

    artifact.write_bytes(b"tampered")

    assert verify(
        tmp_path,
        artifact,
        manifest,
        sbom,
        reproducibility,
        bundle_root,
        bundle,
        identity_path,
    )


def test_commit_or_tag_mismatch_fails_closed(tmp_path):
    artifact, manifest, sbom, reproducibility, bundle_root, bundle, identity_path, _ = build(tmp_path)

    assert verify(
        tmp_path,
        artifact,
        manifest,
        sbom,
        reproducibility,
        bundle_root,
        bundle,
        identity_path,
        git_commit_sha="c" * 40,
    )

    assert verify(
        tmp_path,
        artifact,
        manifest,
        sbom,
        reproducibility,
        bundle_root,
        bundle,
        identity_path,
        git_tag="v1.2.4",
        workflow_ref=(
            f"{REPO}/.github/workflows/production-release.yml"
            "@refs/tags/v1.2.4"
        ),
    )


def test_workflow_content_or_repository_mismatch_fails_closed(tmp_path):
    artifact, manifest, sbom, reproducibility, bundle_root, bundle, identity_path, _ = build(tmp_path)

    (tmp_path / ".github/workflows/production-release.yml").write_text(
        "name: tampered\n"
    )

    assert verify(
        tmp_path,
        artifact,
        manifest,
        sbom,
        reproducibility,
        bundle_root,
        bundle,
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
        reproducibility,
        bundle_root,
        bundle,
        identity_path,
        repository="other/aodsl",
        workflow_ref=(
            "other/aodsl/.github/workflows/"
            "production-release.yml@refs/tags/v1.2.3"
        ),
    )


def test_v5_binds_build_environment_manifest_and_lock(tmp_path):
    artifact, manifest, sbom, reproducibility, bundle_root, bundle, identity_path, identity = build(tmp_path)

    build_environment = (
        tmp_path / "architecture/build-environment.v1.json"
    )
    build_lock = tmp_path / "requirements/build.lock"

    import hashlib

    assert identity["schema"] == "aodsl.certified-release-identity.v5"
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
        reproducibility,
        bundle_root,
        bundle,
        identity_path,
    ) == []


def test_v5_build_lock_tamper_fails_closed(tmp_path):
    artifact, manifest, sbom, reproducibility, bundle_root, bundle, identity_path, _ = build(tmp_path)

    build_lock = tmp_path / "requirements/build.lock"
    build_lock.write_bytes(
        build_lock.read_bytes() + b"\n# tampered\n"
    )

    errors = verify(
        tmp_path,
        artifact,
        manifest,
        sbom,
        reproducibility,
        bundle_root,
        bundle,
        identity_path,
    )

    assert errors
    assert any(
        "build environment lock SHA-256 mismatch" in error
        for error in errors
    )


def test_v5_build_environment_manifest_tamper_fails_closed(tmp_path):
    artifact, manifest, sbom, reproducibility, bundle_root, bundle, identity_path, _ = build(tmp_path)

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
        reproducibility,
        bundle_root,
        bundle,
        identity_path,
    )

    assert errors
    assert any(
        "release identity does not match" in error
        for error in errors
    )


def test_v5_oci_reference_digest_inconsistency_fails_closed(tmp_path):
    artifact, manifest, sbom, reproducibility, bundle_root, bundle, identity_path, _ = build(tmp_path)

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
        reproducibility,
        bundle_root,
        bundle,
        identity_path,
    )

    assert errors
    assert any(
        "OCI reference mismatch" in error
        for error in errors
    )


def test_v5_stored_build_environment_identity_tamper_fails_closed(
    tmp_path,
):
    artifact, manifest, sbom, reproducibility, bundle_root, bundle, identity_path, identity = build(tmp_path)

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
        reproducibility,
        bundle_root,
        bundle,
        identity_path,
    )

    assert errors
    assert any(
        "release identity does not match" in error
        for error in errors
    )


def test_release_identity_creator_has_no_git_cli_dependency():
    """INV-054: certified container must not require an implicit git executable."""
    script = (
        Path(__file__).resolve().parents[2] / "tools" / "create_release_identity.py"
    ).read_text()

    forbidden = (
        "import subprocess",
        '["git"',
        "rev-parse",
        "rev-list",
    )

    for token in forbidden:
        assert token not in script, (
            f"certified release identity creator has forbidden "
            f"Git CLI dependency: {token!r}"
        )

    required = (
        'need("GITHUB_REF_NAME")',
        'need("GITHUB_REPOSITORY")',
        'need("GITHUB_SHA")',
        'need("GITHUB_REF_TYPE")',
    )

    for token in required:
        assert token in script


def test_release_identity_creator_requires_tag_ci_context():
    """INV-052: release identity remains bound to authenticated tag CI context."""
    script = (
        Path(__file__).resolve().parents[2] / "tools" / "create_release_identity.py"
    ).read_text()

    assert 'if ref_type != "tag":' in script
    assert "production release must run from tag" in script
    assert (
        'workflow_ref = '
        'f"{repo}/.github/workflows/production-release.yml@refs/tags/{tag}"'
        in script
    )


def _rewrite_reproducibility(path: Path, mutate) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(
        json.dumps(data, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def test_v5_binds_reproducibility_manifest_and_cross_links(tmp_path):
    (
        artifact,
        manifest,
        sbom,
        reproducibility,
        bundle_root,
        bundle,
        identity_path,
        identity,
    ) = build(tmp_path)

    block = identity["reproducibility"]

    assert block["manifest_path"] == "dist/reproducibility-manifest.json"
    assert block["manifest_sha256"] == hashlib.sha256(
        reproducibility.read_bytes()
    ).hexdigest()
    assert block["schema"] == "aodsl.reproducible-release-artifact.v1"
    assert block["invariant"] == "INV-055"
    assert block["comparison_algorithm"] == "sha256-and-byte-equality"
    assert block["independent_builds"] == 2
    assert block["result"] == "IDENTICAL"
    assert block["artifact_sha256"] == identity["artifact"]["sha256"]
    assert block["sbom_sha256"] == identity["sbom"]["sha256"]

    assert verify(
        tmp_path,
        artifact,
        manifest,
        sbom,
        reproducibility,
        bundle_root,
        bundle,
        identity_path,
    ) == []


def test_v5_noncanonical_reproducibility_path_fails_closed(tmp_path):
    artifact, manifest, sbom, reproducibility, bundle_root, bundle = fixture(tmp_path)

    alternate = tmp_path / "dist/alternate-reproducibility.json"
    alternate.write_bytes(reproducibility.read_bytes())

    try:
        build_release_identity(
            tmp_path,
            artifact,
            manifest,
            sbom,
            alternate,
            bundle_root,
            bundle,
            git_commit_sha=COMMIT,
            git_tag=TAG,
            repository=REPO,
            workflow_ref=WORKFLOW_REF,
        )
    except ValueError as exc:
        assert str(exc) == "reproducibility manifest path mismatch"
    else:
        raise AssertionError(
            "noncanonical reproducibility path was accepted"
        )


def test_v5_non_object_reproducibility_manifest_fails_closed(tmp_path):
    (
        artifact,
        manifest,
        sbom,
        reproducibility,
        bundle_root,
        bundle,
        identity_path,
        _,
    ) = build(tmp_path)

    reproducibility.write_text(
        json.dumps(["not", "an", "object"]) + "\n",
        encoding="utf-8",
    )

    errors = verify(
        tmp_path,
        artifact,
        manifest,
        sbom,
        reproducibility,
        bundle_root,
        bundle,
        identity_path,
    )

    assert errors
    assert any(
        "reproducibility manifest must be a JSON object" in error
        for error in errors
    )


def test_v5_reproducibility_result_fails_closed(tmp_path):
    (
        artifact,
        manifest,
        sbom,
        reproducibility,
        bundle_root,
        bundle,
        identity_path,
        _,
    ) = build(tmp_path)

    _rewrite_reproducibility(
        reproducibility,
        lambda data: data["comparison"].__setitem__(
            "result",
            "NOT_IDENTICAL",
        ),
    )

    errors = verify(
        tmp_path,
        artifact,
        manifest,
        sbom,
        reproducibility,
        bundle_root,
        bundle,
        identity_path,
    )

    assert errors
    assert any(
        "reproducibility result is not IDENTICAL" in error
        for error in errors
    )


def test_v5_reproducibility_build_count_fails_closed(tmp_path):
    (
        artifact,
        manifest,
        sbom,
        reproducibility,
        bundle_root,
        bundle,
        identity_path,
        _,
    ) = build(tmp_path)

    _rewrite_reproducibility(
        reproducibility,
        lambda data: data["comparison"].__setitem__(
            "independent_builds",
            1,
        ),
    )

    errors = verify(
        tmp_path,
        artifact,
        manifest,
        sbom,
        reproducibility,
        bundle_root,
        bundle,
        identity_path,
    )

    assert errors
    assert any(
        "invalid reproducibility independent build count" in error
        for error in errors
    )


def test_v5_reproducibility_artifact_cross_link_fails_closed(tmp_path):
    (
        artifact,
        manifest,
        sbom,
        reproducibility,
        bundle_root,
        bundle,
        identity_path,
        _,
    ) = build(tmp_path)

    _rewrite_reproducibility(
        reproducibility,
        lambda data: data["artifact"].__setitem__(
            "sha256",
            "f" * 64,
        ),
    )

    errors = verify(
        tmp_path,
        artifact,
        manifest,
        sbom,
        reproducibility,
        bundle_root,
        bundle,
        identity_path,
    )

    assert errors
    assert any(
        "reproducibility artifact SHA-256 mismatch" in error
        for error in errors
    )


def test_v5_reproducibility_sbom_cross_link_fails_closed(tmp_path):
    (
        artifact,
        manifest,
        sbom,
        reproducibility,
        bundle_root,
        bundle,
        identity_path,
        _,
    ) = build(tmp_path)

    _rewrite_reproducibility(
        reproducibility,
        lambda data: data["sbom"].__setitem__(
            "sha256",
            "f" * 64,
        ),
    )

    errors = verify(
        tmp_path,
        artifact,
        manifest,
        sbom,
        reproducibility,
        bundle_root,
        bundle,
        identity_path,
    )

    assert errors
    assert any(
        "reproducibility SBOM SHA-256 mismatch" in error
        for error in errors
    )


def test_v5_stored_reproducibility_identity_tamper_fails_closed(
    tmp_path,
):
    (
        artifact,
        manifest,
        sbom,
        reproducibility,
        bundle_root,
        bundle,
        identity_path,
        identity,
    ) = build(tmp_path)

    identity["reproducibility"]["result"] = "NOT_IDENTICAL"
    identity_path.write_text(
        json.dumps(identity),
        encoding="utf-8",
    )

    errors = verify(
        tmp_path,
        artifact,
        manifest,
        sbom,
        reproducibility,
        bundle_root,
        bundle,
        identity_path,
    )

    assert errors
    assert any(
        "release identity does not match" in error
        for error in errors
    )


def test_v5_noncanonical_bundle_manifest_path_fails_closed(tmp_path):
    artifact, manifest, sbom, reproducibility, bundle_root, bundle = fixture(tmp_path)

    alternate = tmp_path / "dist/alternate-certified-bundle.json"
    alternate.write_bytes(bundle.read_bytes())

    try:
        build_release_identity(
            tmp_path,
            artifact,
            manifest,
            sbom,
            reproducibility,
            bundle_root,
            alternate,
            git_commit_sha=COMMIT,
            git_tag=TAG,
            repository=REPO,
            workflow_ref=WORKFLOW_REF,
        )
    except ValueError as exc:
        assert str(exc) == "certified bundle manifest path mismatch"
    else:
        raise AssertionError(
            "noncanonical certified bundle manifest path was accepted"
        )


def test_v5_missing_bundle_manifest_fails_closed(tmp_path):
    (
        artifact,
        manifest,
        sbom,
        reproducibility,
        bundle_root,
        bundle,
        identity_path,
        _,
    ) = build(tmp_path)

    bundle.unlink()

    errors = verify(
        tmp_path,
        artifact,
        manifest,
        sbom,
        reproducibility,
        bundle_root,
        bundle,
        identity_path,
    )

    assert errors
    assert any(
        "certified bundle manifest missing" in error
        for error in errors
    )


def test_v5_bundle_manifest_tamper_fails_closed(tmp_path):
    (
        artifact,
        manifest,
        sbom,
        reproducibility,
        bundle_root,
        bundle,
        identity_path,
        _,
    ) = build(tmp_path)

    data = json.loads(bundle.read_text(encoding="utf-8"))
    data["closure"]["payload_count"] = 999
    bundle.write_text(
        json.dumps(data, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    errors = verify(
        tmp_path,
        artifact,
        manifest,
        sbom,
        reproducibility,
        bundle_root,
        bundle,
        identity_path,
    )

    assert errors
    assert any(
        "promoted certified bundle manifest differs from "
        "physically verified staged manifest" in error
        for error in errors
    )


def test_v5_bundle_payload_tamper_fails_closed(tmp_path):
    (
        artifact,
        manifest,
        sbom,
        reproducibility,
        bundle_root,
        bundle,
        identity_path,
        _,
    ) = build(tmp_path)

    release_manifest = (
        bundle_root / "dist/release-manifest.json"
    )
    release_manifest.write_bytes(
        release_manifest.read_bytes() + b"tampered"
    )

    errors = verify(
        tmp_path,
        artifact,
        manifest,
        sbom,
        reproducibility,
        bundle_root,
        bundle,
        identity_path,
    )

    assert errors
    assert any(
        "certified bundle verification failed" in error
        for error in errors
    )


def test_v5_stored_bundle_identity_tamper_fails_closed(tmp_path):
    (
        artifact,
        manifest,
        sbom,
        reproducibility,
        bundle_root,
        bundle,
        identity_path,
        identity,
    ) = build(tmp_path)

    identity["certified_bundle"]["manifest_sha256"] = "f" * 64
    identity_path.write_text(
        json.dumps(identity),
        encoding="utf-8",
    )

    errors = verify(
        tmp_path,
        artifact,
        manifest,
        sbom,
        reproducibility,
        bundle_root,
        bundle,
        identity_path,
    )

    assert errors
    assert any(
        "release identity does not match" in error
        for error in errors
    )
