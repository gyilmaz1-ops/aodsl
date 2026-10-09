"""CP-88I-C62L59T: Git release identity timeout contracts."""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from aodsl.certification.version_policy import (
    validate_git_release_identity,
)


@pytest.fixture
def release_identity(tmp_path):
    """Minimal valid identity; Git execution is mocked."""
    return {
        "repository_root": tmp_path,
        "git_tag": "v1.0.10",
        "github_ref_type": "tag",
        "github_sha": "a" * 40,
    }


def test_git_timeout_is_bounded_and_fails_closed(release_identity):
    observed = []

    def timeout_git(cmd, **kwargs):
        observed.append(kwargs.get("timeout"))
        raise subprocess.TimeoutExpired(
            cmd=cmd,
            timeout=kwargs.get("timeout") or 1,
        )

    with patch("subprocess.run", side_effect=timeout_git):
        with pytest.raises(
            ValueError, match="git identity lookup timed out"
        ) as exc:
            validate_git_release_identity(**release_identity)

    assert observed == [30]
    assert isinstance(
        exc.value.__cause__,
        subprocess.TimeoutExpired,
    )


def test_git_identity_accepts_matching_head_and_tag(release_identity):
    sha = release_identity["github_sha"]
    commands = []

    def successful_git(cmd, **kwargs):
        commands.append(cmd)
        assert kwargs["timeout"] == 30
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout=sha + "\n",
            stderr="",
        )

    with patch("subprocess.run", side_effect=successful_git):
        assert validate_git_release_identity(**release_identity) == sha

    assert len(commands) == 2
    assert commands[0] == ["git", "rev-parse", "--verify", "HEAD"]
    assert commands[1] == [
        "git",
        "rev-parse",
        "--verify",
        "refs/tags/v1.0.10^{}",
    ]


@pytest.mark.parametrize(
    "overrides",
    [
        {"github_ref_type": "branch"},
        {"git_tag": "invalid"},
        {"github_sha": ""},
    ],
)
def test_invalid_release_metadata_fails_closed(
    release_identity, overrides
):
    release_identity.update(overrides)

    with patch("subprocess.run") as run:
        with pytest.raises(ValueError):
            validate_git_release_identity(**release_identity)

    run.assert_not_called()


def test_git_execution_failure_fails_closed(release_identity):
    with patch(
        "subprocess.run",
        side_effect=OSError("git unavailable"),
    ):
        with pytest.raises(ValueError, match="unable to execute git"):
            validate_git_release_identity(**release_identity)


def test_git_nonzero_exit_fails_closed(release_identity):
    def failed_git(cmd, **kwargs):
        assert kwargs["timeout"] == 30
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=128,
            stdout="",
            stderr="failure",
        )

    with patch("subprocess.run", side_effect=failed_git):
        with pytest.raises(
            ValueError, match="git identity lookup failed"
        ):
            validate_git_release_identity(**release_identity)
