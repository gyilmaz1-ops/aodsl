import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from aodsl.certification.evidence_promotion import normalize_live_evidence

TOOL = Path(__file__).resolve().parents[2] / "tools/verify_live_evidence_pair.py"
spec = importlib.util.spec_from_file_location("evidence_pair_tool", TOOL)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def fixture_pair(root):
    (root / "pyproject.toml").write_text(
        '[project]\nname = "aodsl"\nversion = "1.0.11"\n',
        encoding="utf-8",
    )
    folder = root / "certification/evidence"
    folder.mkdir(parents=True)
    raw = {
        "architecture": {"status": "PASSED"},
        "operations": {"status": "CERTIFIED"},
        "postgres": {
            "id": "CERT-PG-001", "status": "CERTIFIED",
            "covered": ["lease fencing"],
        },
        "production_status": "CERTIFIED",
        "tested_source_tree_sha256": "a" * 64,
    }
    paths = [
        folder / "live-certification-status.raw.json",
        folder / "live-certification-status.json",
    ]
    for path, value in zip(
        paths, [raw, normalize_live_evidence(raw, "1.0.11")]
    ):
        path.write_text(json.dumps(value), encoding="utf-8")
    return paths


def test_matching_pair_cli_is_read_only_and_does_not_certify(tmp_path):
    paths = fixture_pair(tmp_path)
    before = {path: path.read_bytes() for path in paths}
    result = subprocess.run(
        [sys.executable, str(TOOL), "--root", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["evidence_pair"] == "PASS"
    assert report["release_certified"] is False
    assert {path: path.read_bytes() for path in paths} == before


@pytest.mark.parametrize("change", ["hash", "coverage", "version", "extra"])
def test_semantic_mismatch_rejected_without_writes(tmp_path, change):
    paths = fixture_pair(tmp_path)
    canonical = json.loads(paths[1].read_text())
    if change == "hash":
        canonical["tested_source_tree_sha256"] = "b" * 64
    elif change == "coverage":
        canonical["postgres"]["covered"] = ["different coverage"]
    elif change == "version":
        canonical["version"] = "1.0.12"
    else:
        canonical["unexpected"] = True
    paths[1].write_text(json.dumps(canonical))
    before = {path: path.read_bytes() for path in paths}
    assert module.verify_pair(tmp_path)
    assert {path: path.read_bytes() for path in paths} == before


@pytest.mark.parametrize(
    "content",
    ['[]', '{', '{"x":1,"x":2}'],
)
def test_malformed_or_duplicate_json_rejected(tmp_path, content):
    paths = fixture_pair(tmp_path)
    paths[0].write_text(content)
    assert module.verify_pair(tmp_path)


def test_matching_nonhex_hashes_rejected(tmp_path):
    paths = fixture_pair(tmp_path)
    for path in paths:
        data = json.loads(path.read_text())
        data["tested_source_tree_sha256"] = "z" * 64
        path.write_text(json.dumps(data))
    assert module.verify_pair(tmp_path)


def test_missing_archive_cli_fails_closed(tmp_path):
    paths = fixture_pair(tmp_path)
    paths[0].unlink()
    result = subprocess.run(
        [sys.executable, str(TOOL), "--root", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 2
    report = json.loads(result.stdout)
    assert report["evidence_pair"] == "FAIL"
    assert report["release_certified"] is False
