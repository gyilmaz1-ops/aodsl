from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/production-release.yml"

EXACT_IMAGE = (
    "python@sha256:"
    "47ae396f09c1303b8653019811a8498470603d7ffefc29cb07c88f1f8cb3d19f"
)


def workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def job_block(text: str, job: str, next_job: str | None = None) -> str:
    marker = f"  {job}:\n"
    assert marker in text, f"missing workflow job: {job}"

    start = text.index(marker)

    if next_job is None:
        return text[start:]

    end_marker = f"  {next_job}:\n"
    assert end_marker in text, f"missing workflow job: {next_job}"

    end = text.index(end_marker, start + len(marker))
    return text[start:end]


def assert_order(block: str, *needles: str) -> None:
    positions = []
    for needle in needles:
        assert needle in block, f"missing workflow step: {needle}"
        positions.append(block.index(needle))

    assert positions == sorted(positions), (
        "workflow trust-chain order mismatch: "
        + " -> ".join(needles)
    )


def test_inv055_uses_two_independent_matrix_build_executions():
    workflow = workflow_text()
    build = job_block(
        workflow,
        "reproducible-build",
        "production-gate",
    )

    assert "strategy:" in build
    assert "matrix:" in build
    assert "build: [a, b]" in build
    assert "fail-fast: false" in build

    assert f"image: {EXACT_IMAGE}" in build

    assert "python tools/verify_build_environment.py" in build
    assert "-r requirements/build.lock" in build
    assert "-r requirements/production.lock" in build
    assert (
        "python -m pip install --no-deps --no-build-isolation -e ."
        in build
    )
    assert "python tools/verify_production_dependencies.py" in build
    assert "python tools/verify_production_attestation.py" in build
    assert "python tools/release_gate.py --production" in build
    assert "python tools/create_sbom.py" in build
    assert "python tools/verify_sbom.py" in build

    assert "name: aodsl-repro-${{ matrix.build }}" in build
    assert "uses: actions/upload-artifact@v4" in build

    assert_order(
        build,
        "python tools/verify_build_environment.py",
        "-r requirements/build.lock",
        "-r requirements/production.lock",
        "python tools/verify_production_dependencies.py",
        "python tools/verify_production_attestation.py",
        "python tools/release_gate.py --production",
        "python tools/create_sbom.py",
        "python tools/verify_sbom.py",
        "uses: actions/upload-artifact@v4",
    )


def test_inv055_final_gate_compares_then_promotes_without_rebuild():
    workflow = workflow_text()
    final = job_block(workflow, "production-gate")

    assert "needs: reproducible-build" in final
    assert f"image: {EXACT_IMAGE}" in final

    assert "name: aodsl-repro-a" in final
    assert "name: aodsl-repro-b" in final
    assert "uses: actions/download-artifact@v4" in final

    compare_parts = (
        "python tools/verify_reproducible_artifacts.py",
        "repro/a repro/b",
        "--output dist/reproducibility-manifest.json",
    )
    for part in compare_parts:
        assert part in final

    # Final gate must consume already-compared candidate bytes.
    # It must never rebuild the ZIP or regenerate the SBOM.
    assert "python tools/release_gate.py --production" not in final
    assert "python tools/create_sbom.py" not in final

    assert "cp repro/a/aodsl-1.0.0-production-source.zip dist/" in final
    assert "cp repro/a/aodsl-1.0.0.cdx.json dist/" in final
    assert "cp repro/a/release-manifest.json dist/" in final

    assert "python tools/create_release_identity.py" in final
    assert "python tools/verify_release_identity.py" in final

    assert "subject-path: 'dist/aodsl-*-production-source.zip'" in final
    assert "subject-path: 'dist/aodsl-*.cdx.json'" in final

    assert "name: aodsl-certified-production" in final
    assert "dist/reproducibility-manifest.json" in final
    assert "dist/certified-release-identity.json" in final

    assert_order(
        final,
        "python tools/verify_reproducible_artifacts.py",
        "repro/a repro/b",
        "--output dist/reproducibility-manifest.json",
        "cp repro/a/aodsl-1.0.0-production-source.zip dist/",
        "cp repro/a/aodsl-1.0.0.cdx.json dist/",
        "python tools/create_release_identity.py",
        "python tools/verify_release_identity.py",
        "subject-path: 'dist/aodsl-*-production-source.zip'",
        "subject-path: 'dist/aodsl-*.cdx.json'",
        "name: aodsl-certified-production",
    )


def test_inv055_final_gate_downloads_distinct_candidate_artifacts():
    workflow = workflow_text()
    final = job_block(workflow, "production-gate")

    assert final.count("uses: actions/download-artifact@v4") == 2

    assert "name: aodsl-repro-a" in final
    assert "path: repro/a" in final

    assert "name: aodsl-repro-b" in final
    assert "path: repro/b" in final


def test_inv055_reproducibility_evidence_is_in_certified_bundle():
    workflow = workflow_text()
    final = job_block(workflow, "production-gate")

    identity = final.index(
        "python tools/create_release_identity.py"
    )
    identity_verify = final.index(
        "python tools/verify_release_identity.py"
    )
    attest = final.index("uses: actions/attest@v4")
    certified_upload = final.index(
        "name: aodsl-certified-production"
    )

    assert identity < identity_verify < attest < certified_upload
    assert "dist/reproducibility-manifest.json" in final[
        certified_upload:
    ]
