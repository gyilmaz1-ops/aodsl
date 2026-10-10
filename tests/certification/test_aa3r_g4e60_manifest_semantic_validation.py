import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "tools/verify_reproducible_artifacts.py"

spec = importlib.util.spec_from_file_location(
    "aa3r_g4e60_reproducibility",
    MODULE_PATH,
)
assert spec is not None and spec.loader is not None

module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

ReproducibilityError = module.ReproducibilityError


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def valid_manifest(artifact: bytes) -> dict:
    return {
        "version": module.PROJECT_VERSION,
        "mode": "production",
        "artifact": module.ARTIFACT_NAME,
        "sha256": sha(artifact),
        "files": 1,
        "production_certification_manifest_sha256": "a" * 64,
        "certified_source_tree_sha256": "b" * 64,
    }


def make_builds(tmp_path: Path, manifest: dict):
    left = tmp_path / "a"
    right = tmp_path / "b"

    artifact = b"production-artifact"
    sbom = b"same-sbom"

    for root in (left, right):
        root.mkdir()
        (root / module.ARTIFACT_NAME).write_bytes(artifact)
        (root / module.SBOM_NAME).write_bytes(sbom)
        (root / "release-manifest.json").write_text(
            json.dumps(manifest, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    return left, right


@pytest.mark.parametrize(
    "field,value",
    [
        ("mode", "source"),
        ("version", "9.9.9"),
        ("artifact", "wrong-artifact.zip"),
        ("sha256", "0" * 64),
        ("files", -1),
        ("files", True),
        ("production_certification_manifest_sha256", None),
        ("certified_source_tree_sha256", None),
    ],
)
def test_invalid_manifest_semantics_fail_closed(
    tmp_path: Path,
    field: str,
    value,
):
    artifact = b"production-artifact"
    manifest = valid_manifest(artifact)
    manifest[field] = value

    left, right = make_builds(tmp_path, manifest)

    with pytest.raises(ReproducibilityError):
        module.verify_reproducible_artifacts(left, right)


@pytest.mark.parametrize(
    "field",
    [
        "production_certification_manifest_sha256",
        "certified_source_tree_sha256",
    ],
)
def test_missing_production_binding_fails_closed(
    tmp_path: Path,
    field: str,
):
    manifest = valid_manifest(b"production-artifact")
    del manifest[field]

    left, right = make_builds(tmp_path, manifest)

    with pytest.raises(ReproducibilityError):
        module.verify_reproducible_artifacts(left, right)
