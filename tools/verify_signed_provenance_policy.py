#!/usr/bin/env python3

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/production-release.yml"


def fail(message: str) -> None:
    print(f"PROV-001: FAILED — {message}")
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
    fail("workflow job ordering invalid")

build = workflow[build_start:final_start]
final = workflow[final_start:]

require(
    workflow,
    (
        "id-token: write",
        "attestations: write",
        "contents: read",
    ),
    "workflow permissions",
)

require(
    build,
    (
        "build: [a, b]",
        "python tools/release_gate.py --production",
        "python tools/create_sbom.py",
        "python tools/verify_sbom.py",
        "name: aodsl-repro-${{ matrix.build }}",
    ),
    "reproducible-build",
)

require(
    final,
    (
        "needs: reproducible-build",
        "name: aodsl-repro-a",
        "name: aodsl-repro-b",
        "python tools/verify_reproducible_artifacts.py",
        "--output dist/reproducibility-manifest.json",
        "cp repro/a/aodsl-1.0.0-production-source.zip dist/",
        "cp repro/a/aodsl-1.0.0.cdx.json dist/",
        "rm -rf dist/certified-bundle-stage",
        "python tools/create_certified_bundle_manifest.py",
        "python tools/verify_certified_bundle_manifest.py",
        "dist/certified-bundle-manifest.json",
        "python tools/create_release_identity.py",
        "python tools/verify_release_identity.py",
        "uses: actions/attest@v4",
        "subject-path: 'dist/aodsl-*-production-source.zip'",
        "subject-path: 'dist/aodsl-*.cdx.json'",
        "dist/reproducibility-manifest.json",
        "dist/certified-bundle-manifest.json",
        "dist/certified-release-identity.json",
        "name: aodsl-certified-production",
    ),
    "production-gate",
)

if final.count("uses: actions/attest@v4") != 2:
    fail("production-gate must contain exactly two attestations")

compare = final.index(
    "python tools/verify_reproducible_artifacts.py"
)
promote_zip = final.index(
    "cp repro/a/aodsl-1.0.0-production-source.zip dist/"
)
promote_sbom = final.index(
    "cp repro/a/aodsl-1.0.0.cdx.json dist/"
)
identity_create = final.index(
    "python tools/create_release_identity.py"
)
identity_verify = final.index(
    "python tools/verify_release_identity.py"
)
zip_subject = final.index(
    "subject-path: 'dist/aodsl-*-production-source.zip'"
)
sbom_subject = final.index(
    "subject-path: 'dist/aodsl-*.cdx.json'"
)
certified_upload = final.index(
    "name: aodsl-certified-production"
)

if not (
    compare
    < promote_zip
    < promote_sbom
    < identity_create
    < identity_verify
    < zip_subject
    < sbom_subject
    < certified_upload
):
    fail(
        "required order is compare -> promote exact candidate -> "
        "release identity -> verify identity -> signed ZIP/SBOM "
        "provenance -> certified upload"
    )

for forbidden in (
    "python tools/release_gate.py --production",
    "python tools/create_sbom.py",
):
    if forbidden in final:
        fail(
            "final provenance job must not rebuild subject bytes: "
            + forbidden
        )

print("PROV-001: PASSED")
print(
    "Compared and identity-bound production bytes receive "
    "GitHub OIDC/Sigstore provenance before certified upload."
)
