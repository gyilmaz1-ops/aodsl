#!/usr/bin/env python3
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/".github/workflows/production-release.yml"
s=P.read_text() if P.is_file() else ""
required={
 "oidc permission":"id-token: write",
 "attestation permission":"attestations: write",
 "read-only contents":"contents: read",
 "production gate":"python tools/release_gate.py --production",
 "github attest v4":"uses: actions/attest@v4",
 "production subject":"subject-path: 'dist/aodsl-*-production-source.zip'",
 "release identity creation":"python tools/create_release_identity.py",
 "release identity verification":"python tools/verify_release_identity.py",
 "release identity upload":"dist/certified-release-identity.json",
 "upload":"uses: actions/upload-artifact@v4",
}
missing=[k for k,v in required.items() if v not in s]
if missing:
 print("PROV-001: FAILED — missing: "+", ".join(missing)); raise SystemExit(2)
gate=s.index("python tools/release_gate.py --production")
identity_create=s.index("python tools/create_release_identity.py")
identity_verify=s.index("python tools/verify_release_identity.py")
att=s.index("uses: actions/attest@v4")
upload=s.index("uses: actions/upload-artifact@v4")
if not gate < identity_create < identity_verify < att < upload:
 print("PROV-001: FAILED — required order is production gate -> release identity -> verify identity -> signed provenance -> upload")
 raise SystemExit(2)
print("PROV-001: PASSED")
print("Production artifact is subject to GitHub OIDC/Sigstore build provenance before upload.")
