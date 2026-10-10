"""Fail-closed release version policy."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path


_VERSION = re.compile(
    r"(?:0|[1-9][0-9]*)"
    r"\.(?:0|[1-9][0-9]*)"
    r"\.(?:0|[1-9][0-9]*)"
)


def _require_version(version: str) -> str:
    if not isinstance(version, str):
        raise ValueError("release version must be a string")

    if not _VERSION.fullmatch(version):
        raise ValueError(f"invalid release version: {version!r}")

    return version


def validate_release_version(
    *,
    package_version: str,
    runtime_version: str,
    investment_version: str,
    git_tag: str,
) -> str:
    version = _require_version(package_version)

    if runtime_version != version:
        raise ValueError("runtime version mismatch")

    if investment_version != version:
        raise ValueError("investment domain version mismatch")

    if git_tag != f"v{version}":
        raise ValueError("git tag version mismatch")

    return version


def validate_git_release_identity(
    *,
    repository_root: Path,
    git_tag: str,
    github_ref_type: str,
    github_sha: str,
) -> str:
    """Fail-closed validation of GitHub release identity.

    The supplied release tag must:
      - be a valid vX.Y.Z release tag,
      - be a tag reference, not a branch reference,
      - exist in the repository,
      - resolve to the repository HEAD,
      - and match the GitHub workflow commit SHA.
    """

    import subprocess

    root = Path(repository_root)

    if not root.is_dir():
        raise ValueError("repository root does not exist")

    if github_ref_type != "tag":
        raise ValueError("GitHub release reference must be a tag")

    if not isinstance(git_tag, str):
        raise ValueError("git tag must be a string")

    if not re.fullmatch(r"v" + _VERSION.pattern, git_tag):
        raise ValueError(f"invalid git release tag: {git_tag!r}")

    if not isinstance(github_sha, str) or not github_sha:
        raise ValueError("GitHub commit SHA is required")

    def git(*args: str) -> str:
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
                timeout=30,
            )
        except subprocess.TimeoutExpired as exc:
            raise ValueError(
                "git identity lookup timed out"
            ) from exc
        except OSError as exc:
            raise ValueError("unable to execute git") from exc

        if result.returncode != 0:
            raise ValueError(
                f"git identity lookup failed: {' '.join(args)}"
            )

        return result.stdout.strip()

    try:
        head = git("rev-parse", "--verify", "HEAD")
        github_commit = github_sha.strip()

        tag_commit = git(
            "rev-parse",
            "--verify",
            f"refs/tags/{git_tag}^{{}}",
        )
    except ValueError:
        raise

    if head != github_commit:
        raise ValueError(
            "GitHub commit SHA does not match repository HEAD"
        )

    if tag_commit != head:
        raise ValueError(
            "git tag does not resolve to repository HEAD"
        )

    return head


def artifact_names(version: str) -> tuple[str, str]:
    version = _require_version(version)

    return (
        f"aodsl-{version}-production-source.zip",
        f"aodsl-{version}.cdx.json",
    )



def read_project_version(pyproject_path: Path) -> str:
    """Read the authoritative version from pyproject.toml."""

    path = Path(pyproject_path)

    try:
        with path.open("rb") as stream:
            metadata = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(
            f"cannot read project metadata: {path}"
        ) from exc

    project = metadata.get("project")

    if not isinstance(project, dict):
        raise ValueError("missing [project] metadata")

    if project.get("name") != "aodsl":
        raise ValueError("unexpected project name")

    version = project.get("version")

    return _require_version(version)



def validate_artifact_binding(
    *,
    pyproject_path: Path,
    artifact_name: str,
    sbom_name: str,
) -> str:
    """Validate release filenames against authoritative project version."""

    version = read_project_version(pyproject_path)
    expected_artifact, expected_sbom = artifact_names(version)

    if artifact_name != expected_artifact:
        raise ValueError(
            "release artifact version/name mismatch"
        )

    if sbom_name != expected_sbom:
        raise ValueError(
            "release SBOM version/name mismatch"
        )

    return version
