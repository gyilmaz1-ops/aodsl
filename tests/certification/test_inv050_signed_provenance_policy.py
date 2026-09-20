from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
def text(): return (ROOT/".github/workflows/production-release.yml").read_text()
def test_oidc_and_attestation_permissions():
 s=text(); assert "id-token: write" in s and "attestations: write" in s and "contents: read" in s
def test_attestation_subject_is_production_artifact():
 s=text(); assert "uses: actions/attest@v4" in s; assert "subject-path: 'dist/aodsl-*-production-source.zip'" in s
def test_order_gate_then_attest_then_upload():
 s=text(); assert s.index("python tools/release_gate.py --production") < s.index("uses: actions/attest@v4") < s.index("uses: actions/upload-artifact@v4")
