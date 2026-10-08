#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path

from aodsl.certification.release_identity import build_release_identity
from aodsl.certification.version_policy import (
    read_project_version,
    artifact_names,
    validate_artifact_binding,
)

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
artifacts = sorted(DIST.glob("aodsl-*-production-source.zip"))
if len(artifacts) != 1:
    raise SystemExit(f"INV-052: expected exactly one production artifact, found {len(artifacts)}")

version = read_project_version(ROOT / 'pyproject.toml')
expected_artifact, expected_sbom = artifact_names(version)
validate_artifact_binding(
    pyproject_path=ROOT / 'pyproject.toml',
    artifact_name=artifacts[0].name,
    sbom_name=expected_sbom,
)

def need(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"INV-052: required environment variable missing: {name}")
    return value

tag = need("GITHUB_REF_NAME")
repo = need("GITHUB_REPOSITORY")
commit = need("GITHUB_SHA")
ref_type = need("GITHUB_REF_TYPE")
if ref_type != "tag":
    raise SystemExit(f"INV-052: production release must run from tag, got {ref_type!r}")

workflow_ref = f"{repo}/.github/workflows/production-release.yml@refs/tags/{tag}"
identity = build_release_identity(
    ROOT,
    artifacts[0],
    ROOT / "certification/production-certification-manifest.json",
    DIST / expected_sbom,
    DIST / "reproducibility-manifest.json",
    DIST / "certified-bundle-stage",
    DIST / "certified-bundle-manifest.json",
    git_commit_sha=commit,
    git_tag=tag,
    repository=repo,
    workflow_ref=workflow_ref,
    version=version,
)
out = DIST / "certified-release-identity.json"
out.write_text(json.dumps(identity, indent=2, sort_keys=True) + "\n")
print("INV-052 CERTIFIED RELEASE IDENTITY: CREATED")
print("Git commit:", commit)
print("Git tag:", tag)
print("Workflow SHA256:", identity["github"]["workflow_sha256"])
print("Artifact SHA256:", identity["artifact"]["sha256"])
