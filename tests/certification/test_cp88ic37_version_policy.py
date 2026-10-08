import pytest

from aodsl.certification.version_policy import (
    artifact_names,
    validate_release_version,
)


def test_version_001_consistent_release():
    assert validate_release_version(
        package_version="1.0.0",
        runtime_version="1.0.0",
        investment_version="1.0.0",
        git_tag="v1.0.0",
    ) == "1.0.0"


@pytest.mark.parametrize(
    "runtime,investment,tag",
    [
        ("1.0.1", "1.0.0", "v1.0.0"),
        ("1.0.0", "1.0.1", "v1.0.0"),
        ("1.0.0", "1.0.0", "v1.0.1"),
    ],
)
def test_version_001_mismatch_fails_closed(
    runtime, investment, tag
):
    with pytest.raises(ValueError):
        validate_release_version(
            package_version="1.0.0",
            runtime_version=runtime,
            investment_version=investment,
            git_tag=tag,
        )


def test_version_002_artifact_names():
    assert artifact_names("1.0.1") == (
        "aodsl-1.0.1-production-source.zip",
        "aodsl-1.0.1.cdx.json",
    )


def test_version_002_invalid_version_fails_closed():
    with pytest.raises(ValueError):
        artifact_names("../1.0.0")


def test_version_003_project_version_authority(tmp_path):
    from aodsl.certification.version_policy import read_project_version

    project = tmp_path / "pyproject.toml"
    project.write_text(
        '[project]\nname = "aodsl"\nversion = "1.0.1"\n',
        encoding="utf-8",
    )

    assert read_project_version(project) == "1.0.1"


def test_version_003_missing_project_version_fails_closed(tmp_path):
    from aodsl.certification.version_policy import read_project_version

    project = tmp_path / "pyproject.toml"
    project.write_text(
        '[project]\nname = "aodsl"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        read_project_version(project)


def test_version_003_invalid_project_version_fails_closed(tmp_path):
    from aodsl.certification.version_policy import read_project_version

    project = tmp_path / "pyproject.toml"
    project.write_text(
        '[project]\nname = "aodsl"\nversion = "../1.0.1"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        read_project_version(project)



def test_version_004_release_artifact_binding(tmp_path):
    from aodsl.certification.version_policy import (
        validate_artifact_binding,
    )

    project = tmp_path / "pyproject.toml"
    project.write_text(
        '[project]\nname = "aodsl"\nversion = "1.0.1"\n',
        encoding="utf-8",
    )

    assert validate_artifact_binding(
        pyproject_path=project,
        artifact_name="aodsl-1.0.1-production-source.zip",
        sbom_name="aodsl-1.0.1.cdx.json",
    ) == "1.0.1"


@pytest.mark.parametrize(
    "artifact,sbom",
    [
        (
            "aodsl-1.0.0-production-source.zip",
            "aodsl-1.0.1.cdx.json",
        ),
        (
            "aodsl-1.0.1-production-source.zip",
            "aodsl-1.0.0.cdx.json",
        ),
    ],
)
def test_version_004_mismatch_fails_closed(
    tmp_path, artifact, sbom
):
    from aodsl.certification.version_policy import (
        validate_artifact_binding,
    )

    project = tmp_path / "pyproject.toml"
    project.write_text(
        '[project]\nname = "aodsl"\nversion = "1.0.1"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        validate_artifact_binding(
            pyproject_path=project,
            artifact_name=artifact,
            sbom_name=sbom,
        )
