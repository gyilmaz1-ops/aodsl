from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RELEASE_GATE = ROOT / "tools/release_gate.py"


def test_release_packaging_does_not_use_unbounded_root_scan():
    source = RELEASE_GATE.read_text(encoding="utf-8")

    assert 'ROOT.rglob("*")' not in source, (
        "Release packaging must not include arbitrary "
        "untracked build artifacts"
    )


def test_release_packaging_uses_git_tracked_source_scope():
    source = RELEASE_GATE.read_text(encoding="utf-8")

    assert "ls-files" in source or "ls-tree" in source, (
        "Release packaging must derive file membership "
        "from a Git-controlled source scope"
    )


import ast
import hashlib
import subprocess
import tempfile
import zipfile
from unittest.mock import patch

import pytest


def _packaging_code():
    tree = ast.parse(RELEASE_GATE.read_text(encoding="utf-8"))
    start = end = None

    for index, node in enumerate(tree.body):
        if isinstance(node, ast.Assign):
            names = [
                target.id
                for target in node.targets
                if isinstance(target, ast.Name)
            ]
            if "tree_data" in names:
                start = index
            if "digest" in names and start is not None:
                end = index
                break

    assert start is not None and end is not None and end > start

    module = ast.Module(
        body=tree.body[start:end],
        type_ignores=[],
    )
    ast.fix_missing_locations(module)
    return compile(module, str(RELEASE_GATE), "exec")


def _git(*args):
    return subprocess.check_output(
        ["git", *args],
        cwd=ROOT,
    )


def _build(code, artifact):
    namespace = {
        "ROOT": ROOT,
        "artifact": artifact,
        "subprocess": subprocess,
        "zipfile": zipfile,
    }
    exec(code, namespace)


def test_packaging_behavior_matches_git_head(tmp_path):
    code = _packaging_code()
    raw = _git("ls-tree", "-r", "-z", "HEAD")
    expected = {}

    for record in raw.split(b"\x00"):
        if not record:
            continue
        metadata, sep, raw_path = record.partition(b"\x09")
        assert sep == b"\x09"
        mode, kind, oid = metadata.decode("ascii").split()
        assert kind == "blob"
        assert mode in ("100644", "100755")
        expected[raw_path.decode("utf-8")] = (mode, oid)

    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"

    _build(code, first)
    _build(code, second)

    assert hashlib.sha256(first.read_bytes()).digest() == (
        hashlib.sha256(second.read_bytes()).digest()
    )

    with zipfile.ZipFile(first) as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]

        assert len(names) == len(set(names))
        assert names == sorted(names)
        assert set(names) == set(expected)

        for info in infos:
            mode, oid = expected[info.filename]
            assert archive.read(info.filename) == _git(
                "cat-file", "blob", oid
            )
            permissions = (info.external_attr >> 16) & 0o777
            assert permissions == (
                0o755 if mode == "100755" else 0o644
            )
            assert info.create_system == 3
            assert info.compress_type == zipfile.ZIP_DEFLATED
            assert info.date_time == (1980, 1, 1, 0, 0, 0)


@pytest.mark.parametrize(
    "payload, expected_error",
    [
        (
            b"100644 blob " + b"0" * 40 + b"\x09/etc/passwd\x00",
            "UNSAFE_GIT_TREE_PATH",
        ),
        (
            b"100644 blob " + b"0" * 40 + b"\x09../escape\x00",
            "UNSAFE_GIT_TREE_PATH",
        ),
        (
            b"100644 blob " + b"0" * 40 + b"\x09src/../escape\x00",
            "UNSAFE_GIT_TREE_PATH",
        ),
        (
            b"100644 blob " + b"0" * 40 + b"\x09src/./file\x00",
            "UNSAFE_GIT_TREE_PATH",
        ),
        (
            b"100644 blob " + b"0" * 40 + b"\x09src//file\x00",
            "UNSAFE_GIT_TREE_PATH",
        ),
        (
            b"100644 blob " + b"0" * 40 + b"\x09src\\evil\x00",
            "UNSAFE_GIT_TREE_PATH",
        ),
        (
            b"120000 blob " + b"0" * 40 + b"\x09link\x00",
            "UNSUPPORTED_GIT_TREE_ENTRY",
        ),
        (
            b"160000 commit " + b"0" * 40 + b"\x09submodule\x00",
            "UNSUPPORTED_GIT_TREE_ENTRY",
        ),
        (
            b"invalid\x09file\x00",
            "INVALID_GIT_TREE_METADATA",
        ),
        (
            b"100644 blob " + b"0" * 40 + b"\x00",
            "INVALID_GIT_TREE_RECORD",
        ),
    ],
)
def test_packaging_rejects_unsafe_git_tree_entries(
    tmp_path, payload, expected_error
):
    code = _packaging_code()

    def fake_check_output(command, **kwargs):
        if command[:2] == ["git", "ls-tree"]:
            return payload
        raise AssertionError(f"UNEXPECTED_SUBPROCESS: {command}")

    with patch.object(
        subprocess,
        "check_output",
        side_effect=fake_check_output,
    ):
        with pytest.raises(RuntimeError) as exc:
            _build(code, tmp_path / "unsafe.zip")

        assert str(exc.value) == expected_error
