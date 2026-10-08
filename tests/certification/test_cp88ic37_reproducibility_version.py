
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

    evidence = module.verify_reproducible_artifacts(
        build_a,
        build_b,
        version="1.0.1",
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
