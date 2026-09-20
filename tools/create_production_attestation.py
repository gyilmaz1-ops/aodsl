#!/usr/bin/env python3
import json
from pathlib import Path
from aodsl.certification.attestation import create_attestation
R=Path(__file__).resolve().parents[1]; E=R/"certification/evidence/live-certification-status.json"; M=R/"certification/production-certification-manifest.json"; A=R/"certification/production-certification-attestation.json"
m,a=create_attestation(R,E); M.write_text(json.dumps(m,indent=2,sort_keys=True)+"\n"); A.write_text(json.dumps(a,indent=2,sort_keys=True)+"\n")
print(json.dumps({"status":"CREATED","source_tree_sha256":m["source"]["canonical_tree_sha256"],"architecture_registry_sha256":m["architecture"]["registry_sha256"],"live_evidence_sha256":m["evidence"]["sha256"],"manifest_sha256":a["manifest_sha256"]},indent=2,sort_keys=True))
