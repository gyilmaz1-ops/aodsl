from pathlib import Path
import hashlib
import importlib.util
import json

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "tools/verify_reproducible_artifacts.py"

spec = importlib.util.spec_from_file_location(
    "aa3r_g4e59_release_manifest", MODULE_PATH
)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

VERSION = "1.0.0"
ARTIFACT_NAME, SBOM_NAME = module.artifact_names(VERSION)
ARTIFACT = b"identical-production-artifact"
SBOM = b"identical-production-sbom"

def sha(data):
    return hashlib.sha256(data).hexdigest()

def certification_fixture(tmp_path):
    path = tmp_path / "production-certification-manifest.json"
    payload = {
        "source": {
            "canonical_tree_sha256": "b" * 64,
        },
    }
    path.write_text(
        json.dumps(payload, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def manifest_bytes(artifact_sha=None, certification_path=None):
    assert certification_path is not None
    certification_sha = sha(certification_path.read_bytes())
    source_sha = json.loads(
        certification_path.read_text(encoding="utf-8")
    )["source"]["canonical_tree_sha256"]
    manifest = {
        "version": VERSION,
        "mode": "production",
        "artifact": ARTIFACT_NAME,
        "sha256": sha(ARTIFACT) if artifact_sha is None else artifact_sha,
        "files": 1,
        "production_certification_manifest_sha256": certification_sha,
        "certified_source_tree_sha256": source_sha,
    }
    return (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()

@pytest.fixture
def builds(tmp_path):
    certification_path = certification_fixture(tmp_path)
    a, b = tmp_path / "a", tmp_path / "b"
    for directory in (a, b):
        directory.mkdir()
        (directory / ARTIFACT_NAME).write_bytes(ARTIFACT)
        (directory / SBOM_NAME).write_bytes(SBOM)
        (directory / "release-manifest.json").write_bytes(manifest_bytes(certification_path=certification_path))
    return a, b, certification_path

def verify(builds):
    a, b, certification_path = builds
    return module.verify_reproducible_artifacts(
        a, b,
        version=VERSION,
        certification_manifest_path=certification_path,
    )

def test_identical_valid_manifest_is_bound_to_evidence(builds):
    evidence = verify(builds)
    assert evidence["comparison"]["result"] == "IDENTICAL"
    assert evidence["release_manifest"] == {
        "path": "dist/release-manifest.json",
        "sha256": sha(manifest_bytes(certification_path=builds[2])),
    }

def test_manifest_mismatch_fails_closed(builds):
    _, b, _ = builds
    (b / "release-manifest.json").write_bytes(
        manifest_bytes(certification_path=builds[2]).replace(b'"files": 1', b'"files": 2')
    )
    with pytest.raises(module.ReproducibilityError):
        verify(builds)

@pytest.mark.parametrize("missing_side", ["a", "b"])
def test_missing_manifest_fails_closed(builds, missing_side):
    a, b, _ = builds
    directory = a if missing_side == "a" else b
    (directory / "release-manifest.json").unlink()
    with pytest.raises(module.ReproducibilityError):
        verify(builds)

def test_invalid_manifest_json_fails_closed(builds):
    for directory in builds[:2]:
        (directory / "release-manifest.json").write_bytes(b'{"version":')
    with pytest.raises(module.ReproducibilityError):
        verify(builds)

def test_wrong_artifact_sha_fails_closed(builds):
    for directory in builds[:2]:
        (directory / "release-manifest.json").write_bytes(
            manifest_bytes("0" * 64, certification_path=builds[2])
        )
    with pytest.raises(module.ReproducibilityError):
        verify(builds)
