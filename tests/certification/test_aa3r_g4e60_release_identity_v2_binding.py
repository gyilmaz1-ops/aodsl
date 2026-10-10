
import hashlib
import importlib.util
import json
import shutil
from pathlib import Path

import pytest

from aodsl.certification.release_identity import build_release_identity
from aodsl.certification.certified_bundle import (
    BUNDLE_MANIFEST_PATH,
    build_certified_bundle_manifest,
)

ROOT = Path(__file__).resolve().parents[2]


def legacy_module():
    path = ROOT / "tests/certification/test_inv052_release_identity_binding.py"
    spec = importlib.util.spec_from_file_location("aa3r_inv052", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_identity(tmp_path, mutation=None):
    legacy = legacy_module()
    artifact, cert, sbom, repro, stage, bundle = legacy.fixture(tmp_path)

    release_manifest = tmp_path / "dist/release-manifest.json"
    evidence = json.loads(repro.read_text())
    evidence["schema"] = "aodsl.reproducible-release-artifact.v2"
    evidence["release_manifest"] = {
        "path": "dist/release-manifest.json",
        "sha256": sha(release_manifest),
    }

    if mutation:
        mutation(evidence)

    repro.write_text(
        json.dumps(evidence, sort_keys=True, indent=2) + "\n"
    )

    staged_repro = stage / "dist/reproducibility-manifest.json"
    shutil.copyfile(repro, staged_repro)

    staged_bundle = stage / BUNDLE_MANIFEST_PATH
    staged_bundle.write_text(
        json.dumps(
            build_certified_bundle_manifest(stage),
            sort_keys=True,
            indent=2,
        ) + "\n"
    )
    shutil.copyfile(staged_bundle, bundle)

    return build_release_identity(
        tmp_path,
        artifact,
        cert,
        sbom,
        repro,
        stage,
        bundle,
        git_commit_sha=legacy.COMMIT,
        git_tag=legacy.TAG,
        repository=legacy.REPO,
        workflow_ref=legacy.WORKFLOW_REF,
    )


def test_valid_v2_evidence(tmp_path):
    identity = run_identity(tmp_path)
    assert identity["reproducibility"]["schema"] == (
        "aodsl.reproducible-release-artifact.v2"
    )
    assert identity["reproducibility"]["release_manifest_sha256"] == (
        sha(tmp_path / "dist/release-manifest.json")
    )


def test_missing_manifest_binding_rejected(tmp_path):
    def mutate(evidence):
        del evidence["release_manifest"]

    with pytest.raises(ValueError):
        run_identity(tmp_path, mutate)


def test_wrong_manifest_hash_rejected(tmp_path):
    def mutate(evidence):
        evidence["release_manifest"]["sha256"] = "0" * 64

    with pytest.raises(ValueError):
        run_identity(tmp_path, mutate)


def test_wrong_manifest_path_rejected(tmp_path):
    def mutate(evidence):
        evidence["release_manifest"]["path"] = "dist/wrong.json"

    with pytest.raises(ValueError):
        run_identity(tmp_path, mutate)
