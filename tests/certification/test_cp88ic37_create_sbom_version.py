
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools/create_sbom.py"


def run_cli(tmp_path, pyproject_text):
    tools = tmp_path / "tools"
    tools.mkdir()

    script = tools / "create_sbom.py"
    script.write_bytes(SCRIPT.read_bytes())

    requirements = tmp_path / "requirements"
    requirements.mkdir()

    lock = "example==1.0.0 --hash=sha256:" + "a" * 64
    (requirements / "production.lock").write_text(
        lock + chr(10),
        encoding="utf-8",
    )

    if pyproject_text is not None:
        (tmp_path / "pyproject.toml").write_text(
            pyproject_text,
            encoding="utf-8",
        )

    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")

    return subprocess.run(
        [sys.executable, str(script)],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )


def test_version_008_cli_uses_project_version(tmp_path):
    result = run_cli(
        tmp_path,
        chr(10).join([
            "[project]",
            'name = "aodsl"',
            'version = "1.0.1"',
            "",
        ]),
    )

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "dist/aodsl-1.0.1.cdx.json").is_file()
    assert not (tmp_path / "dist/aodsl-1.0.0.cdx.json").exists()


def test_version_008_cli_missing_metadata_fails_closed(tmp_path):
    result = run_cli(tmp_path, None)

    assert result.returncode != 0
    assert not list((tmp_path / "dist").glob("*.cdx.json")) if (
        tmp_path / "dist"
    ).exists() else True


def test_version_008_cli_invalid_metadata_fails_closed(tmp_path):
    result = run_cli(
        tmp_path,
        chr(10).join([
            "[project]",
            'name = "aodsl"',
            'version = "bad"',
            "",
        ]),
    )

    assert result.returncode != 0
    dist = tmp_path / "dist"
    assert not dist.exists() or not list(dist.glob("*.cdx.json"))
