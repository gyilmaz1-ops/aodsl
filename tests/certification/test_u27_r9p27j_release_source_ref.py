from pathlib import Path
import os
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools/verify_release_source_ref.py"


def run_git(repo, *args):
    return subprocess.check_output(
        ["git", *args],
        cwd=repo,
        text=True,
    ).strip()


@pytest.fixture
def release_repo(tmp_path):
    repo = tmp_path / "release-repo"
    repo.mkdir()

    subprocess.run(
        ["git", "init", "-q", str(repo)],
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "AODSL Test"],
        cwd=repo,
        check=True,
    )

    (repo / "pyproject.toml").write_text(
        '[project]\nname = "aodsl"\nversion = "1.0.11"\n'
    )

    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "fixture"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "tag", "v1.0.11"],
        cwd=repo,
        check=True,
    )
    return repo


def context(repo, **changes):
    sha = run_git(repo, "rev-parse", "HEAD")
    values = {
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF_TYPE": "tag",
        "GITHUB_REF": "refs/tags/v1.0.11",
        "GITHUB_REF_NAME": "v1.0.11",
        "GITHUB_SHA": sha,
    }
    values.update(changes)
    return values


def invoke(repo, env):
    process_env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GITHUB_")
    }
    process_env.update(env)

    assert SCRIPT.is_file(), (
        "TEST_INFRASTRUCTURE_ERROR: "
        "release source verifier is missing"
    )

    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=repo,
        env=process_env,
        text=True,
        capture_output=True,
    )



def assert_rejected(result, expected_code):
    output = result.stdout + result.stderr

    assert result.returncode == 2, output

    assert expected_code in output, (
        f"Expected {expected_code}, got: {output}"
    )

    assert "Traceback (most recent call last)" not in output
    assert "can't open file" not in output


def test_valid_lightweight_tag_push(release_repo):
    result = invoke(release_repo, context(release_repo))
    assert result.returncode == 0, result.stdout + result.stderr


def test_valid_annotated_tag_push(release_repo):
    subprocess.run(
        ["git", "tag", "-a", "-f", "v1.0.11", "-m", "release"],
        cwd=release_repo,
        check=True,
    )
    result = invoke(release_repo, context(release_repo))
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "changes, expected_code",
    [
        ({"GITHUB_EVENT_NAME": "workflow_dispatch"}, "SOURCE-REF-001"),
        ({"GITHUB_REF_TYPE": "branch"}, "SOURCE-REF-001"),
        ({"GITHUB_REF": "refs/heads/main"}, "SOURCE-REF-001"),
        ({"GITHUB_REF_NAME": "v1.0.10"}, "SOURCE-REF-001"),
        ({"GITHUB_SHA": "0" * 40}, "SOURCE-REF-003"),
        ({"GITHUB_EVENT_NAME": ""}, "SOURCE-REF-001"),
        ({"GITHUB_REF": ""}, "SOURCE-REF-001"),
    ],
)
def test_invalid_context_rejected(
    release_repo, changes, expected_code
):
    result = invoke(release_repo, context(release_repo, **changes))
    assert_rejected(result, expected_code)


def test_missing_tag_rejected(release_repo):
    subprocess.run(
        ["git", "tag", "-d", "v1.0.11"],
        cwd=release_repo,
        check=True,
    )
    result = invoke(release_repo, context(release_repo))
    assert_rejected(result, "SOURCE-REF-002")


def test_tag_points_to_different_commit_rejected(release_repo):
    original = run_git(release_repo, "rev-parse", "HEAD")
    (release_repo / "change.txt").write_text('new commit\n')
    subprocess.run(["git", "add", "."], cwd=release_repo, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "next"],
        cwd=release_repo,
        check=True,
    )
    result = invoke(
        release_repo,
        context(release_repo, GITHUB_SHA=original),
    )
    assert_rejected(result, "SOURCE-REF-003")



def test_tag_target_mismatch_with_matching_github_sha_rejected(
    release_repo,
):
    original = run_git(release_repo, "rev-parse", "HEAD")

    (release_repo / "change.txt").write_text("new commit\n")

    subprocess.run(
        ["git", "add", "."],
        cwd=release_repo,
        check=True,
    )
    subprocess.run(
        ["git", "commit", "-qm", "next"],
        cwd=release_repo,
        check=True,
    )

    current = run_git(release_repo, "rev-parse", "HEAD")

    assert current != original
    assert run_git(
        release_repo,
        "rev-parse",
        "refs/tags/v1.0.11^{}",
    ) == original

    result = invoke(
        release_repo,
        context(release_repo),
    )

    assert_rejected(result, "SOURCE-REF-002")


