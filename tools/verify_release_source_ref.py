#!/usr/bin/env python3
"""Fail-closed verification of GitHub release source identity."""

import os
from pathlib import Path
import re
import subprocess
import sys

from aodsl.certification.version_policy import (
    read_project_version,
    validate_release_version,
)


TAG_PATTERN = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+")
SHA_PATTERN = re.compile(r"[0-9a-fA-F]{40}")


def isolated_git_environment() -> dict[str, str]:
    """Remove inherited Git-specific subprocess overrides."""
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_")
    }


class SourceRefError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def reject(code: str, message: str) -> None:
    raise SourceRefError(code, message)


def git(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["/usr/bin/git", *args],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
            env=isolated_git_environment(),
        )
    except (OSError, subprocess.TimeoutExpired):
        reject("SOURCE-REF-002", "Git identity lookup unavailable")

    if result.returncode != 0:
        reject("SOURCE-REF-002", "Git identity lookup failed")

    return result.stdout.strip()


def verify(root: Path) -> str:
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    ref_type = os.environ.get("GITHUB_REF_TYPE", "")
    ref = os.environ.get("GITHUB_REF", "")
    tag = os.environ.get("GITHUB_REF_NAME", "")
    sha = os.environ.get("GITHUB_SHA", "")

    if (
        event != "push"
        or ref_type != "tag"
        or not TAG_PATTERN.fullmatch(tag)
        or ref != f"refs/tags/{tag}"
    ):
        reject(
            "SOURCE-REF-001",
            "Invalid GitHub release event or tag context",
        )

    if not SHA_PATTERN.fullmatch(sha):
        reject("SOURCE-REF-003", "Invalid GitHub commit SHA")

    repository_root = git(
        root,
        "rev-parse",
        "--show-toplevel",
    )

    if Path(repository_root).resolve() != root.resolve():
        reject(
            "SOURCE-REF-002",
            "Git repository root does not match verification root",
        )

    head = git(root, "rev-parse", "--verify", "HEAD^{commit}")

    if head.lower() != sha.lower():
        reject(
            "SOURCE-REF-003",
            "GitHub SHA does not match repository HEAD",
        )

    tag_commit = git(
        root,
        "rev-parse",
        "--verify",
        f"refs/tags/{tag}^{{commit}}",
    )

    if tag_commit != head:
        reject(
            "SOURCE-REF-002",
            "Release tag does not resolve to HEAD",
        )

    metadata = root / "pyproject.toml"

    try:
        committed = subprocess.run(
            ["/usr/bin/git", "show", "HEAD:pyproject.toml"],
            cwd=root,
            capture_output=True,
            check=False,
            timeout=30,
            env=isolated_git_environment(),
        )
    except (OSError, subprocess.TimeoutExpired):
        reject(
            "SOURCE-REF-004",
            "Cannot inspect committed project metadata",
        )

    if committed.returncode != 0:
        reject(
            "SOURCE-REF-004",
            "Committed project metadata is missing",
        )

    try:
        working_bytes = metadata.read_bytes()
    except OSError:
        reject(
            "SOURCE-REF-004",
            "Working project metadata is unavailable",
        )

    if working_bytes != committed.stdout:
        reject(
            "SOURCE-REF-004",
            "Working project metadata differs from HEAD",
        )

    try:
        version = read_project_version(metadata)
        validate_release_version(
            package_version=version,
            runtime_version=version,
            investment_version=version,
            git_tag=tag,
        )
    except ValueError:
        reject(
            "SOURCE-REF-004",
            "Project version does not match release tag",
        )

    return head


def main() -> int:
    try:
        head = verify(Path.cwd())
    except SourceRefError as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return 2

    print(f"SOURCE-REF: PASS {head}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
