from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/production-release.yml"

CICD = ROOT / "tools/verify_cicd_policy.py"
PROVENANCE = ROOT / "tools/verify_signed_provenance_policy.py"


def mutant_workflow(
    tmp_path: Path,
    needle: str,
    *,
    expected_count: int = 1,
) -> Path:
    text = WORKFLOW.read_text(encoding="utf-8")

    actual_count = text.count(needle)
    assert actual_count == expected_count, (
        f"mutation precondition failed for {needle!r}: "
        f"expected {expected_count}, got {actual_count}"
    )

    text = text.replace(
        needle,
        "# CP-86.1D MUTANT: " + needle,
    )

    path = tmp_path / "production-release.yml"
    path.write_text(text, encoding="utf-8")
    return path


def run_verifier(script: Path, workflow: Path) -> subprocess.CompletedProcess[str]:
    code = f"""
import runpy
from pathlib import Path

real_path = Path

class RedirectedPath(type(real_path())):
    pass

namespace = runpy.run_path(
    {str(script)!r},
    run_name="cp861d_verifier",
)
"""

    # The current verifier executes at module load, so importing it while
    # replacing its WORKFLOW constant is not a safe injection seam.
    #
    # Execute a temporary source copy with only the WORKFLOW assignment
    # redirected. The verifier logic itself remains byte-identical.
    source = script.read_text(encoding="utf-8")

    old = (
        'WORKFLOW = ROOT / ".github/workflows/production-release.yml"'
    )
    new = f"WORKFLOW = Path({str(workflow)!r})"

    assert old in source, (
        f"verifier injection seam changed: {script.name}"
    )

    patched = source.replace(old, new, 1)

    runner = workflow.parent / f"run-{script.name}"
    runner.write_text(patched, encoding="utf-8")

    return subprocess.run(
        [sys.executable, str(runner)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def assert_rejected(
    script: Path,
    workflow: Path,
) -> None:
    result = run_verifier(script, workflow)

    assert result.returncode != 0, (
        f"{script.name} FALSE-PASS on non-executable trust-chain "
        f"operation\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


@pytest.mark.parametrize(
    "script",
    [CICD, PROVENANCE],
)
def test_commented_reproducibility_comparison_is_rejected(
    tmp_path: Path,
    script: Path,
) -> None:
    workflow = mutant_workflow(
        tmp_path,
        "python tools/verify_reproducible_artifacts.py",
    )

    assert_rejected(script, workflow)


@pytest.mark.parametrize(
    "script",
    [CICD, PROVENANCE],
)
def test_commented_exact_zip_promotion_is_rejected(
    tmp_path: Path,
    script: Path,
) -> None:
    workflow = mutant_workflow(
        tmp_path,
        "cp repro/a/aodsl-1.0.0-production-source.zip dist/",
    )

    assert_rejected(script, workflow)


def test_commented_attestations_are_rejected(
    tmp_path: Path,
) -> None:
    workflow = mutant_workflow(
        tmp_path,
        "uses: actions/attest@1e69f48acb82d1966a394da916b4c1698aa569d6",
        expected_count=2,
    )

    assert_rejected(PROVENANCE, workflow)
