#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from aodsl.certification.release_identity import build_release_identity

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
artifacts = sorted(DIST.glob("aodsl-*-production-source.zip"))
if len(artifacts) != 1:
    raise SystemExit(f"INV-052: expected exactly one production artifact, found {len(artifacts)}")

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

def git(*args: str) -> str:
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("INV-052: git identity check failed: " + r.stderr.strip())
    return r.stdout.strip()

head = git("rev-parse", "HEAD")
if head != commit:
    raise SystemExit(f"INV-052: checkout/commit mismatch: HEAD={head!r}, GITHUB_SHA={commit!r}")
tag_commit = git("rev-list", "-n", "1", f"refs/tags/{tag}")
if tag_commit != commit:
    raise SystemExit(f"INV-052: tag/commit mismatch: tag={tag_commit!r}, GITHUB_SHA={commit!r}")
workflow_ref = f"{repo}/.github/workflows/production-release.yml@refs/tags/{tag}"
identity = build_release_identity(
    ROOT,
    artifacts[0],
    ROOT / "certification/production-certification-manifest.json",
    git_commit_sha=commit,
    git_tag=tag,
    repository=repo,
    workflow_ref=workflow_ref,
)
out = DIST / "certified-release-identity.json"
out.write_text(json.dumps(identity, indent=2, sort_keys=True) + "\n")
print("INV-052 CERTIFIED RELEASE IDENTITY: CREATED")
print("Git commit:", commit)
print("Git tag:", tag)
print("Workflow SHA256:", identity["github"]["workflow_sha256"])
print("Artifact SHA256:", identity["artifact"]["sha256"])
