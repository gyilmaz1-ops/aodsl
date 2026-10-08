
import json

import pytest

from aodsl.certification.certified_bundle import (
    CertifiedBundleError,
    REQUIRED_PAYLOAD_PATHS,
    build_certified_bundle_manifest,
    verify_certified_bundle_manifest,
)


def populate(root, paths):
    for index, relative in enumerate(paths):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(f"payload-{index}".encode())


def version_101_paths():
    return tuple(
        path.replace("aodsl-1.0.0", "aodsl-1.0.1")
        for path in REQUIRED_PAYLOAD_PATHS
    )


def write_manifest(root, manifest):
    path = root / "dist/certified-bundle-manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def test_version_009_expected_payload_paths():
    from aodsl.certification.certified_bundle import (
        required_payload_paths,
    )

    paths = required_payload_paths("1.0.1")

    assert paths == version_101_paths()
    assert len(paths) == 7
    assert len(set(paths)) == 7
    assert required_payload_paths("1.0.0") == REQUIRED_PAYLOAD_PATHS


def test_version_009_build_and_verify_versioned_bundle(tmp_path):
    paths = version_101_paths()
    populate(tmp_path, paths)

    manifest = build_certified_bundle_manifest(
        tmp_path,
        version="1.0.1",
    )

    assert [entry["path"] for entry in manifest["payload"]] == list(paths)

    manifest_path = write_manifest(tmp_path, manifest)

    assert verify_certified_bundle_manifest(
        tmp_path,
        manifest_path,
        version="1.0.1",
    ) == []


def test_version_009_extra_payload_fails_closed(tmp_path):
    paths = version_101_paths()
    populate(tmp_path, paths)

    manifest = build_certified_bundle_manifest(
        tmp_path,
        version="1.0.1",
    )
    manifest_path = write_manifest(tmp_path, manifest)

    extra = tmp_path / "dist/unexpected.bin"
    extra.write_bytes(b"unexpected")

    errors = verify_certified_bundle_manifest(
        tmp_path,
        manifest_path,
        version="1.0.1",
    )

    assert errors
    assert any("unexpected" in error.lower() for error in errors)
