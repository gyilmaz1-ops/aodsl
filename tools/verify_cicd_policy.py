#!/usr/bin/env python3
from pathlib import Path
P=Path(__file__).resolve().parents[1]/".github/workflows/production-release.yml"
s=P.read_text() if P.is_file() else ""
need=[
"actions/checkout@v4",
"image: python@sha256:47ae396f09c1303b8653019811a8498470603d7ffefc29cb07c88f1f8cb3d19f",
"python tools/verify_build_environment.py",
"-r requirements/build.lock",
"--require-hashes",
"--only-binary=:all:",
"python -m pip install --no-deps --no-build-isolation -e .",
"python tools/verify_production_attestation.py",
"python tools/release_gate.py --production","actions/upload-artifact@v4","dist/aodsl-*-production-source.zip",
"dist/release-manifest.json","certification/production-certification-manifest.json",
"certification/production-certification-attestation.json","certification/evidence/live-certification-status.json",
"if-no-files-found: error"]
missing=[x for x in need if x not in s]
ordered=(not missing and s.index("python tools/release_gate.py --production") < s.index("actions/upload-artifact@v4"))
if missing or not ordered:
 print("CI-PROD-001: FAILED")
 raise SystemExit(2)
print("CI-PROD-001: PASSED")
