#!/usr/bin/env python3

from pathlib import Path

from aodsl.certification.sbom import verify_installed_dependencies

ROOT = Path(__file__).resolve().parents[1]

errors = verify_installed_dependencies(ROOT)

if errors:
    print("INV-053 PRODUCTION DEPENDENCIES: FAILED")
    for error in errors:
        print("-", error)
    raise SystemExit(2)

print("INV-053 PRODUCTION DEPENDENCIES: PASSED")
