from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from aodsl.certification.certified_bundle import (
    BUNDLE_MANIFEST_PATH,
    CLOSURE_POLICY,
    REQUIRED_PAYLOAD_PATHS,
    CertifiedBundleError,
    _validate_relative_path,
    build_certified_bundle_manifest,
    verify_certified_bundle_manifest,
)


def populate(root: Path) -> None:
    for index, relative in enumerate(REQUIRED_PAYLOAD_PATHS):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(
            f"payload-{index}:{relative}\n".encode("utf-8")
        )


def write_manifest(root: Path, manifest: dict) -> Path:
    path = root / BUNDLE_MANIFEST_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def test_inv056_builds_exact_physical_payload_closure(tmp_path):
    populate(tmp_path)

    manifest = build_certified_bundle_manifest(tmp_path)

    assert manifest["schema"] == "aodsl.certified-bundle.v1"
    assert manifest["invariant"] == "INV-056"
    assert manifest["hash_algorithm"] == "sha256"
    assert manifest["closure"] == {
        "policy": CLOSURE_POLICY,
        "payload_count": len(REQUIRED_PAYLOAD_PATHS),
        "self_excluded_path": BUNDLE_MANIFEST_PATH,
    }
    assert [
        entry["path"] for entry in manifest["payload"]
    ] == list(REQUIRED_PAYLOAD_PATHS)


def test_inv056_manifest_verifies_exact_bundle(tmp_path):
    populate(tmp_path)
    manifest = build_certified_bundle_manifest(tmp_path)
    path = write_manifest(tmp_path, manifest)

    assert verify_certified_bundle_manifest(tmp_path, path) == []


def test_inv056_manifest_self_exclusion_succeeds(tmp_path):
    populate(tmp_path)
    manifest = build_certified_bundle_manifest(tmp_path)
    path = write_manifest(tmp_path, manifest)

    assert path.is_file()
    assert verify_certified_bundle_manifest(tmp_path, path) == []


def test_inv056_build_rejects_unexpected_physical_payload(tmp_path):
    populate(tmp_path)

    extra = tmp_path / "dist/unexpected.bin"
    extra.write_bytes(b"undeclared")

    with pytest.raises(
        CertifiedBundleError,
        match="physical closure mismatch",
    ):
        build_certified_bundle_manifest(tmp_path)


def test_inv056_verify_rejects_unexpected_physical_payload(tmp_path):
    populate(tmp_path)
    manifest = build_certified_bundle_manifest(tmp_path)
    path = write_manifest(tmp_path, manifest)

    extra = tmp_path / "dist/unexpected.bin"
    extra.write_bytes(b"undeclared")

    errors = verify_certified_bundle_manifest(tmp_path, path)

    assert errors
    assert "physical closure mismatch" in errors[0]
    assert "dist/unexpected.bin" in errors[0]


def test_inv056_missing_payload_fails_closed(tmp_path):
    populate(tmp_path)
    manifest = build_certified_bundle_manifest(tmp_path)
    path = write_manifest(tmp_path, manifest)

    (tmp_path / REQUIRED_PAYLOAD_PATHS[0]).unlink()

    errors = verify_certified_bundle_manifest(tmp_path, path)

    assert errors
    assert "physical closure mismatch" in errors[0]


def test_inv056_modified_payload_digest_fails_closed(tmp_path):
    populate(tmp_path)
    manifest = build_certified_bundle_manifest(tmp_path)
    path = write_manifest(tmp_path, manifest)

    target = tmp_path / REQUIRED_PAYLOAD_PATHS[0]
    original_size = target.stat().st_size
    target.write_bytes(b"x" * original_size)

    errors = verify_certified_bundle_manifest(tmp_path, path)

    assert errors
    assert "SHA-256 mismatch" in errors[0]


def test_inv056_modified_payload_size_fails_closed(tmp_path):
    populate(tmp_path)
    manifest = build_certified_bundle_manifest(tmp_path)
    path = write_manifest(tmp_path, manifest)

    target = tmp_path / REQUIRED_PAYLOAD_PATHS[0]
    target.write_bytes(target.read_bytes() + b"x")

    errors = verify_certified_bundle_manifest(tmp_path, path)

    assert errors
    assert "size mismatch" in errors[0]


def test_inv056_missing_declared_entry_fails_closed(tmp_path):
    populate(tmp_path)
    manifest = build_certified_bundle_manifest(tmp_path)
    manifest["payload"].pop()
    manifest["closure"]["payload_count"] -= 1
    path = write_manifest(tmp_path, manifest)

    errors = verify_certified_bundle_manifest(tmp_path, path)

    assert errors
    assert "payload closure mismatch" in errors[0]


