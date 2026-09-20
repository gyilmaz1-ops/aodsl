#!/usr/bin/env python3
from pathlib import Path
P=Path(__file__).resolve().parents[1]/".github/workflows/production-release.yml"
s=P.read_text() if P.is_file() else ""
need=["actions/checkout@v4",'python-version: "3.12"',"python tools/verify_production_attestation.py",
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
