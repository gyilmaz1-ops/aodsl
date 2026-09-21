#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from aodsl.certification.certified_bundle import (
    BUNDLE_MANIFEST_PATH,
    build_certified_bundle_manifest,
)


parser = argparse.ArgumentParser()
parser.add_argument(
    "--bundle-root",
    required=True,
    type=Path,
)
args = parser.parse_args()

root = args.bundle_root.resolve()
out = root / BUNDLE_MANIFEST_PATH

try:
    manifest = build_certified_bundle_manifest(root)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
except Exception as exc:
    print(f"INV-056 CERTIFIED BUNDLE: FAILED: {exc}")
    raise SystemExit(2)

print("INV-056 CERTIFIED BUNDLE: CREATED")
print("Manifest:", out)
print("Payload count:", manifest["closure"]["payload_count"])