def test_inv056_unexpected_declared_entry_fails_closed(tmp_path):
    populate(tmp_path)
    manifest = build_certified_bundle_manifest(tmp_path)
    manifest["payload"].append(
        {
            "path": "dist/unexpected.bin",
            "size": 0,
            "sha256": "0" * 64,
        }
    )
    manifest["closure"]["payload_count"] += 1
    path = write_manifest(tmp_path, manifest)

    errors = verify_certified_bundle_manifest(tmp_path, path)

    assert errors
    assert "payload closure mismatch" in errors[0]


def test_inv056_duplicate_path_fails_closed(tmp_path):
    populate(tmp_path)
    manifest = build_certified_bundle_manifest(tmp_path)
    manifest["payload"].append(dict(manifest["payload"][0]))
    manifest["closure"]["payload_count"] += 1
    path = write_manifest(tmp_path, manifest)

    errors = verify_certified_bundle_manifest(tmp_path, path)

    assert errors
    assert "duplicate certified bundle payload path" in errors[0]


@pytest.mark.parametrize(
    "value",
    (
        "../escape",
        "/absolute/path",
        "dist/../escape",
        "./dist/file",
        "dist\\file",
    ),
)
def test_inv056_rejects_noncanonical_or_escaping_paths(value):
    with pytest.raises(CertifiedBundleError):
        _validate_relative_path(value)


def test_inv056_payload_order_is_canonical(tmp_path):
    populate(tmp_path)
    manifest = build_certified_bundle_manifest(tmp_path)
    manifest["payload"] = list(reversed(manifest["payload"]))
    path = write_manifest(tmp_path, manifest)

    errors = verify_certified_bundle_manifest(tmp_path, path)

    assert errors
    assert "payload order mismatch" in errors[0]


def test_inv056_manifest_is_deterministic(tmp_path):
    populate(tmp_path)

    first = build_certified_bundle_manifest(tmp_path)
    second = build_certified_bundle_manifest(tmp_path)

    assert first == second
    assert (
        json.dumps(first, sort_keys=True, separators=(",", ":"))
        == json.dumps(second, sort_keys=True, separators=(",", ":"))
    )


def test_inv056_symlink_payload_is_rejected(tmp_path):
    populate(tmp_path)

    target = tmp_path / REQUIRED_PAYLOAD_PATHS[0]
    target.unlink()

    outside = tmp_path.parent / (
        f"{tmp_path.name}-outside-payload"
    )
    outside.write_bytes(b"outside")

    try:
        os.symlink(outside, target)

        with pytest.raises(
            CertifiedBundleError,
            match="symlink forbidden",
        ):
            build_certified_bundle_manifest(tmp_path)
    finally:
        if target.is_symlink():
            target.unlink()
        outside.unlink(missing_ok=True)


def test_inv056_unexpected_symlink_is_rejected(tmp_path):
    populate(tmp_path)

    outside = tmp_path.parent / (
        f"{tmp_path.name}-outside-extra"
    )
    outside.write_bytes(b"outside")

    link = tmp_path / "dist/undeclared-link"

    try:
        os.symlink(outside, link)

        with pytest.raises(
            CertifiedBundleError,
            match="symlink forbidden",
        ):
            build_certified_bundle_manifest(tmp_path)
    finally:
        if link.is_symlink():
            link.unlink()
        outside.unlink(missing_ok=True)


def test_inv056_noncanonical_manifest_path_fails_closed(tmp_path):
    populate(tmp_path)
    manifest = build_certified_bundle_manifest(tmp_path)

    alternate = tmp_path / "dist/alternate.json"
    alternate.write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )

    errors = verify_certified_bundle_manifest(
        tmp_path,
        alternate,
    )

    assert errors
    assert "manifest path mismatch" in errors[0]


def test_inv056_missing_manifest_fails_closed(tmp_path):
    populate(tmp_path)

    errors = verify_certified_bundle_manifest(
        tmp_path,
        tmp_path / BUNDLE_MANIFEST_PATH,
    )

    assert errors
    assert "manifest missing" in errors[0]


def test_inv056_self_exclusion_metadata_tamper_fails_closed(
    tmp_path,
):
    populate(tmp_path)
    manifest = build_certified_bundle_manifest(tmp_path)
    manifest["closure"]["self_excluded_path"] = "dist/other.json"
    path = write_manifest(tmp_path, manifest)

    errors = verify_certified_bundle_manifest(tmp_path, path)

    assert errors
    assert "self-exclusion mismatch" in errors[0]
