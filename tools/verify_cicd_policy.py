#!/usr/bin/env python3

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/production-release.yml"
EXACT_IMAGE = (
    "python@sha256:"
    "47ae396f09c1303b8653019811a8498470603d7ffefc29cb07c88f1f8cb3d19f"
)


def fail(message: str) -> None:
    print(f"CI-PROD-001: FAILED — {message}")
    raise SystemExit(2)


def require(block: str, values, scope: str) -> None:
    missing = [value for value in values if value not in block]
    if missing:
        fail(
            f"{scope} missing: "
            + ", ".join(missing)
        )


if not WORKFLOW.is_file():
    fail("production workflow missing")

workflow = WORKFLOW.read_text(encoding="utf-8")

build_marker = "  reproducible-build:\n"
final_marker = "  production-gate:\n"

if build_marker not in workflow:
    fail("reproducible-build job missing")
if final_marker not in workflow:
    fail("production-gate job missing")

build_start = workflow.index(build_marker)
final_start = workflow.index(final_marker)

if not build_start < final_start:
    fail("reproducible-build must precede production-gate")

build = workflow[build_start:final_start]
final = workflow[final_start:]

require(
    workflow,
    (
        "contents: read",
        "id-token: write",
        "attestations: write",
    ),
    "workflow",
)

require(
    build,
    (
        "strategy:",
        "fail-fast: false",
        "build: [a, b]",
        f"image: {EXACT_IMAGE}",
        "actions/checkout@v4",
        "python tools/verify_build_environment.py",
        "--require-hashes",
        "--only-binary=:all:",
        "-r requirements/build.lock",
        "-r requirements/production.lock",
        "python -m pip install "
        "--no-deps --no-build-isolation -e .",
        "python tools/verify_production_dependencies.py",
        "python tools/verify_production_attestation.py",
        "python tools/release_gate.py --production",
        "python tools/create_sbom.py",
        "python tools/verify_sbom.py",
        "name: aodsl-repro-${{ matrix.build }}",
        "uses: actions/upload-artifact@v4",
    ),
    "reproducible-build",
)

build_order = (
    "python tools/verify_build_environment.py",
    "-r requirements/build.lock",
    "-r requirements/production.lock",
    "python tools/verify_production_dependencies.py",
    "python tools/verify_production_attestation.py",
    "python tools/release_gate.py --production",
    "python tools/create_sbom.py",
    "python tools/verify_sbom.py",
    "name: aodsl-repro-${{ matrix.build }}",
)

positions = [build.index(value) for value in build_order]
if positions != sorted(positions):
    fail("reproducible-build trust-chain ordering invalid")

require(
    final,
    (
        "needs: reproducible-build",
        f"image: {EXACT_IMAGE}",
        "actions/checkout@v4",
        "python tools/verify_build_environment.py",
        "--require-hashes",
        "--only-binary=:all:",
        "-r requirements/build.lock",
        "-r requirements/production.lock",
        "python -m pip install "
        "--no-deps --no-build-isolation -e .",
        "python tools/verify_production_dependencies.py",
        "uses: actions/download-artifact@v4",
        "name: aodsl-repro-a",
        "path: repro/a",
        "name: aodsl-repro-b",
        "path: repro/b",
        "python tools/verify_reproducible_artifacts.py",
        "--output dist/reproducibility-manifest.json",
        "cp repro/a/aodsl-1.0.0-production-source.zip dist/",
        "cp repro/a/aodsl-1.0.0.cdx.json dist/",
        "cp repro/a/release-manifest.json dist/",
        "python tools/verify_sbom.py",
        "python tools/create_release_identity.py",
        "python tools/verify_release_identity.py",
        "name: aodsl-certified-production",
        "dist/reproducibility-manifest.json",
        "dist/certified-release-identity.json",
        "if-no-files-found: error",
    ),
    "production-gate",
)

if final.count("uses: actions/download-artifact@v4") != 2:
    fail("production-gate must download exactly two candidates")

for forbidden in (
    "python tools/release_gate.py --production",
    "python tools/create_sbom.py",
):
    if forbidden in final:
        fail(
            "production-gate must not rebuild certified "
            f"bytes: {forbidden}"
        )

final_order = (
    "name: aodsl-repro-a",
    "name: aodsl-repro-b",
    "python tools/verify_reproducible_artifacts.py",
    "cp repro/a/aodsl-1.0.0-production-source.zip dist/",
    "cp repro/a/aodsl-1.0.0.cdx.json dist/",
    "python tools/verify_sbom.py",
    "python tools/create_release_identity.py",
    "python tools/verify_release_identity.py",
    "name: aodsl-certified-production",
)

positions = [final.index(value) for value in final_order]
if positions != sorted(positions):
    fail("production-gate trust-chain ordering invalid")

print("CI-PROD-001: PASSED")
print(
    "Two independent candidate executions are compared before "
    "exact-byte promotion and certification."
)
