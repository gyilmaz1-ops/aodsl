from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

SCHEMA = "aodsl.certified-release-identity.v2"
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
    dependency_lock_path = root / "requirements/production.lock"
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
    if not dependency_lock_path.is_file():
        raise ValueError("production dependency lock missing")

    cert = json.loads(certification_manifest_path.read_text())
    source_sha = cert.get("source", {}).get("canonical_tree_sha256", "")
    _require(source_sha, HEX64, "certified source SHA-256")
    artifact_sha = _sha256_file(artifact_path)

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
            "sha256": _sha256_file(sbom_path),
            "dependency_lock_path": "requirements/production.lock",
            "dependency_lock_sha256": _sha256_file(dependency_lock_path),
        },
    }


def verify_release_identity(
    root: Path,
    identity_path: Path,
    artifact_path: Path,
    certification_manifest_path: Path,
    sbom_path: Path,
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
