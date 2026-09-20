#!/usr/bin/env python3
import json
from pathlib import Path
from aodsl.certification.attestation import verify_attestation
R=Path(__file__).resolve().parents[1]; E=R/"certification/evidence/live-certification-status.json"; M=R/"certification/production-certification-manifest.json"; A=R/"certification/production-certification-attestation.json"
e=verify_attestation(R,E,M,A); print(json.dumps({"status":"NOT_CERTIFIED" if e else "CERTIFIED","verification":"FAIL_CLOSED","errors":e},indent=2,sort_keys=True)); raise SystemExit(2 if e else 0)
