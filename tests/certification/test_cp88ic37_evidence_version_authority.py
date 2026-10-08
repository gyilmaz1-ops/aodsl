import json
from pathlib import Path

import pytest

from aodsl.certification.attestation import (
    build_manifest,
    sha256_bytes,
    canonical_json_bytes,
    source_tree_entries,
)
from aodsl.certification import evidence_promotion as ep


def write_project(root: Path, version: str):
    content = chr(10).join((
        "[project]",
        'name = "aodsl"',
        f'version = "{version}"',
        "",
    ))
    (root / "pyproject.toml").write_text(
        content,
        encoding="utf-8",
    )


def test_attestation_rejects_evidence_version_mismatch(tmp_path):
    root = tmp_path
    write_project(root, "1.0.1")

    registry = root / "architecture"
    registry.mkdir()
    registry.joinpath("invariants.v1.json").write_text(
        json.dumps({
            "invariants": [
                {"id": f"INV-{i:03d}"}
                for i in range(1, 57)
            ]
        }),
        encoding="utf-8",
    )

    evidence = root / "live.json"
    evidence.write_text(
        json.dumps({
            "schema": "aodsl.live-certification.v1",
            "version": "1.0.0",
            "architecture": "PASSED",
            "operations": "CERTIFIED",
            "cert_pg_001": "CERTIFIED",
            "production_deployment": "CERTIFIED",
            "postgres": {
                "id": "CERT-PG-001",
                "status": "CERTIFIED",
            },
            "tested_source_tree_sha256": sha256_bytes(
                canonical_json_bytes(source_tree_entries(root))
            ),
        }),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="version"):
        build_manifest(root, evidence)


def test_promotion_uses_authoritative_project_version(
    tmp_path, monkeypatch
):
    write_project(tmp_path, "1.0.1")

    raw_path = tmp_path / "raw.json"
    canonical_path = tmp_path / "canonical.json"
    archive_path = tmp_path / "archive.json"

    raw_path.write_text(
        json.dumps({
            "architecture": {"status": "PASSED"},
            "operations": {"status": "CERTIFIED"},
            "postgres": {
                "id": "CERT-PG-001",
                "status": "CERTIFIED",
            },
            "production_status": "CERTIFIED",
            "tested_source_tree_sha256": "a" * 64,
        }),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        ep,
        "canonical_source_tree_sha256",
        lambda root: "a" * 64,
    )

    result = ep.promote_live_evidence(
        tmp_path,
        raw_path,
        canonical_path,
        archive_path,
    )

    assert result["version"] == "1.0.1"
    assert json.loads(canonical_path.read_text())["version"] == "1.0.1"
