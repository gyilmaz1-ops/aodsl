#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ARTIFACT_NAME = "aodsl-1.0.0-production-source.zip"
SBOM_NAME = "aodsl-1.0.0.cdx.json"
SCHEMA = "aodsl.reproducible-release-artifact.v1"


class ReproducibilityError(RuntimeError):
    """INV-055 fail-closed reproducibility verification error."""


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_pair(left: Path, right: Path, label: str) -> str:
    """Verify two files are byte-for-byte identical, fail closed otherwise."""
    left = Path(left)
    right = Path(right)

    if not left.is_file():
        raise ReproducibilityError(
            f"missing artifact: build A {label}: {left}"
        )
    if not right.is_file():
        raise ReproducibilityError(
            f"missing artifact: build B {label}: {right}"
        )

    left_bytes = left.read_bytes()
    right_bytes = right.read_bytes()

    if left_bytes != right_bytes:
        raise ReproducibilityError(
            f"byte-for-byte mismatch for {label}: "
            f"build_a={sha256_file(left)}, "
            f"build_b={sha256_file(right)}"
        )

    return hashlib.sha256(left_bytes).hexdigest()


def compare_file(left: Path, right: Path, label: str) -> str:
    """Compatibility wrapper over the canonical pair verifier."""
    return verify_pair(left, right, label)


def verify_reproducible_artifacts(
    build_a: Path,
    build_b: Path,
) -> dict:
    build_a = Path(build_a)
    build_b = Path(build_b)

    artifact_sha = compare_file(
        build_a / ARTIFACT_NAME,
        build_b / ARTIFACT_NAME,
        "production artifact",
    )
    sbom_sha = compare_file(
        build_a / SBOM_NAME,
        build_b / SBOM_NAME,
        "SBOM",
    )

    return {
        "schema": SCHEMA,
        "invariant": "INV-055",
        "comparison": {
            "algorithm": "sha256-and-byte-equality",
            "independent_builds": 2,
            "result": "IDENTICAL",
        },
        "artifact": {
            "path": f"dist/{ARTIFACT_NAME}",
            "sha256": artifact_sha,
        },
        "sbom": {
            "path": f"dist/{SBOM_NAME}",
            "sha256": sbom_sha,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("build_a", type=Path)
    parser.add_argument("build_b", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    try:
        evidence = verify_reproducible_artifacts(
            args.build_a,
            args.build_b,
        )
    except Exception as exc:
        print(f"INV-055 REPRODUCIBILITY: FAILED: {exc}")
        return 2

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    print("INV-055 REPRODUCIBILITY: PASSED")
    print("Artifact SHA256:", evidence["artifact"]["sha256"])
    print("SBOM SHA256:", evidence["sbom"]["sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
