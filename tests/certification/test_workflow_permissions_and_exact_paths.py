from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/production-release.yml"
SCRIPTS = (
    "verify_cicd_policy.py",
    "verify_signed_provenance_policy.py",
)

def verify(tmp_path, script, text):
    workflow = tmp_path / "production-release.yml"
    workflow.write_text(text, encoding="utf-8")
    source = (ROOT / "tools" / script).read_text(encoding="utf-8")
    seam = 'WORKFLOW = ROOT / ".github/workflows/production-release.yml"'
    assert source.count(seam) == 1
    source = source.replace(seam, f"WORKFLOW = Path({str(workflow)!r})", 1)
    runner = tmp_path / script
    runner.write_text(source, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(runner)],
        cwd=ROOT, capture_output=True, text=True,
    )

@pytest.mark.parametrize("script", SCRIPTS)
def test_current_workflow_accepted(tmp_path, script):
    result = verify(tmp_path, script, WORKFLOW.read_text(encoding="utf-8"))
    assert result.returncode == 0, result.stdout + result.stderr

MUTATIONS = [
    (
        "permissions:\n  contents: read\n",
        "permissions:\n  contents: read\n  id-token: write\n",
    ),
    (
        "  reproducible-build:\n",
        "  reproducible-build:\n    permissions:\n"
        "      contents: read\n      id-token: write\n",
    ),
    (
        "      attestations: write\n",
        "      attestations: read\n",
    ),
    (
        "      contents: read\n      id-token: write\n",
        "      contents: write\n      id-token: write\n",
    ),
    (
        "      attestations: write\n",
        "      attestations: write\n      packages: write\n",
    ),
    (
        "subject-path: 'dist/${{ env.AODSL_ARTIFACT }}'",
        "subject-path: 'dist/*-production-source.zip'",
    ),
    (
        "subject-path: 'dist/${{ env.AODSL_SBOM }}'",
        "subject-path: 'dist/wrong.cdx.json'",
    ),
    (
        "            dist/${{ env.AODSL_ARTIFACT }}\n",
        "            dist/*-production-source.zip\n",
    ),
    (
        "            dist/release-manifest.json\n",
        "",
    ),
    (
        "            dist/release-manifest.json\n",
        "            dist/release-manifest.json\n"
        "            dist/extra.json\n",
    ),
    (
        "            dist/release-manifest.json\n",
        "            dist/release-manifest.json\n"
        "            dist/release-manifest.json\n",
    ),
]

@pytest.mark.parametrize("script", SCRIPTS)
@pytest.mark.parametrize("old,new", MUTATIONS)
def test_unsafe_workflow_rejected(tmp_path, script, old, new):
    text = WORKFLOW.read_text(encoding="utf-8")
    assert text.count(old) == 1, repr(old)
    result = verify(tmp_path, script, text.replace(old, new, 1))
    assert result.returncode == 2, result.stdout + result.stderr
    assert "FAILED" in result.stdout


@pytest.mark.parametrize("script", SCRIPTS)
@pytest.mark.parametrize("old,new", [
    ("      packages: read\n", "      packages: write\n"),
    ("      packages: read\n", ""),
    (
        "        password: ${{ secrets.GITHUB_TOKEN }}",
        "        password: ${{ secrets.WRONG_TOKEN }}",
    ),
    (
        "        username: ${{ github.actor }}",
        "        username: wrong-account",
    ),
])
def test_registry_access_contract_rejected(tmp_path, script, old, new):
    text = WORKFLOW.read_text(encoding="utf-8")
    assert text.count(old) == 2, repr(old)
    result = verify(tmp_path, script, text.replace(old, new))
    assert result.returncode == 2, result.stdout + result.stderr
    assert "FAILED" in result.stdout


@pytest.mark.parametrize("script", SCRIPTS)
@pytest.mark.parametrize("job_name", [
    "reproducible-build", "production-gate",
])
@pytest.mark.parametrize("mutation", [
    "missing", "wildcard", "wrong_directory",
    "before_checkout", "after_source_ref", "duplicate",
])
def test_workspace_git_trust_contract_rejected(
    tmp_path, script, job_name, mutation,
):
    text = WORKFLOW.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    job_start = lines.index(f"  {job_name}:\n")
    job_end = next(
        (
            index for index in range(job_start + 1, len(lines))
            if lines[index].startswith("  ")
            and not lines[index].startswith("   ")
            and lines[index].strip()
            and not lines[index].lstrip().startswith("#")
        ),
        len(lines),
    )
    job = "".join(lines[job_start:job_end])
    trust_marker = "      - name: Configure workspace Git trust\n"
    assert job.count(trust_marker) == 1
    start = job.index(trust_marker)
    end = job.find("      - ", start + len(trust_marker))
    assert end != -1
    block = job[start:end]

    if mutation == "missing":
        changed = job[:start] + job[end:]
    elif mutation == "wildcard":
        assert block.count('"safe.directory", str(workspace),') == 1
        altered = block.replace(
            '"safe.directory", str(workspace),',
            '"safe.directory", "*",',
        )
        changed = job[:start] + altered + job[end:]
    elif mutation == "wrong_directory":
        altered = block.replace(
            'Path(os.environ["GITHUB_WORKSPACE"]).resolve()',
            'Path("/tmp").resolve()',
        )
        assert altered != block
        changed = job[:start] + altered + job[end:]
    elif mutation == "duplicate":
        changed = job[:start] + block + block + job[end:]
    else:
        remaining = job[:start] + job[end:]
        if mutation == "before_checkout":
            marker = "      - name: Checkout exact release source\n"
            assert remaining.count(marker) == 1
            position = remaining.index(marker)
        else:
            marker = "      - name: Verify release source reference\n"
            assert remaining.count(marker) == 1
            source_start = remaining.index(marker)
            position = remaining.find(
                "      - ", source_start + len(marker)
            )
            assert position != -1
        changed = remaining[:position] + block + remaining[position:]

    mutated = (
        "".join(lines[:job_start])
        + changed
        + "".join(lines[job_end:])
    )
    result = verify(tmp_path, script, mutated)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "workspace Git trust" in result.stdout
