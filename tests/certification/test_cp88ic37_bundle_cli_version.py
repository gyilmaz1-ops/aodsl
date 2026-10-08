
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from aodsl.certification.certified_bundle import (
    BUNDLE_MANIFEST_PATH,
    required_payload_paths,
)


ROOT = Path(__file__).resolve().parents[2]
CREATE = "create_certified_bundle_manifest.py"
VERIFY = "verify_certified_bundle_manifest.py"


def setup_repository(tmp_path, version="1.0.1"):
    repo = tmp_path / "repo"
    tools = repo / "tools"
    tools.mkdir(parents=True)

    for name in (CREATE, VERIFY):
        shutil.copyfile(ROOT / "tools" / name, tools / name)

    if version is not None:
        (repo / "pyproject.toml").write_text(
            '[project]\n'
            'name = "aodsl"\n'
            f'version = "{version}"\n',
            encoding="utf-8",
        )

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    return repo, bundle


def populate(bundle, version):
    for index, relative in enumerate(required_payload_paths(version)):
        path = bundle / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"payload-{index}".encode())


def run_cli(repo, bundle, script):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")

    return subprocess.run(
        [
            sys.executable,
            str(repo / "tools" / script),
            "--bundle-root",
            str(bundle),
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )


def test_version_010_create_and_verify_101(tmp_path):
    repo, bundle = setup_repository(tmp_path, "1.0.1")
    populate(bundle, "1.0.1")

    created = run_cli(repo, bundle, CREATE)
    assert created.returncode == 0, created.stdout + created.stderr

    manifest = json.loads(
        (bundle / BUNDLE_MANIFEST_PATH).read_text(encoding="utf-8")
    )

    assert [item["path"] for item in manifest["payload"]] == list(
        required_payload_paths("1.0.1")
    )

    verified = run_cli(repo, bundle, VERIFY)
    assert verified.returncode == 0, verified.stdout + verified.stderr


@pytest.mark.parametrize("script", [CREATE, VERIFY])
def test_version_010_missing_metadata_fails_closed(tmp_path, script):
    repo, bundle = setup_repository(tmp_path, None)
    populate(bundle, "1.0.0")

    result = run_cli(repo, bundle, script)
    assert result.returncode != 0


@pytest.mark.parametrize("script", [CREATE, VERIFY])
def test_version_010_invalid_metadata_fails_closed(tmp_path, script):
    repo, bundle = setup_repository(tmp_path, "1.0.1-dev")
    populate(bundle, "1.0.0")

    result = run_cli(repo, bundle, script)
    assert result.returncode != 0


def test_version_010_wrong_version_bundle_rejected(tmp_path):
    repo, bundle = setup_repository(tmp_path, "1.0.1")
    populate(bundle, "1.0.0")

    result = run_cli(repo, bundle, CREATE)
    assert result.returncode != 0
    assert not (bundle / BUNDLE_MANIFEST_PATH).exists()


def test_version_010_legacy_100_supported(tmp_path):
    repo, bundle = setup_repository(tmp_path, "1.0.0")
    populate(bundle, "1.0.0")

    created = run_cli(repo, bundle, CREATE)
    assert created.returncode == 0, created.stdout + created.stderr

    verified = run_cli(repo, bundle, VERIFY)
    assert verified.returncode == 0, verified.stdout + verified.stderr
