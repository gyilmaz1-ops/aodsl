#!/usr/bin/env python3

from pathlib import Path

from aodsl.certification.sbom import (
    LOCK_PATH,
    sha256_file,
    write_sbom,
)

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
OUTPUT = DIST / "aodsl-1.0.0.cdx.json"

write_sbom(ROOT, OUTPUT)

print("INV-053 DETERMINISTIC SBOM: CREATED")
print("SBOM:", OUTPUT.relative_to(ROOT))
print("SBOM SHA256:", sha256_file(OUTPUT))
print("Dependency lock:", LOCK_PATH)
print("Dependency lock SHA256:", sha256_file(ROOT / LOCK_PATH))
