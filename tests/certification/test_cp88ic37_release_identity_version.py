
import ast
import inspect
from pathlib import Path

import pytest

from aodsl.certification import release_identity
from aodsl.certification.version_policy import (
    artifact_names,
    validate_release_version,
)


ROOT = Path(__file__).resolve().parents[2]


def function_node(path, name):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == name
    )


def calls_named(node, name):
    return [
        call for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and (
            isinstance(call.func, ast.Name)
            and call.func.id == name
            or isinstance(call.func, ast.Attribute)
            and call.func.attr == name
        )
    ]


def test_version_011_build_api_accepts_explicit_version():
    signature = inspect.signature(
        release_identity.build_release_identity
    )
    assert "version" in signature.parameters
    assert signature.parameters["version"].kind == (
        inspect.Parameter.KEYWORD_ONLY
    )


def test_version_011_verify_api_accepts_explicit_version():
    signature = inspect.signature(
        release_identity.verify_release_identity
    )
    assert "version" in signature.parameters
    assert signature.parameters["version"].kind == (
        inspect.Parameter.KEYWORD_ONLY
    )


def test_version_011_build_passes_version_to_bundle_verifier():
    path = ROOT / "src/aodsl/certification/release_identity.py"
    node = function_node(path, "build_release_identity")

    calls = calls_named(
        node, "verify_certified_bundle_manifest"
    )
    assert len(calls) == 1
    assert any(
        keyword.arg == "version"
        and isinstance(keyword.value, ast.Name)
        and keyword.value.id == "version"
        for keyword in calls[0].keywords
    )


@pytest.mark.parametrize(
    "filename",
    [
        "tools/create_release_identity.py",
        "tools/verify_release_identity.py",
    ],
)
def test_version_011_cli_uses_authoritative_metadata(filename):
    path = ROOT / filename
    tree = ast.parse(path.read_text(encoding="utf-8"))

    assert calls_named(tree, "read_project_version")
    assert calls_named(tree, "artifact_names")
    assert calls_named(tree, "validate_artifact_binding")


def test_version_011_tag_and_artifact_policy_contract():
    assert artifact_names("1.0.1") == (
        "aodsl-1.0.1-production-source.zip",
        "aodsl-1.0.1.cdx.json",
    )

    assert validate_release_version(
        package_version="1.0.1",
        runtime_version="1.0.1",
        investment_version="1.0.1",
        git_tag="v1.0.1",
    ) == "1.0.1"

    with pytest.raises(ValueError, match="git tag"):
        validate_release_version(
            package_version="1.0.1",
            runtime_version="1.0.1",
            investment_version="1.0.1",
            git_tag="v1.0.0",
        )
