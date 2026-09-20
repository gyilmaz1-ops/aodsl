from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

SCHEMA = "aodsl.certified-release-identity.v4"
HASH_ALGORITHM = "sha256"
WORKFLOW_PATH = ".github/workflows/production-release.yml"
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
TAG = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _require(value: str, pattern: re.Pattern[str], label: str) -> str:
    value = (value or "").strip()
    if not pattern.fullmatch(value):
        raise ValueError(f"invalid {label}: {value!r}")
    return value


def build_release_identity(
    root: Path,
    artifact_path: Path,
    certification_manifest_path: Path,
    sbom_path: Path,
    reproducibility_manifest_path: Path,
    *,
    git_commit_sha: str,
    git_tag: str,
    repository: str,
    workflow_ref: str,
) -> dict:
    root = Path(root).resolve()
    artifact_path = Path(artifact_path).resolve()
    certification_manifest_path = Path(certification_manifest_path).resolve()
    sbom_path = Path(sbom_path).resolve()
    reproducibility_manifest_path = Path(
        reproducibility_manifest_path
    ).resolve()
    expected_reproducibility_manifest_path = (
        root / "dist/reproducibility-manifest.json"
    ).resolve()
    if (
        reproducibility_manifest_path
        != expected_reproducibility_manifest_path
    ):
        raise ValueError("reproducibility manifest path mismatch")

    dependency_lock_path = root / "requirements/production.lock"
    build_environment_path = root / "architecture/build-environment.v1.json"
    build_lock_path = root / "requirements/build.lock"
    workflow_path = root / WORKFLOW_PATH

    commit = _require(git_commit_sha, HEX40, "git commit SHA")
    tag = _require(git_tag, TAG, "git tag")
    repo = _require(repository, REPOSITORY, "repository")
    expected_workflow_ref = f"{repo}/{WORKFLOW_PATH}@refs/tags/{tag}"
    if workflow_ref != expected_workflow_ref:
        raise ValueError(
            "workflow ref mismatch: "
            f"expected={expected_workflow_ref!r}, got={workflow_ref!r}"
        )
    if not artifact_path.is_file():
        raise ValueError(f"release artifact missing: {artifact_path}")
    if not certification_manifest_path.is_file():
        raise ValueError("production certification manifest missing")
    if not workflow_path.is_file():
        raise ValueError(f"release workflow missing: {WORKFLOW_PATH}")
    if not sbom_path.is_file():
        raise ValueError(f"production SBOM missing: {sbom_path}")
    if not reproducibility_manifest_path.is_file():
        raise ValueError("reproducibility manifest missing")
    if not dependency_lock_path.is_file():
        raise ValueError("production dependency lock missing")
    if not build_environment_path.is_file():
        raise ValueError("build environment manifest missing")
    if not build_lock_path.is_file():
        raise ValueError("build toolchain lock missing")

    build_environment = json.loads(
        build_environment_path.read_text(encoding="utf-8")
    )

    if build_environment.get("schema") != "aodsl.build-environment.v1":
        raise ValueError("invalid build environment schema")
    if build_environment.get("invariant") != "INV-054":
        raise ValueError("invalid build environment invariant")

    build_toolchain = build_environment.get("build_toolchain", {})
    if build_toolchain.get("lock_path") != "requirements/build.lock":
        raise ValueError("build environment lock path mismatch")

    actual_build_lock_sha = _sha256_file(build_lock_path)
    declared_build_lock_sha = build_toolchain.get("lock_sha256", "")
    _require(
        declared_build_lock_sha,
        HEX64,
        "build environment lock SHA-256",
    )
    if actual_build_lock_sha != declared_build_lock_sha:
        raise ValueError("build environment lock SHA-256 mismatch")

    oci = build_environment.get("oci", {})
    oci_digest = oci.get("digest", "")
    oci_reference = oci.get("reference", "")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", oci_digest):
        raise ValueError("invalid build environment OCI digest")
    if oci_reference != f'{oci.get("image", "")}@{oci_digest}':
        raise ValueError("build environment OCI reference mismatch")

    cert = json.loads(certification_manifest_path.read_text())
    source_sha = cert.get("source", {}).get("canonical_tree_sha256", "")
    _require(source_sha, HEX64, "certified source SHA-256")
    artifact_sha = _sha256_file(artifact_path)
    sbom_sha = _sha256_file(sbom_path)

    reproducibility = json.loads(
        reproducibility_manifest_path.read_text(encoding="utf-8")
    )
    if not isinstance(reproducibility, dict):
        raise ValueError("reproducibility manifest must be a JSON object")

    if (
        reproducibility.get("schema")
        != "aodsl.reproducible-release-artifact.v1"
    ):
        raise ValueError("invalid reproducibility manifest schema")
    if reproducibility.get("invariant") != "INV-055":
        raise ValueError("invalid reproducibility manifest invariant")

    comparison = reproducibility.get("comparison", {})
    if comparison.get("algorithm") != "sha256-and-byte-equality":
        raise ValueError("invalid reproducibility comparison algorithm")
    if comparison.get("independent_builds") != 2:
        raise ValueError("invalid reproducibility independent build count")
    if comparison.get("result") != "IDENTICAL":
        raise ValueError("reproducibility result is not IDENTICAL")

    expected_artifact_path = f"dist/{artifact_path.name}"
    expected_sbom_path = f"dist/{sbom_path.name}"

    reproducible_artifact = reproducibility.get("artifact", {})
    reproducible_sbom = reproducibility.get("sbom", {})

    if reproducible_artifact.get("path") != expected_artifact_path:
        raise ValueError("reproducibility artifact path mismatch")
    if reproducible_artifact.get("sha256") != artifact_sha:
        raise ValueError("reproducibility artifact SHA-256 mismatch")
    if reproducible_sbom.get("path") != expected_sbom_path:
        raise ValueError("reproducibility SBOM path mismatch")
    if reproducible_sbom.get("sha256") != sbom_sha:
        raise ValueError("reproducibility SBOM SHA-256 mismatch")

    return {
        "schema": SCHEMA,
        "hash_algorithm": HASH_ALGORITHM,
        "certification": {
            "manifest_path": "certification/production-certification-manifest.json",
            "manifest_sha256": _sha256_file(certification_manifest_path),
            "source_tree_sha256": source_sha,
        },
        "git": {"commit_sha": commit, "tag": tag},
        "github": {
            "repository": repo,
            "workflow_path": WORKFLOW_PATH,
            "workflow_ref": workflow_ref,
            "workflow_sha256": _sha256_file(workflow_path),
            "oidc_issuer": "https://token.actions.githubusercontent.com",
            "provenance_predicate_type": "https://slsa.dev/provenance/v1",
        },
        "artifact": {
            "path": f"dist/{artifact_path.name}",
            "sha256": artifact_sha,
            "provenance_subject_digest": {"sha256": artifact_sha},
        },
        "sbom": {
            "path": f"dist/{sbom_path.name}",
            "format": "CycloneDX",
            "spec_version": "1.6",
            "sha256": sbom_sha,
            "dependency_lock_path": "requirements/production.lock",
            "dependency_lock_sha256": _sha256_file(dependency_lock_path),
        },
        "reproducibility": {
            "manifest_path": "dist/reproducibility-manifest.json",
            "manifest_sha256": _sha256_file(
                reproducibility_manifest_path
            ),
            "schema": reproducibility["schema"],
            "invariant": reproducibility["invariant"],
            "comparison_algorithm": comparison["algorithm"],
            "independent_builds": comparison["independent_builds"],
            "result": comparison["result"],
            "artifact_sha256": artifact_sha,
            "sbom_sha256": sbom_sha,
        },
        "build_environment": {
            "manifest_path": "architecture/build-environment.v1.json",
            "manifest_sha256": _sha256_file(build_environment_path),
            "oci_reference": oci_reference,
            "oci_digest": oci_digest,
            "platform": {
                "os": build_environment["platform"]["os"],
                "architecture": build_environment["platform"]["architecture"],
            },
            "python": {
                "implementation": build_environment["python"]["implementation"],
                "version": build_environment["python"]["version"],
            },
            "pip_version": build_environment["bootstrap"]["pip_version"],
            "build_lock_path": "requirements/build.lock",
            "build_lock_sha256": actual_build_lock_sha,
        },
    }


def verify_release_identity(
    root: Path,
    identity_path: Path,
    artifact_path: Path,
    certification_manifest_path: Path,
    sbom_path: Path,
    reproducibility_manifest_path: Path,
    *,
    git_commit_sha: str,
    git_tag: str,
    repository: str,
    workflow_ref: str,
) -> list[str]:
    try:
        stored = json.loads(Path(identity_path).read_text())
        expected = build_release_identity(
            root,
            artifact_path,
            certification_manifest_path,
            sbom_path,
            reproducibility_manifest_path,
            git_commit_sha=git_commit_sha,
            git_tag=git_tag,
            repository=repository,
            workflow_ref=workflow_ref,
        )
    except Exception as exc:
        return [str(exc)]
    errors = []
    if stored.get("schema") != SCHEMA:
        errors.append("release identity schema mismatch")
    if stored != expected:
        errors.append("release identity does not match certification/git/workflow/artifact")
    return errors
