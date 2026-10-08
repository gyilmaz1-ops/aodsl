
from pathlib import Path

import pytest

from aodsl.certification.sbom import build_sbom


def make_root(tmp_path: Path) -> Path:
    requirements = tmp_path / "requirements"
    requirements.mkdir()

    lock = "example==1.0.0 --hash=sha256:" + "a" * 64
    (requirements / "production.lock").write_text(
        lock + chr(10),
        encoding="utf-8",
    )
    return tmp_path


def test_version_006_explicit_sbom_version(tmp_path):
    root = make_root(tmp_path)

    sbom = build_sbom(root, version="1.0.1")

    assert sbom["metadata"]["component"]["version"] == "1.0.1"


def test_version_006_invalid_sbom_version_fails_closed(tmp_path):
    root = make_root(tmp_path)

    with pytest.raises(ValueError):
        build_sbom(root, version="../1.0.1")


def test_version_007_write_sbom_explicit_version(tmp_path):
    from aodsl.certification.sbom import write_sbom

    root = make_root(tmp_path)
    output = root / "dist" / "test.cdx.json"

    written = write_sbom(root, output, version="1.0.1")

    assert written["metadata"]["component"]["version"] == "1.0.1"
    assert output.is_file()


def test_version_007_verify_sbom_explicit_version(tmp_path):
    from aodsl.certification.sbom import write_sbom, verify_sbom

    root = make_root(tmp_path)
    output = root / "dist" / "test.cdx.json"

    write_sbom(root, output, version="1.0.1")

    assert verify_sbom(root, output, version="1.0.1") == []


def test_version_007_invalid_verification_version_fails_closed(tmp_path):
    from aodsl.certification.sbom import write_sbom, verify_sbom

    root = make_root(tmp_path)
    output = root / "dist" / "test.cdx.json"

    write_sbom(root, output)

    errors = verify_sbom(root, output, version="../1.0.1")

    assert errors
