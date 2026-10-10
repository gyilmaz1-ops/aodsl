import hashlib
import json
from pathlib import Path

import pytest

from tools.verify_reproducible_artifacts import (
    ReproducibilityError,
    validate_release_manifest,
)


ARTIFACT = "aodsl-1.0.0-production-source.zip"
ARTIFACT_SHA = "a" * 64
SOURCE_SHA = "b" * 64


def write_json(path, payload):
    path.write_text(
        json.dumps(payload, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def fixture(tmp_path):
    certification_path = tmp_path / "production-certification-manifest.json"

    write_json(
        certification_path,
        {
            "source": {
                "canonical_tree_sha256": SOURCE_SHA,
            },
        },
    )

    certification_sha = hashlib.sha256(
        certification_path.read_bytes()
    ).hexdigest()

    release_path = tmp_path / "release-manifest.json"

    release = {
        "version": "1.0.0",
        "mode": "production",
        "artifact": ARTIFACT,
        "sha256": ARTIFACT_SHA,
        "files": 100,
        "production_certification_manifest_sha256": certification_sha,
        "certified_source_tree_sha256": SOURCE_SHA,
    }

    write_json(release_path, release)

    return certification_path, release_path, release


def validate(certification_path, release_path):
    validate_release_manifest(
        release_path,
        version="1.0.0",
        artifact_name=ARTIFACT,
        artifact_sha=ARTIFACT_SHA,
        certification_manifest_path=certification_path,
    )


def test_valid_certification_binding_passes(tmp_path):
    cert, release, _ = fixture(tmp_path)
    validate(cert, release)


def test_wrong_certification_manifest_sha_rejected(tmp_path):
    cert, release, payload = fixture(tmp_path)
    payload["production_certification_manifest_sha256"] = "c" * 64
    write_json(release, payload)

    with pytest.raises(ReproducibilityError):
        validate(cert, release)


def test_wrong_certified_source_sha_rejected(tmp_path):
    cert, release, payload = fixture(tmp_path)
    payload["certified_source_tree_sha256"] = "d" * 64
    write_json(release, payload)

    with pytest.raises(ReproducibilityError):
        validate(cert, release)


def test_missing_certification_manifest_rejected(tmp_path):
    cert, release, _ = fixture(tmp_path)
    cert.unlink()

    with pytest.raises(ReproducibilityError):
        validate(cert, release)


def test_malformed_certification_manifest_rejected(tmp_path):
    cert, release, _ = fixture(tmp_path)
    cert.write_text("{broken", encoding="utf-8")

    with pytest.raises(ReproducibilityError):
        validate(cert, release)


def test_invalid_certification_source_sha_rejected(tmp_path):
    cert, release, payload = fixture(tmp_path)

    write_json(
        cert,
        {
            "source": {
                "canonical_tree_sha256": "invalid",
            },
        },
    )

    payload["production_certification_manifest_sha256"] = (
        hashlib.sha256(cert.read_bytes()).hexdigest()
    )
    write_json(release, payload)

    with pytest.raises(ReproducibilityError):
        validate(cert, release)
