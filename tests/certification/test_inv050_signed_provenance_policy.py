from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/production-release.yml"


def text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def production_gate(workflow: str) -> str:
    marker = "  production-gate:\n"
    assert marker in workflow
    return workflow[workflow.index(marker):]


def test_oidc_and_attestation_permissions():
    s = text()
    assert "id-token: write" in s
    assert "attestations: write" in s
    assert "contents: read" in s


def test_attestation_subject_is_production_artifact():
    final = production_gate(text())

    assert "uses: actions/attest@v4" in final
    assert (
        "subject-path: 'dist/aodsl-*-production-source.zip'"
        in final
    )


def test_final_gate_orders_identity_attestation_and_certified_upload():
    final = production_gate(text())

    identity_create = (
        "python tools/create_release_identity.py"
    )
    identity_verify = (
        "python tools/verify_release_identity.py"
    )
    attest = "uses: actions/attest@v4"
    certified_upload = "name: aodsl-certified-production"

    for required in (
        identity_create,
        identity_verify,
        attest,
        certified_upload,
    ):
        assert required in final

    assert (
        final.index(identity_create)
        < final.index(identity_verify)
        < final.index(attest)
        < final.index(certified_upload)
    )


def test_final_gate_attests_only_after_reproducibility_comparison():
    final = production_gate(text())

    compare = "python tools/verify_reproducible_artifacts.py"
    identity = "python tools/create_release_identity.py"
    attest = "uses: actions/attest@v4"

    assert compare in final

    assert (
        final.index(compare)
        < final.index(identity)
        < final.index(attest)
    )
