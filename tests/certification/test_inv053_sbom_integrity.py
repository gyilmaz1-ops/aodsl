from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from aodsl.certification.release_identity import (
    SCHEMA as RELEASE_IDENTITY_SCHEMA,
    build_release_identity,
    verify_release_identity,
)
from aodsl.certification.sbom import (
    build_sbom,
    parse_production_lock,
    verify_sbom,
    write_sbom,
)


PSYCOPG_HASH = "a" * 64
PSYCOPG_BINARY_LINUX_HASH = "b" * 64
PSYCOPG_BINARY_MACOS_HASH = "c" * 64
TYPING_EXTENSIONS_HASH = "d" * 64


def hashed_lock() -> str:
    return (
        "psycopg==3.3.6 "
        f"--hash=sha256:{PSYCOPG_HASH}\n"
        "psycopg-binary==3.3.6 "
        f"--hash=sha256:{PSYCOPG_BINARY_LINUX_HASH} "
        f"--hash=sha256:{PSYCOPG_BINARY_MACOS_HASH}\n"
        "typing-extensions==4.16.0 "
        f"--hash=sha256:{TYPING_EXTENSIONS_HASH}\n"
    )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_root(tmp_path: Path) -> Path:
    root = tmp_path

    (root / "requirements").mkdir()
    (root / "dist").mkdir()
    (root / "certification").mkdir()
    (root / ".github/workflows").mkdir(parents=True)

    (root / "requirements/production.lock").write_text(
        hashed_lock(),
        encoding="utf-8",
    )

    (root / ".github/workflows/production-release.yml").write_text(
        "name: test\n",
        encoding="utf-8",
    )

    (
        root
        / "certification/production-certification-manifest.json"
    ).write_text(
        json.dumps(
            {
                "source": {
                    "canonical_tree_sha256": "a" * 64,
                }
            }
        ),
        encoding="utf-8",
    )

    (
        root / "dist/aodsl-1.0.0-production-source.zip"
    ).write_bytes(
        b"certified-production-artifact"
    )

    return root


def test_lock_requires_sha256_for_every_dependency(
    tmp_path: Path,
) -> None:
    lock = tmp_path / "production.lock"
    lock.write_text(
        "psycopg==3.3.6\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="invalid or unhashed production lock entry",
    ):
        parse_production_lock(lock)


def test_lock_rejects_malformed_sha256(
    tmp_path: Path,
) -> None:
    lock = tmp_path / "production.lock"
    lock.write_text(
        "psycopg==3.3.6 --hash=sha256:not-a-valid-digest\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="invalid or unhashed production lock entry",
    ):
        parse_production_lock(lock)


def test_lock_parser_canonicalizes_hash_order(
    tmp_path: Path,
) -> None:
    lock = tmp_path / "production.lock"
    lock.write_text(
        "psycopg-binary==3.3.6 "
        f"--hash=sha256:{PSYCOPG_BINARY_MACOS_HASH} "
        f"--hash=sha256:{PSYCOPG_BINARY_LINUX_HASH}\n",
        encoding="utf-8",
    )

    components = parse_production_lock(lock)

    assert components[0]["hashes"] == sorted(
        [
            PSYCOPG_BINARY_MACOS_HASH,
            PSYCOPG_BINARY_LINUX_HASH,
        ]
    )


def test_sbom_contains_approved_artifact_hashes(
    tmp_path: Path,
) -> None:
    root = make_root(tmp_path)
    sbom = build_sbom(root)

    components = {
        item["name"]: item
        for item in sbom["components"]
    }

    assert components["psycopg"]["hashes"] == [
        {
            "alg": "SHA-256",
            "content": PSYCOPG_HASH,
        }
    ]

    assert components["psycopg-binary"]["hashes"] == [
        {
            "alg": "SHA-256",
            "content": digest,
        }
        for digest in sorted(
            [
                PSYCOPG_BINARY_LINUX_HASH,
                PSYCOPG_BINARY_MACOS_HASH,
            ]
        )
    ]

    assert components["typing-extensions"]["hashes"] == [
        {
            "alg": "SHA-256",
            "content": TYPING_EXTENSIONS_HASH,
        }
    ]


def test_sbom_is_deterministic(tmp_path: Path) -> None:
    root = make_root(tmp_path)

    first = build_sbom(root)
    second = build_sbom(root)

    assert first == second

    out1 = root / "dist/first.cdx.json"
    out2 = root / "dist/second.cdx.json"

    write_sbom(root, out1)
    write_sbom(root, out2)

    assert out1.read_bytes() == out2.read_bytes()
    assert sha256(out1) == sha256(out2)


def test_sbom_fails_closed_after_lock_change(
    tmp_path: Path,
) -> None:
    root = make_root(tmp_path)
    sbom = root / "dist/aodsl-1.0.0.cdx.json"

    write_sbom(root, sbom)

    assert verify_sbom(root, sbom) == []

    changed = hashed_lock().replace(
        PSYCOPG_HASH,
        "e" * 64,
        1,
    )

    (root / "requirements/production.lock").write_text(
        changed,
        encoding="utf-8",
    )

    errors = verify_sbom(root, sbom)

    assert errors
    assert (
        "SBOM does not match production dependency lock"
        in errors
    )