def test_version_mismatch_rejected(release_repo):
    (release_repo / "pyproject.toml").write_text(
        '[project]\nname = "aodsl"\nversion = "1.0.12"\n'
    )
    result = invoke(release_repo, context(release_repo))
    assert_rejected(result, "SOURCE-REF-004")


def test_committed_version_mismatch_rejected(release_repo):
    (release_repo / "pyproject.toml").write_text(
        '[project]\nname = "aodsl"\nversion = "1.0.12"\n'
    )

    subprocess.run(
        ["git", "add", "pyproject.toml"],
        cwd=release_repo,
        check=True,
    )
    subprocess.run(
        ["git", "commit", "-qm", "version mismatch"],
        cwd=release_repo,
        check=True,
    )

    subprocess.run(
        ["git", "tag", "-f", "v1.0.11"],
        cwd=release_repo,
        check=True,
    )

    head = run_git(release_repo, "rev-parse", "HEAD")
    tag_commit = run_git(
        release_repo,
        "rev-parse",
        "refs/tags/v1.0.11^{}",
    )

    assert head == tag_commit
    assert run_git(
        release_repo,
        "show",
        "HEAD:pyproject.toml",
    ).find('version = "1.0.12"') >= 0

    result = invoke(
        release_repo,
        context(release_repo),
    )

    assert_rejected(result, "SOURCE-REF-004")


def test_parent_repository_discovery_rejected(
    release_repo, tmp_path
):
    import os
    import subprocess
    import sys

    outer = release_repo
    nested = outer / "nested"
    nested.mkdir()

    metadata = (outer / "pyproject.toml").read_bytes()
    (nested / "pyproject.toml").write_bytes(metadata)

    sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=outer,
        text=True,
    ).strip()

    env = os.environ.copy()
    env.update({
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF_TYPE": "tag",
        "GITHUB_REF": "refs/tags/v1.0.11",
        "GITHUB_REF_NAME": "v1.0.11",
        "GITHUB_SHA": sha,
    })

    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=nested,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )

    assert result.returncode != 0
    assert "SOURCE-REF-002" in (
        result.stdout + result.stderr
    )


def test_git_environment_redirection_rejected(
    release_repo, tmp_path
):
    import os
    import subprocess
    import sys

    repository = release_repo
    target = tmp_path / "redirected-target"
    target.mkdir()

    metadata = (repository / "pyproject.toml").read_bytes()
    (target / "pyproject.toml").write_bytes(metadata)

    sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        text=True,
    ).strip()

    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_")
    }

    env.update({
        "GIT_DIR": str(repository / ".git"),
        "GIT_WORK_TREE": str(target),
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF_TYPE": "tag",
        "GITHUB_REF": "refs/tags/v1.0.11",
        "GITHUB_REF_NAME": "v1.0.11",
        "GITHUB_SHA": sha,
    })

    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=target,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )

    assert result.returncode != 0, (
        "Verifier accepted externally redirected Git metadata: "
        + result.stdout
        + result.stderr
    )
    assert "SOURCE-REF-002" in (
        result.stdout + result.stderr
    )


def test_path_executable_redirection_rejected(
    release_repo, tmp_path
):
    import os
    import subprocess
    import sys

    marker = tmp_path / "fake-git-invoked"
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()

    fake_git = fake_bin / "git"
    fake_git.write_text(
        "#!/bin/sh\\n"
        f'echo invoked >> "{marker}"\\n'
        "exit 97\\n"
    )
    fake_git.chmod(0o755)

    sha = subprocess.check_output(
        ["/usr/bin/git", "rev-parse", "HEAD"],
        cwd=release_repo,
        text=True,
    ).strip()

    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_")
    }
    env.update({
        "PATH": str(fake_bin) + os.pathsep + os.environ["PATH"],
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF_TYPE": "tag",
        "GITHUB_REF": "refs/tags/v1.0.11",
        "GITHUB_REF_NAME": "v1.0.11",
        "GITHUB_SHA": sha,
    })

    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=release_repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )

    assert not marker.exists(), (
        "Verifier executed attacker-controlled Git binary"
    )
    assert result.returncode == 0, (
        result.stdout + result.stderr
    )
    assert "SOURCE-REF: PASS" in result.stdout
