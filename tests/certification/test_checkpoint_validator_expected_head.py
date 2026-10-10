import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "repo"
    (root / "tools").mkdir(parents=True)
    shutil.copy2(
        ROOT / "tools/aodsl_validate.py",
        root / "tools/aodsl_validate.py",
    )
    folder = root / "tests/certification"
    folder.mkdir(parents=True)
    for name in (
        "test_aa3r_g4e60_release_identity_v2_binding.py",
        "test_inv052_release_identity_binding.py",
        "test_cp88ic37_release_identity_versioned_e2e.py",
    ):
        (folder / name).write_text("def test_fixture(): pass\n")

    def git(*args):
        return subprocess.check_output(
            ["git", *args], cwd=root, text=True,
        ).strip()

    git("init", "-q")
    git("add", ".")
    git(
        "-c", "user.name=Checkpoint Test",
        "-c", "user.email=test@example.invalid",
        "-c", "commit.gpgsign=false",
        "-c", "core.hooksPath=/dev/null",
        "commit", "-qm", "Fixture",
    )
    return root, git("rev-parse", "HEAD")


def run(repository, *args):
    root, _ = repository
    return subprocess.run(
        [
            sys.executable, "tools/aodsl_validate.py",
            "--checkpoint", "P6J-3G-4", "--report", *args,
        ],
        cwd=root, capture_output=True, text=True,
    )


def test_explicit_matching_commit_passes_without_certification(repository):
    root, head = repository
    result = run(repository, "--expected-head", head)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["result"] == "PASS"
    assert report["expected_head"] == head
    assert report["tests_executed"] is False
    assert report["release_certified"] is False
    assert subprocess.check_output(
        ["git", "status", "--porcelain=v1"], cwd=root,
    ) == b""


def test_wrong_commit_fails_closed(repository):
    result = run(repository, "--expected-head", "0" * 40)
    assert result.returncode == 2
    assert "HEAD_MISMATCH" in json.loads(result.stdout)["errors"]


def test_default_preserves_historical_checkpoint(repository):
    result = run(repository)
    assert result.returncode == 2
    report = json.loads(result.stdout)
    assert report["expected_head"] == (
        "c318c77a7859d5759bc42637f9281b515b61d825"
    )
    assert "HEAD_MISMATCH" in report["errors"]


def test_moving_reference_is_rejected(repository):
    result = run(repository, "--expected-head", "HEAD")
    assert result.returncode == 2
    assert "40-character commit SHA" in result.stderr
