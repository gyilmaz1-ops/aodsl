#!/usr/bin/env python3
from pathlib import Path

from aodsl.certification.evidence_promotion import promote_live_evidence

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "dist/live-certification-status.json"
CANONICAL = ROOT / "certification/evidence/live-certification-status.json"
RAW_ARCHIVE = ROOT / "certification/evidence/live-certification-status.raw.json"


def main() -> int:
    evidence = promote_live_evidence(ROOT, RAW, CANONICAL, RAW_ARCHIVE)
    print("INV-051 ATOMIC CERTIFICATION EVIDENCE PROMOTION: PASSED")
    print("tested_source_tree_sha256:", evidence["tested_source_tree_sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
