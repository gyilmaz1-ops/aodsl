import json
from pathlib import Path

import pytest

from aodsl.certification import evidence_promotion as ep


def raw(sha="a" * 64, production="CERTIFIED"):
    return {
        "architecture": {"id": "ARCH-001", "status": "PASSED", "artifact_count": 29},
        "operations": {"id": "OPS-001", "status": "CERTIFIED"},
        "postgres": {"id": "CERT-PG-001", "status": "CERTIFIED", "covered": ["lease fencing"]},
        "production_status": production,
        "tested_source_tree_sha256": sha,
    }


def test_normalizes_raw_to_canonical_schema():
    c = ep.normalize_live_evidence(raw())
    assert c["schema"] == "aodsl.live-certification.v1"
    assert c["production_deployment"] == "CERTIFIED"
    assert c["cert_pg_001"] == "CERTIFIED"


def test_rejects_non_certified_raw():
    with pytest.raises(ValueError, match="not promotable"):
        ep.normalize_live_evidence(raw(production="NOT_CERTIFIED"))


def test_source_mismatch_does_not_replace_existing_evidence(tmp_path, monkeypatch):
    rp = tmp_path / "raw.json"; cp = tmp_path / "canonical.json"; ap = tmp_path / "archive.json"
    rp.write_text(json.dumps(raw("a" * 64)))
    cp.write_text('{"sentinel":"old"}\n')
    monkeypatch.setattr(ep, "canonical_source_tree_sha256", lambda root: "b" * 64)
    with pytest.raises(ValueError, match="source binding mismatch"):
        ep.promote_live_evidence(tmp_path, rp, cp, ap)
    assert json.loads(cp.read_text()) == {"sentinel": "old"}
    assert not ap.exists()


def test_success_replaces_canonical_and_archives_raw(tmp_path, monkeypatch):
    rp = tmp_path / "raw.json"; cp = tmp_path / "canonical.json"; ap = tmp_path / "archive.json"
    r = raw("c" * 64); rp.write_text(json.dumps(r)); cp.write_text('{"sentinel":"old"}\n')
    monkeypatch.setattr(ep, "canonical_source_tree_sha256", lambda root: "c" * 64)
    c = ep.promote_live_evidence(tmp_path, rp, cp, ap)
    assert json.loads(cp.read_text()) == c
    assert json.loads(ap.read_text()) == r
