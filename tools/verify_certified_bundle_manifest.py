#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from aodsl.certification.certified_bundle import (
    BUNDLE_MANIFEST_PATH,
    verify_certified_bundle_manifest,
)


parser = argparse.ArgumentParser()
parser.add_argument(
    "--bundle-root",
    required=True,
    type=Path,
)
args = parser.parse_args()

root = args.bundle_root.resolve()

errors = verify_certified_bundle_manifest(
    root,
    root / BUNDLE_MANIFEST_PATH,
)

if errors:
    print("INV-056 CERTIFIED BUNDLE: FAILED")
    for error in errors:
        print("-", error)
    raise SystemExit(2)

print("INV-056 CERTIFIED BUNDLE: PASSED")
