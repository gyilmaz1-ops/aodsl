#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from aodsl.certification.version_policy import (
    artifact_names,
    read_project_version,
)

# Backward-compatible release artifact names.
PROJECT_VERSION = read_project_version(
    Path(__file__).resolve().parents[1] / "pyproject.toml"
)
ARTIFACT_NAME, SBOM_NAME = artifact_names(PROJECT_VERSION)

SCHEMA = "aodsl.reproducible-release-artifact.v2"


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



def validate_release_manifest(
    path: Path,
    *,
    version: str,
    artifact_name: str,
    artifact_sha: str,
    certification_manifest_path: Path,
) -> None:
    """Validate production release metadata and certification binding."""
    try:
        manifest = json.loads(path.read_bytes())
    except (OSError, UnicodeError, ValueError) as exc:
        raise ReproducibilityError(
            f"invalid release manifest JSON: {exc}"
        ) from exc

    if not isinstance(manifest, dict):
        raise ReproducibilityError(
            "release manifest must be a JSON object"
        )

    expected = {
        "version": version,
        "mode": "production",
        "artifact": artifact_name,
        "sha256": artifact_sha,
    }

    for key, value in expected.items():
        if manifest.get(key) != value:
            raise ReproducibilityError(
                f"invalid release manifest {key}"
            )

    files = manifest.get("files")
    if type(files) is not int or files <= 0:
        raise ReproducibilityError(
            "invalid release manifest files"
        )

    for key in (
        "production_certification_manifest_sha256",
        "certified_source_tree_sha256",
    ):
        value = manifest.get(key)
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(c not in "0123456789abcdef" for c in value)
        ):
            raise ReproducibilityError(
                f"invalid release manifest {key}"
            )


    certification_manifest_path = Path(certification_manifest_path)

    try:
        certification_bytes = certification_manifest_path.read_bytes()
        certification = json.loads(certification_bytes)
    except (OSError, UnicodeError, ValueError) as exc:
        raise ReproducibilityError(
            f"invalid certification manifest: {exc}"
        ) from exc

    if not isinstance(certification, dict):
        raise ReproducibilityError(
            "certification manifest must be a JSON object"
        )

    source_binding = certification.get("source")
    if not isinstance(source_binding, dict):
        raise ReproducibilityError(
            "certification source binding missing"
        )

    certified_source_sha = source_binding.get(
        "canonical_tree_sha256"
    )

    if (
        not isinstance(certified_source_sha, str)
        or len(certified_source_sha) != 64
        or any(c not in "0123456789abcdef" for c in certified_source_sha)
    ):
        raise ReproducibilityError(
            "invalid certification source SHA-256"
        )

    expected_certification_sha = hashlib.sha256(
        certification_bytes
    ).hexdigest()

    if (
        manifest["production_certification_manifest_sha256"]
        != expected_certification_sha
    ):
        raise ReproducibilityError(
            "certification manifest SHA-256 mismatch"
        )

    if (
        manifest["certified_source_tree_sha256"]
        != certified_source_sha
    ):
        raise ReproducibilityError(
            "certified source-tree SHA-256 mismatch"
        )


def verify_reproducible_artifacts(
    build_a: Path,
    build_b: Path,
    *,
    version: str | None = None,
    certification_manifest_path: Path | None = None,
) -> dict:
    if certification_manifest_path is None:
        certification_manifest_path = (
            Path(__file__).resolve().parents[1]
            / "certification/production-certification-manifest.json"
        )

    if version is None:
        version = read_project_version(
            Path(__file__).resolve().parents[1] / "pyproject.toml"
        )

    artifact_name, sbom_name = artifact_names(version)

    build_a = Path(build_a)
    build_b = Path(build_b)

    artifact_sha = compare_file(
        build_a / artifact_name,
        build_b / artifact_name,
        "production artifact",
    )
    sbom_sha = compare_file(
        build_a / sbom_name,
        build_b / sbom_name,
        "SBOM",
    )

    manifest_name = "release-manifest.json"
    manifest_sha = compare_file(
        build_a / manifest_name,
        build_b / manifest_name,
        "release manifest",
    )

    validate_release_manifest(
        build_a / manifest_name,
        version=version,
        artifact_name=artifact_name,
        artifact_sha=artifact_sha,
        certification_manifest_path=certification_manifest_path,
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
            "path": f"dist/{artifact_name}",
            "sha256": artifact_sha,
        },
        "sbom": {
            "path": f"dist/{sbom_name}",
            "sha256": sbom_sha,
        },
        "release_manifest": {
            "path": "dist/release-manifest.json",
            "sha256": manifest_sha,
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
