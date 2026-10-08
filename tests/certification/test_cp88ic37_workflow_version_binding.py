"""VERSION-011 production workflow version-binding contracts."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/production-release.yml"


def workflow_source():
    return WORKFLOW.read_text(encoding="utf-8")


def test_workflow_contains_no_fixed_100_artifact_names():
    source = workflow_source()
    assert "aodsl-1.0.0-production-source.zip" not in source
    assert "aodsl-1.0.0.cdx.json" not in source


def test_workflow_uses_authoritative_version_source():
    source = workflow_source()
    assert "pyproject.toml" in source
    assert "read_project_version" in source


def test_workflow_has_version_binding_in_both_jobs():
    source = workflow_source()

    build = source.split("  production-gate:", 1)[0]
    promotion = source.split("  production-gate:", 1)[1]

    assert "read_project_version" in build
    assert "read_project_version" in promotion


def test_workflow_preserves_production_certification():
    source = workflow_source()

    assert "python tools/release_gate.py --production" in source
    assert "python tools/verify_production_attestation.py" in source
    assert "python tools/verify_release_identity.py" in source


def test_workflow_preserves_reproducibility_comparison():
    source = workflow_source()

    assert "python tools/verify_reproducible_artifacts.py" in source
    assert "repro/a repro/b" in source
