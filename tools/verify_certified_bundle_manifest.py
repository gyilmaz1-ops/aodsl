#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from aodsl.certification.version_policy import read_project_version

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

try:
    version = read_project_version(
        Path(__file__).resolve().parents[1] / 'pyproject.toml'
    )
    errors = verify_certified_bundle_manifest(
        root,
        root / BUNDLE_MANIFEST_PATH,
        version=version,
    )
except Exception as exc:
    errors = [str(exc)]

if errors:
    print("INV-056 CERTIFIED BUNDLE: FAILED")
    for error in errors:
        print("-", error)
    raise SystemExit(2)

print("INV-056 CERTIFIED BUNDLE: PASSED")
