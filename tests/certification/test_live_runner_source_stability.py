import importlib.util
import json
from pathlib import Path

import pytest

from aodsl.certification.source_binding import canonical_source_tree_sha256

REPO = Path(__file__).resolve().parents[2]


def fixture_runner(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "source_stability_runner",
        REPO / "tools/run_live_postgres_certification.py",
    )
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    (tmp_path / "pyproject.toml").write_bytes(
        (REPO / "pyproject.toml").read_bytes()
    )
    source = tmp_path / "src/probe.py"
    source.parent.mkdir()
    source.write_text("VALUE = 1\n", encoding="utf-8")
    runner.ROOT = tmp_path
    runner.OUT = tmp_path / "dist/live-certification-status.json"
    runner.CANONICAL = tmp_path / "certification/evidence/live-certification-status.json"
    runner.RAW_ARCHIVE = tmp_path / "certification/evidence/live-certification-status.raw.json"
    return runner, source


def certified_status():
    return {
        "architecture": {"status": "PASSED"},
        "operations": {"status": "CERTIFIED"},
        "postgres": {"id": "CERT-PG-001", "status": "CERTIFIED"},
        "production_status": "CERTIFIED",
    }


@pytest.mark.parametrize("change", ["edit", "add", "delete"])
@pytest.mark.parametrize("existing", [False, True])
def test_changed_source_blocks_all_evidence_writes(
    tmp_path, change, existing
):
    runner, source = fixture_runner(tmp_path)
    outputs = [runner.OUT, runner.CANONICAL, runner.RAW_ARCHIVE]
    if existing:
        for path in outputs:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"existing evidence\n")
    original = {
        path: path.read_bytes() if path.exists() else None
        for path in outputs
    }

    def gate():
        if change == "edit":
            source.write_text("VALUE = 2\n", encoding="utf-8")
        elif change == "add":
            (source.parent / "added.py").write_text(
                "VALUE = 2\n", encoding="utf-8"
            )
        else:
            source.unlink()
        return certified_status()

    runner.certification_status = gate
    assert runner.main() == 2
    for path, content in original.items():
        if content is None:
            assert not path.exists()
        else:
            assert path.read_bytes() == content


def test_stable_source_can_promote(tmp_path):
    runner, _ = fixture_runner(tmp_path)
    expected = canonical_source_tree_sha256(tmp_path)
    runner.certification_status = certified_status
    assert runner.main() == 0
    for path in (runner.OUT, runner.CANONICAL, runner.RAW_ARCHIVE):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["tested_source_tree_sha256"] == expected
