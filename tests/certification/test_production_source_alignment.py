import ast
from pathlib import Path
import subprocess

import pytest

from aodsl.certification.release_source import (
    CERTIFICATION_FILES,
    verify_production_source_alignment,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def repository(tmp_path, monkeypatch):
    for key in list(__import__("os").environ):
        if key.startswith("GIT_"):
            monkeypatch.delenv(key)
    root = tmp_path / "repo"
    root.mkdir()
    (root / "src").mkdir()
    (root / "src/probe.py").write_text("VALUE = 1\n")
    (root / "pyproject.toml").write_text(
        '[project]\nname = "aodsl"\nversion = "1.0.11"\n'
    )
    for name in CERTIFICATION_FILES:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n")

    def git(*args):
        subprocess.run(
            ["git", *args], cwd=root, check=True,
            capture_output=True,
        )

    git("init", "-q")
    git("add", ".")
    git(
        "-c", "user.name=Fixture",
        "-c", "user.email=fixture@example.invalid",
        "-c", "commit.gpgsign=false",
        "-c", "core.hooksPath=/dev/null",
        "commit", "-qm", "Synthetic alignment fixture",
    )
    return root


def test_matching_inputs_are_accepted(repository):
    verify_production_source_alignment(repository)


@pytest.mark.parametrize("change", ["edit", "add", "delete"])
def test_source_changes_are_rejected(repository, change):
    source = repository / "src/probe.py"
    if change == "edit":
        source.write_text("VALUE = 2\n")
    elif change == "add":
        (repository / "src/new.py").write_text("VALUE = 3\n")
    else:
        source.unlink()
    with pytest.raises(ValueError, match="PRODUCTION-SOURCE-001"):
        verify_production_source_alignment(repository)


@pytest.mark.parametrize("name", CERTIFICATION_FILES)
def test_changed_certification_is_rejected(repository, name):
    (repository / name).write_text('{"changed": true}\n')
    with pytest.raises(ValueError, match="PRODUCTION-SOURCE-002"):
        verify_production_source_alignment(repository)


def test_generated_dist_does_not_block_release(repository):
    dist = repository / "dist"
    dist.mkdir()
    (dist / "generated.txt").write_text("generated\n")
    verify_production_source_alignment(repository)


def test_production_guard_precedes_release_stages():
    tree = ast.parse((ROOT / "tools/release_gate.py").read_text())
    guarded = [
        node for node in tree.body
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Name)
        and node.test.id == "production"
        and any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id == "verify_production_source_alignment"
            for child in ast.walk(node)
        )
    ]
    assert len(guarded) == 1
    stages = [
        node.lineno for node in tree.body
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "run"
    ]
    assert stages and guarded[0].lineno < min(stages)
