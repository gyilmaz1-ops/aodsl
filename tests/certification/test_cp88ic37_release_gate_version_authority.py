"""VERSION-011 release gate version authority contracts."""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
GATE = ROOT / "tools" / "release_gate.py"


def gate_source():
    return GATE.read_text(encoding="utf-8")


def gate_ast():
    return ast.parse(gate_source())


def test_release_gate_imports_version_policy():
    tree = gate_ast()

    imports = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "aodsl.certification.version_policy"
    ]

    assert imports, "release gate must import version policy"


def test_release_gate_calls_validate_release_version():
    tree = gate_ast()

    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "validate_release_version"
    ]

    assert calls, "release gate must validate all version sources"


def test_release_gate_has_no_fixed_version_assertion():
    source = gate_source()

    assert 'pv==iv=="1.0.0"' not in source
    assert "pv == iv == '1.0.0'" not in source


def test_release_gate_checks_investment_domain_version():
    source = gate_source()

    assert "investment_domain" in source
    assert "investment_version" in source


def test_release_gate_requires_production_tag_metadata():
    source = gate_source()

    assert "GITHUB_REF_TYPE" in source
    assert "GITHUB_REF_NAME" in source
    assert "tag" in source


def test_release_gate_keeps_production_certification_gate():
    source = gate_source()

    assert "verify_production_attestation.py" in source
    assert "CERT-PG-001" in source
    assert "production_deployment" in source
