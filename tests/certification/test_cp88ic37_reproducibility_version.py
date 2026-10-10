
import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "tools/verify_reproducible_artifacts.py"

spec = importlib.util.spec_from_file_location(
    "cp88ic37_reproducibility",
    MODULE,
)
assert spec is not None
assert spec.loader is not None

module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_version_005_explicit_version_binding(tmp_path):
    import json
    import hashlib

    certification_path = tmp_path / "production-certification-manifest.json"
    certification_payload = {
        "source": {
            "canonical_tree_sha256": "b" * 64,
        },
    }
    certification_bytes = (
        json.dumps(certification_payload, sort_keys=True) + "\n"
    ).encode("utf-8")
    certification_path.write_bytes(certification_bytes)

    certification_sha = hashlib.sha256(certification_bytes).hexdigest()
    source_sha = certification_payload["source"]["canonical_tree_sha256"]

    build_a = tmp_path / "build_a"
    build_b = tmp_path / "build_b"

    for build in (build_a, build_b):
        build.mkdir()
        (build / "aodsl-1.0.1-production-source.zip").write_bytes(
            b"artifact"
        )
        (build / "aodsl-1.0.1.cdx.json").write_bytes(
            b"sbom"
        )

        import json
        import hashlib

        manifest = {
            "version": "1.0.1",
            "mode": "production",
            "artifact": "aodsl-1.0.1-production-source.zip",
            "sha256": hashlib.sha256(b"artifact").hexdigest(),
            "files": 1,
            "production_certification_manifest_sha256": certification_sha,
            "certified_source_tree_sha256": source_sha,
        }

        (build / "release-manifest.json").write_text(
            json.dumps(manifest, sort_keys=True, indent=2) + "\n"
        )

    evidence = module.verify_reproducible_artifacts(
        build_a,
        build_b,
        version="1.0.1",
        certification_manifest_path=certification_path,
    )

    assert evidence["artifact"]["path"] == (
        "dist/aodsl-1.0.1-production-source.zip"
    )
    assert evidence["sbom"]["path"] == (
        "dist/aodsl-1.0.1.cdx.json"
    )


def test_version_005_invalid_version_fails_closed(tmp_path):
    with pytest.raises(ValueError):
        module.verify_reproducible_artifacts(
            tmp_path,
            tmp_path,
            version="../1.0.1",
        )
