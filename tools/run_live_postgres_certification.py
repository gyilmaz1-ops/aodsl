#!/usr/bin/env python3
import json
from pathlib import Path

from aodsl.certification.evidence_promotion import promote_live_evidence
from aodsl.certification.gate import certification_status
from aodsl.certification.source_binding import canonical_source_tree_sha256

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "dist/live-certification-status.json"
CANONICAL = ROOT / "certification/evidence/live-certification-status.json"
RAW_ARCHIVE = ROOT / "certification/evidence/live-certification-status.raw.json"


def main() -> int:
    status = certification_status()
    status["tested_source_tree_sha256"] = canonical_source_tree_sha256(ROOT)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(status, indent=2, sort_keys=True) + "\n")
    print(json.dumps(status, indent=2, sort_keys=True))
    if status.get("production_status") != "CERTIFIED":
        return 2
    promote_live_evidence(ROOT, OUT, CANONICAL, RAW_ARCHIVE)
    print("INV-051 ATOMIC CERTIFICATION EVIDENCE PROMOTION: PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