def test_sbom_fails_closed_after_sbom_tamper(
    tmp_path: Path,
) -> None:
    root = make_root(tmp_path)
    sbom = root / "dist/aodsl-1.0.0.cdx.json"

    write_sbom(root, sbom)

    data = json.loads(
        sbom.read_text(encoding="utf-8")
    )
    data["components"][0]["version"] = "999.0.0"

    sbom.write_text(
        json.dumps(data),
        encoding="utf-8",
    )

    errors = verify_sbom(root, sbom)

    assert errors
    assert (
        "SBOM does not match production dependency lock"
        in errors
    )


def test_release_identity_binds_sbom_and_lock(
    tmp_path: Path,
) -> None:
    root = make_root(tmp_path)

    artifact = (
        root
        / "dist/aodsl-1.0.0-production-source.zip"
    )
    cert = (
        root
        / "certification/"
        "production-certification-manifest.json"
    )
    sbom = root / "dist/aodsl-1.0.0.cdx.json"

    write_sbom(root, sbom)

    identity = build_release_identity(
        root,
        artifact,
        cert,
        sbom,
        git_commit_sha="b" * 40,
        git_tag="v1.0.4",
        repository="gyilmaz1-ops/aodsl",
        workflow_ref=(
            "gyilmaz1-ops/aodsl/"
            ".github/workflows/production-release.yml"
            "@refs/tags/v1.0.4"
        ),
    )

    assert (
        RELEASE_IDENTITY_SCHEMA
        == "aodsl.certified-release-identity.v2"
    )

    assert identity["sbom"]["sha256"] == sha256(sbom)

    assert (
        identity["sbom"]["dependency_lock_sha256"]
        == sha256(root / "requirements/production.lock")
    )

    identity_path = (
        root / "dist/certified-release-identity.json"
    )

    identity_path.write_text(
        json.dumps(
            identity,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    assert verify_release_identity(
        root,
        identity_path,
        artifact,
        cert,
        sbom,
        git_commit_sha="b" * 40,
        git_tag="v1.0.4",
        repository="gyilmaz1-ops/aodsl",
        workflow_ref=(
            "gyilmaz1-ops/aodsl/"
            ".github/workflows/production-release.yml"
            "@refs/tags/v1.0.4"
        ),
    ) == []

    sbom.write_bytes(
        sbom.read_bytes() + b" "
    )

    errors = verify_release_identity(
        root,
        identity_path,
        artifact,
        cert,
        sbom,
        git_commit_sha="b" * 40,
        git_tag="v1.0.4",
        repository="gyilmaz1-ops/aodsl",
        workflow_ref=(
            "gyilmaz1-ops/aodsl/"
            ".github/workflows/production-release.yml"
            "@refs/tags/v1.0.4"
        ),
    )

    assert errors
    assert (
        "release identity does not match "
        "certification/git/workflow/artifact"
        in errors
    )


def test_production_workflow_enforces_dependency_and_sbom_contract() -> None:
    root = Path(__file__).resolve().parents[2]
    workflow_path = (
        root / ".github/workflows/production-release.yml"
    )
    workflow = workflow_path.read_text(encoding="utf-8")

    # Production dependencies must come only from the
    # cryptographically pinned lock.
    assert "--require-hashes" in workflow
    assert "--only-binary=:all:" in workflow
    assert "-r requirements/production.lock" in workflow

    # Production dependency resolution must not fall back to
    # the range-based postgres optional dependency.
    assert '.[postgres,test]' not in workflow
    assert '.[postgres]' not in workflow

    # Test tooling remains a separate non-production install path.
    assert 'python -m pip install -e ".[test]"' in workflow

    dependency_verify = (
        "python tools/verify_production_dependencies.py"
    )
    sbom_create = "python tools/create_sbom.py"
    sbom_verify = "python tools/verify_sbom.py"
    identity_create = "python tools/create_release_identity.py"
    identity_verify = "python tools/verify_release_identity.py"
    sbom_subject = "subject-path: 'dist/aodsl-*.cdx.json'"
    upload = "uses: actions/upload-artifact@v4"

    for required in (
        dependency_verify,
        sbom_create,
        sbom_verify,
        identity_create,
        identity_verify,
        sbom_subject,
        upload,
    ):
        assert required in workflow

    # Enforce the release trust-chain ordering.
    assert workflow.index(dependency_verify) < workflow.index(sbom_create)
    assert workflow.index(sbom_create) < workflow.index(sbom_verify)
    assert workflow.index(sbom_verify) < workflow.index(identity_create)
    assert workflow.index(identity_create) < workflow.index(identity_verify)
    assert workflow.index(identity_verify) < workflow.index(sbom_subject)
    assert workflow.index(sbom_subject) < workflow.index(upload)
