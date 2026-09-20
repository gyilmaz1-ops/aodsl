#!/usr/bin/env python3

from pathlib import Path

from aodsl.certification.sbom import (
    verify_installed_dependencies,
    verify_sbom,
)

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"

sboms = sorted(DIST.glob("aodsl-*.cdx.json"))

if len(sboms) != 1:
    print(
        "INV-053: FAILED — "
        f"expected exactly one production SBOM, found {len(sboms)}"
    )
    raise SystemExit(2)

errors = verify_sbom(ROOT, sboms[0])
errors.extend(verify_installed_dependencies(ROOT))

if errors:
    print("INV-053: FAILED")
    for error in errors:
        print("-", error)
    raise SystemExit(2)

print("INV-053 DETERMINISTIC SBOM: PASSED")
print("INV-053 INSTALLED DEPENDENCIES: PASSED")
