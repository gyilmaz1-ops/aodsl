import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "tools" / "verify_reproducible_artifacts.py"

spec = importlib.util.spec_from_file_location(
    "verify_reproducible_artifacts",
    MODULE_PATH,
)
assert spec is not None
assert spec.loader is not None

module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

ReproducibilityError = module.ReproducibilityError
sha256_file = module.sha256_file
verify_pair = module.verify_pair


def test_identical_artifacts_pass(tmp_path: Path):
    left = tmp_path / "left.bin"
    right = tmp_path / "right.bin"

    payload = b"deterministic-release-artifact\n"
    left.write_bytes(payload)
    right.write_bytes(payload)

    digest = verify_pair(left, right, "artifact")

    assert digest == sha256_file(left)
    assert digest == sha256_file(right)


def test_different_artifacts_fail_closed(tmp_path: Path):
    left = tmp_path / "left.bin"
    right = tmp_path / "right.bin"

    left.write_bytes(b"artifact-a")
    right.write_bytes(b"artifact-b")

    with pytest.raises(
        ReproducibilityError,
        match="byte-for-byte mismatch",
    ):
        verify_pair(left, right, "artifact")


@pytest.mark.parametrize("missing_side", ["left", "right"])
def test_missing_artifact_fails_closed(
    tmp_path: Path,
    missing_side: str,
):
    left = tmp_path / "left.bin"
    right = tmp_path / "right.bin"

    if missing_side != "left":
        left.write_bytes(b"artifact")
    if missing_side != "right":
        right.write_bytes(b"artifact")

    with pytest.raises(
        ReproducibilityError,
        match="missing artifact",
    ):
        verify_pair(left, right, "artifact")


def test_evidence_binds_identical_artifact_and_sbom(tmp_path: Path):
    left = tmp_path / "a"
    right = tmp_path / "b"
    left.mkdir()
    right.mkdir()

    artifact = b"production-artifact"
    sbom = b'{"bomFormat":"CycloneDX"}\n'

    for root in (left, right):
        (root / module.ARTIFACT_NAME).write_bytes(artifact)
        (root / module.SBOM_NAME).write_bytes(sbom)

    evidence = module.verify_reproducible_artifacts(left, right)

    assert evidence["schema"] == "aodsl.reproducible-release-artifact.v1"
    assert evidence["invariant"] == "INV-055"
    assert evidence["comparison"] == {
        "algorithm": "sha256-and-byte-equality",
        "independent_builds": 2,
        "result": "IDENTICAL",
    }
    assert evidence["artifact"]["sha256"] == sha256_file(
        left / module.ARTIFACT_NAME
    )
    assert evidence["sbom"]["sha256"] == sha256_file(
        left / module.SBOM_NAME
    )


def test_evidence_is_deterministic(tmp_path: Path):
    left = tmp_path / "a"
    right = tmp_path / "b"
    left.mkdir()
    right.mkdir()

    for root in (left, right):
        (root / module.ARTIFACT_NAME).write_bytes(b"same-artifact")
        (root / module.SBOM_NAME).write_bytes(b"same-sbom")

    first = module.verify_reproducible_artifacts(left, right)
    second = module.verify_reproducible_artifacts(left, right)

    assert first == second


def test_evidence_contains_no_execution_specific_identity(tmp_path: Path):
    import json

    left = tmp_path / "a"
    right = tmp_path / "b"
    left.mkdir()
    right.mkdir()

    for root in (left, right):
        (root / module.ARTIFACT_NAME).write_bytes(b"same-artifact")
        (root / module.SBOM_NAME).write_bytes(b"same-sbom")

    evidence = module.verify_reproducible_artifacts(left, right)
    serialized = json.dumps(evidence, sort_keys=True)

    for forbidden in (
        "run_id",
        "job_id",
        "timestamp",
        "workspace",
        "runner",
    ):
        assert forbidden not in serialized
