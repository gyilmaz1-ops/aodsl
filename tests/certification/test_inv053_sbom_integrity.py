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
    (root / "architecture").mkdir()
    (root / "dist").mkdir()
    (root / "certification").mkdir()
    (root / ".github/workflows").mkdir(parents=True)

    (root / "requirements/production.lock").write_text(
        hashed_lock(),
        encoding="utf-8",
    )

    build_lock = root / "requirements/build.lock"
    build_lock.write_text(
        "setuptools==84.0.0 "
        "--hash=sha256:"
        + "e" * 64
        + "\n",
        encoding="utf-8",
    )

    build_lock_sha = sha256(build_lock)

    (
        root / "architecture/build-environment.v1.json"
    ).write_text(
        json.dumps(
            {
                "schema": "aodsl.build-environment.v1",
                "invariant": "INV-054",
                "platform": {
                    "os": "linux",
                    "architecture": "amd64",
                },
                "oci": {
                    "image": "python",
                    "tag": "3.12.11-slim",
                    "digest": "sha256:47ae396f09c1303b8653019811a8498470603d7ffefc29cb07c88f1f8cb3d19f",
                    "reference": "python@sha256:47ae396f09c1303b8653019811a8498470603d7ffefc29cb07c88f1f8cb3d19f",
                },
                "python": {
                    "implementation": "CPython",
                    "version": "3.12.11",
                },
                "bootstrap": {
                    "pip_version": "25.0.1",
                },
                "build_toolchain": {
                    "lock_path": "requirements/build.lock",
                    "lock_sha256": build_lock_sha,
                    "installation_policy": {
                        "require_hashes": True,
                        "only_binary": True,
                    },
                },
            }
        ),
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

    reproducibility = root / "dist/reproducibility-manifest.json"
    reproducibility.write_text(
        json.dumps(
            {
                "schema": "aodsl.reproducible-release-artifact.v1",
                "invariant": "INV-055",
                "comparison": {
                    "algorithm": "sha256-and-byte-equality",
                    "independent_builds": 2,
                    "result": "IDENTICAL",
                },
                "artifact": {
                    "path": "dist/aodsl-1.0.0-production-source.zip",
                    "sha256": sha256(artifact),
                },
                "sbom": {
                    "path": "dist/aodsl-1.0.0.cdx.json",
                    "sha256": sha256(sbom),
                },
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    identity = build_release_identity(
        root,
        artifact,
        cert,
        sbom,
        reproducibility,
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
        == "aodsl.certified-release-identity.v4"
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
        reproducibility,
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
        reproducibility,
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
        "reproducibility SBOM SHA-256 mismatch"
        in errors
    )


def test_production_workflow_enforces_dependency_and_sbom_contract() -> None:
    root = Path(__file__).resolve().parents[2]
    workflow_path = (
        root / ".github/workflows/production-release.yml"
    )
    workflow = workflow_path.read_text(encoding="utf-8")

    build_marker = "  reproducible-build:\n"
    final_marker = "  production-gate:\n"

    assert build_marker in workflow
    assert final_marker in workflow

    build_start = workflow.index(build_marker)
    final_start = workflow.index(final_marker)

    assert build_start < final_start

    build = workflow[build_start:final_start]
    final = workflow[final_start:]

    # Both certified execution phases use the cryptographically
    # pinned dependency contracts.
    for block in (build, final):
        assert "--require-hashes" in block
        assert "--only-binary=:all:" in block
        assert "-r requirements/build.lock" in block
        assert "-r requirements/production.lock" in block

        assert '.[postgres,test]' not in block
        assert '.[postgres]' not in block

        assert (
            'python -m pip install -e ".[test]"'
            not in block
        )
        assert (
            "python -m pip install "
            "--no-deps --no-build-isolation -e ."
            in block
        )

        assert (
            block.index("-r requirements/build.lock")
            < block.index("-r requirements/production.lock")
        )

    dependency_verify = (
        "python tools/verify_production_dependencies.py"
    )
    sbom_create = "python tools/create_sbom.py"
    sbom_verify = "python tools/verify_sbom.py"
    candidate_upload = "name: aodsl-repro-${{ matrix.build }}"

    for required in (
        dependency_verify,
        sbom_create,
        sbom_verify,
        candidate_upload,
    ):
        assert required in build

    # Candidate bytes must be generated and verified before
    # they cross the artifact boundary.
    assert (
        build.index(dependency_verify)
        < build.index(sbom_create)
        < build.index(sbom_verify)
        < build.index(candidate_upload)
    )

    compare = "python tools/verify_reproducible_artifacts.py"
    promoted_sbom_verify = "python tools/verify_sbom.py"
    identity_create = "python tools/create_release_identity.py"
    identity_verify = "python tools/verify_release_identity.py"
    sbom_subject = "subject-path: 'dist/aodsl-*.cdx.json'"
    certified_upload = "name: aodsl-certified-production"

    for required in (
        dependency_verify,
        compare,
        promoted_sbom_verify,
        identity_create,
        identity_verify,
        sbom_subject,
        certified_upload,
    ):
        assert required in final

    # The final gate must consume the already-built candidate.
    # It may verify the promoted SBOM, but must not regenerate it.
    assert "python tools/create_sbom.py" not in final
    assert "python tools/release_gate.py --production" not in final

    # Final trust chain:
    # compare -> verify promoted SBOM -> bind identity ->
    # verify identity -> attest SBOM -> certified upload.
    assert (
        final.index(compare)
        < final.index(promoted_sbom_verify)
        < final.index(identity_create)
        < final.index(identity_verify)
        < final.index(sbom_subject)
        < final.index(certified_upload)
    )
