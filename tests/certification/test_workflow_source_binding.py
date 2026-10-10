from pathlib import Path

import pytest

from aodsl.certification.attestation import source_tree_entries
from aodsl.certification.source_binding import canonical_source_tree_sha256


def workflow_fixture(root):
    path = root / ".github/workflows/production-release.yml"
    path.parent.mkdir(parents=True)
    path.write_text("name: Original\n", encoding="utf-8")
    return path


def test_workflow_is_in_certified_source_entries(tmp_path):
    workflow_fixture(tmp_path)
    paths = {entry["path"] for entry in source_tree_entries(tmp_path)}
    assert ".github/workflows/production-release.yml" in paths


@pytest.mark.parametrize("change", ["edit", "add", "delete"])
def test_workflow_change_invalidates_source_hash(tmp_path, change):
    workflow = workflow_fixture(tmp_path)
    before = canonical_source_tree_sha256(tmp_path)
    if change == "edit":
        workflow.write_text("name: Changed\n", encoding="utf-8")
    elif change == "add":
        (workflow.parent / "another.yml").write_text(
            "name: Added\n", encoding="utf-8"
        )
    else:
        workflow.unlink()
    assert canonical_source_tree_sha256(tmp_path) != before
