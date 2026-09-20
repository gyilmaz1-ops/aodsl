#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path

from aodsl.certification.release_identity import verify_release_identity

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
artifacts = sorted(DIST.glob("aodsl-*-production-source.zip"))
if len(artifacts) != 1:
    print(f"INV-052: FAILED — expected exactly one production artifact, found {len(artifacts)}")
    raise SystemExit(2)
required = ["GITHUB_SHA", "GITHUB_REF_NAME", "GITHUB_REF_TYPE", "GITHUB_REPOSITORY"]
missing = [x for x in required if not os.environ.get(x, "").strip()]
if missing:
    print("INV-052: FAILED — missing environment: " + ", ".join(missing))
    raise SystemExit(2)
if os.environ["GITHUB_REF_TYPE"] != "tag":
    print("INV-052: FAILED — release ref is not a tag")
    raise SystemExit(2)
repo = os.environ["GITHUB_REPOSITORY"]
tag = os.environ["GITHUB_REF_NAME"]
workflow_ref = f"{repo}/.github/workflows/production-release.yml@refs/tags/{tag}"
errors = verify_release_identity(
    ROOT,
    DIST / "certified-release-identity.json",
    artifacts[0],
    ROOT / "certification/production-certification-manifest.json",
    git_commit_sha=os.environ["GITHUB_SHA"],
    git_tag=tag,
    repository=repo,
    workflow_ref=workflow_ref,
)
if errors:
    print("INV-052: FAILED")
    for e in errors:
        print("-", e)
    raise SystemExit(2)
print("INV-052 CERTIFIED RELEASE IDENTITY: PASSED")
