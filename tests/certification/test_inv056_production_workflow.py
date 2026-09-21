from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "production-release.yml"


def text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def production_gate(workflow: str) -> str:
    marker = "  production-gate:"
    assert marker in workflow
    return workflow[workflow.index(marker):]


def test_inv056_final_gate_has_physical_closure_chain():
    final = production_gate(text())

    required = (
        "rm -rf dist/certified-bundle-stage",
        "dist/certified-bundle-stage/dist",
        "dist/certified-bundle-stage/certification/evidence",
        "python tools/create_certified_bundle_manifest.py",
        "--bundle-root dist/certified-bundle-stage",
        "python tools/verify_certified_bundle_manifest.py",
        "dist/certified-bundle-stage/dist/certified-bundle-manifest.json",
        "dist/certified-bundle-manifest.json",
        "python tools/create_release_identity.py",
        "python tools/verify_release_identity.py",
    )

    for value in required:
        assert value in final


def test_inv056_final_gate_orders_closure_before_identity():
    final = production_gate(text())

    compare = final.index(
        "python tools/verify_reproducible_artifacts.py"
    )
    promote = final.index(
        "cp repro/a/aodsl-1.0.0-production-source.zip dist/"
    )
    sbom_verify = final.index("python tools/verify_sbom.py")
    stage = final.index("rm -rf dist/certified-bundle-stage")
    create_bundle = final.index(
        "python tools/create_certified_bundle_manifest.py"
    )
    verify_bundle = final.index(
        "python tools/verify_certified_bundle_manifest.py"
    )
    promote_bundle = final.index(
        "dist/certified-bundle-manifest.json"
    )
    identity = final.index("python tools/create_release_identity.py")
    identity_verify = final.index(
        "python tools/verify_release_identity.py"
    )
    attest = final.index("uses: actions/attest@v4")
    upload = final.index("name: aodsl-certified-production")

    assert (
        compare
        < promote
        < sbom_verify
        < stage
        < create_bundle
        < verify_bundle
        < promote_bundle
        < identity
        < identity_verify
        < attest
        < upload
    )


def test_inv056_stage_copies_exact_seven_payloads():
    final = production_gate(text())

    start = final.index("- name: Stage exact certified bundle payload")
    end = final.index("- name: Create physical certified bundle manifest")
    block = final[start:end]

    expected_sources = (
        "dist/aodsl-1.0.0-production-source.zip",
        "dist/aodsl-1.0.0.cdx.json",
        "dist/release-manifest.json",
        "dist/reproducibility-manifest.json",
        "certification/production-certification-manifest.json",
        "certification/production-certification-attestation.json",
        "certification/evidence/live-certification-status.json",
    )

    copy_lines = [
        line.strip()
        for line in block.splitlines()
        if line.strip().startswith("cp ")
    ]
    assert len(copy_lines) == 7

    for source in expected_sources:
        assert source in block

    assert "*" not in block
    assert "cp -r" not in block
    assert "cp -R" not in block


def test_inv056_manifest_is_promoted_by_exact_bytes():
    final = production_gate(text())

    assert (
        "dist/certified-bundle-stage/dist/certified-bundle-manifest.json"
        in final
    )
    assert "dist/certified-bundle-manifest.json" in final
    assert (
        "cmp "
        "dist/certified-bundle-stage/dist/"
        "certified-bundle-manifest.json "
        "dist/certified-bundle-manifest.json"
        in final
    )


def test_inv056_certified_upload_is_explicit_and_excludes_stage():
    final = production_gate(text())

    start = final.index("- name: Upload certified artifact")
    upload = final[start:]

    expected = (
        "dist/aodsl-*-production-source.zip",
        "dist/aodsl-*.cdx.json",
        "dist/release-manifest.json",
        "dist/reproducibility-manifest.json",
        "dist/certified-bundle-manifest.json",
        "dist/certified-release-identity.json",
        "certification/production-certification-manifest.json",
        "certification/production-certification-attestation.json",
        "certification/evidence/live-certification-status.json",
    )

    for path in expected:
        assert path in upload

    assert "dist/certified-bundle-stage" not in upload


def test_inv056_release_identity_cli_uses_stage_root():
    create = (
        ROOT / "tools" / "create_release_identity.py"
    ).read_text(encoding="utf-8")
    verify = (
        ROOT / "tools" / "verify_release_identity.py"
    ).read_text(encoding="utf-8")

    required = 'DIST / "certified-bundle-stage"'

    assert required in create
    assert required in verify
