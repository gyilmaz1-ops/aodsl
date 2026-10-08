import pytest

from aodsl.certification.release_identity import TAG


@pytest.mark.parametrize(
    "tag",
    [
        "v1.2.3",
        "v0.0.1",
        "v1.2.3-rc.1",
        "v1.2.3+build.5",
        "v1.2.3-rc.1+build.5",
    ],
)
def test_valid_semver_tag_is_accepted(tag):
    assert TAG.fullmatch(tag) is not None


@pytest.mark.parametrize(
    "tag",
    [
        "v01.2.3",
        "v1.02.3",
        "v1.2.03",
        "v1.2.3-rc..1",
        "v1.2.3-01",
        "v1.2.3-rc.01",
        "v1.2.3+build..5",
        "v1.2.3-",
        "v1.2.3+",
        "1.2.3",
    ],
)
def test_invalid_semver_tag_is_rejected(tag):
    assert TAG.fullmatch(tag) is None



@pytest.mark.parametrize(
    "tag",
    [
        "v0.0.0",
        "v10.20.30",
        "v1.2.3-0",
        "v1.2.3-alpha",
        "v1.2.3-alpha-beta",
        "v1.2.3-alpha.0",
        "v1.2.3-rc.1+build.01",
        "v1.2.3+001",
        "v1.2.3+build-001",
        "v1.2.3-0A",
    ],
)
def test_cp88ic37_extended_valid_semver(tag):
    assert TAG.fullmatch(tag) is not None


@pytest.mark.parametrize(
    "tag",
    [
        "v00.0.0",
        "v1.2.3-alpha.00",
        "v1.2.3-0001",
        "v1.2.3-alpha..beta",
        "v1.2.3-alpha.",
        "v1.2.3+.build",
        "v1.2.3+build.",
        "v1.2.3-+build",
        "v1.2.3-rc.1+build..2",
        "v1.2.3-rc_1",
        "v1.2.3+build_1",
        "v1.2.3-rc.1+build+extra",
    ],
)
def test_cp88ic37_extended_invalid_semver(tag):
    assert TAG.fullmatch(tag) is None



@pytest.mark.parametrize(
    "invalid_tag",
    [
        "v01.2.3",
        "v1.2.3-01",
        "v1.2.3-rc..1",
        "v1.2.3+build..5",
    ],
)
def test_cp88ic37_build_rejects_invalid_tag(tmp_path, invalid_tag):
    from test_inv052_release_identity_binding import (
        fixture,
        COMMIT,
        REPO,
    )
    from aodsl.certification.release_identity import (
        build_release_identity,
        WORKFLOW_PATH,
    )

    artifact, manifest, sbom, reproducibility, bundle_root, bundle = (
        fixture(tmp_path)
    )

    workflow_ref = (
        f"{REPO}/{WORKFLOW_PATH}@refs/tags/{invalid_tag}"
    )

    with pytest.raises(ValueError, match="git tag"):
        build_release_identity(
            tmp_path,
            artifact,
            manifest,
            sbom,
            reproducibility,
            bundle_root,
            bundle,
            git_commit_sha=COMMIT,
            git_tag=invalid_tag,
            repository=REPO,
            workflow_ref=workflow_ref,
        )


@pytest.mark.parametrize(
    "invalid_tag",
    [
        "v01.2.3",
        "v1.2.3-01",
        "v1.2.3-rc..1",
        "v1.2.3+build..5",
    ],
)
def test_cp88ic37_verify_rejects_invalid_tag(tmp_path, invalid_tag):
    from test_inv052_release_identity_binding import (
        build,
        verify,
    )

    (
        artifact,
        manifest,
        sbom,
        reproducibility,
        bundle_root,
        bundle,
        identity_path,
        identity,
    ) = build(tmp_path)

    errors = verify(
        tmp_path,
        artifact,
        manifest,
        sbom,
        reproducibility,
        bundle_root,
        bundle,
        identity_path,
        git_tag=invalid_tag,
    )

    assert errors
    assert any("git tag" in error for error in errors)
