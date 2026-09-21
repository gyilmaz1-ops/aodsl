from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path, PurePosixPath


SCHEMA = "aodsl.certified-bundle.v1"
INVARIANT = "INV-056"
HASH_ALGORITHM = "sha256"

BUNDLE_MANIFEST_PATH = "dist/certified-bundle-manifest.json"
CLOSURE_POLICY = "exact-physical-payload-set"

REQUIRED_PAYLOAD_PATHS = (
    "dist/aodsl-1.0.0-production-source.zip",
    "dist/aodsl-1.0.0.cdx.json",
    "dist/release-manifest.json",
    "dist/reproducibility-manifest.json",
    "certification/production-certification-manifest.json",
    "certification/production-certification-attestation.json",
    "certification/evidence/live-certification-status.json",
)


class CertifiedBundleError(RuntimeError):
    """INV-056 fail-closed certified bundle verification error."""


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _validate_relative_path(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise CertifiedBundleError("invalid bundle path")

    if "\\" in value:
        raise CertifiedBundleError(
            f"non-canonical bundle path: {value!r}"
        )

    path = PurePosixPath(value)

    if path.is_absolute():
        raise CertifiedBundleError(
            f"absolute bundle path forbidden: {value!r}"
        )

    if value != path.as_posix():
        raise CertifiedBundleError(
            f"non-canonical bundle path: {value!r}"
        )

    if any(part in ("", ".", "..") for part in path.parts):
        raise CertifiedBundleError(
            f"path traversal or non-canonical path: {value!r}"
        )

    return value


def _root(root: Path) -> Path:
    root = Path(root)

    if root.is_symlink():
        raise CertifiedBundleError(
            "certified bundle root must not be a symlink"
        )

    root = root.resolve()

    if not root.is_dir():
        raise CertifiedBundleError(
            "certified bundle root missing or not a directory"
        )

    return root


def _resolve_payload(root: Path, relative_path: str) -> Path:
    relative_path = _validate_relative_path(relative_path)
    root = _root(root)

    candidate = root / relative_path

    if candidate.is_symlink():
        raise CertifiedBundleError(
            f"symlink forbidden in certified bundle: {relative_path}"
        )

    resolved = candidate.resolve()

    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise CertifiedBundleError(
            f"bundle path escapes bundle root: {relative_path!r}"
        ) from exc

    return resolved


def _enumerate_actual_payload_paths(root: Path) -> list[str]:
    root = _root(root)
    actual = []

    for candidate in sorted(
        root.rglob("*"),
        key=lambda path: path.relative_to(root).as_posix(),
    ):
        relative = candidate.relative_to(root).as_posix()
        _validate_relative_path(relative)

        if candidate.is_symlink():
            raise CertifiedBundleError(
                f"symlink forbidden in certified bundle: {relative}"
            )

        mode = candidate.lstat().st_mode

        if stat.S_ISDIR(mode):
            continue

        if not stat.S_ISREG(mode):
            raise CertifiedBundleError(
                "special filesystem node forbidden in certified bundle: "
                f"{relative}"
            )

        if relative == BUNDLE_MANIFEST_PATH:
            continue

        actual.append(relative)

    return actual


def _verify_physical_closure(root: Path) -> None:
    actual = _enumerate_actual_payload_paths(root)
    expected = list(REQUIRED_PAYLOAD_PATHS)

    if set(actual) != set(expected):
        missing = sorted(set(expected) - set(actual))
        unexpected = sorted(set(actual) - set(expected))
        raise CertifiedBundleError(
            "certified bundle physical closure mismatch: "
            f"missing={missing}, unexpected={unexpected}"
        )

    if actual != sorted(actual):
        raise CertifiedBundleError(
            "certified bundle physical enumeration is non-deterministic"
        )


def _entry(root: Path, relative_path: str) -> dict:
    path = _resolve_payload(root, relative_path)

    if not path.is_file():
        raise CertifiedBundleError(
            f"missing certified bundle payload: {relative_path}"
        )

    return {
        "path": relative_path,
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def build_certified_bundle_manifest(root: Path) -> dict:
    root = _root(root)

    _verify_physical_closure(root)

    entries = [
        _entry(root, relative_path)
        for relative_path in REQUIRED_PAYLOAD_PATHS
    ]

    return {
        "schema": SCHEMA,
        "invariant": INVARIANT,
        "hash_algorithm": HASH_ALGORITHM,
        "closure": {
            "policy": CLOSURE_POLICY,
            "payload_count": len(entries),
            "self_excluded_path": BUNDLE_MANIFEST_PATH,
        },
        "payload": entries,
    }


def verify_certified_bundle_manifest(
    root: Path,
    manifest_path: Path,
) -> list[str]:
    try:
        root = _root(root)
        manifest_path = Path(manifest_path).resolve()

        expected_manifest_path = (
            root / BUNDLE_MANIFEST_PATH
        ).resolve()

        if manifest_path != expected_manifest_path:
            raise CertifiedBundleError(
                "certified bundle manifest path mismatch"
            )

        if not manifest_path.is_file():
            raise CertifiedBundleError(
                "certified bundle manifest missing"
            )

        _verify_physical_closure(root)

        stored = json.loads(
            manifest_path.read_text(encoding="utf-8")
        )

        if not isinstance(stored, dict):
            raise CertifiedBundleError(
                "certified bundle manifest must be a JSON object"
            )

        if stored.get("schema") != SCHEMA:
            raise CertifiedBundleError(
                "certified bundle schema mismatch"
            )

        if stored.get("invariant") != INVARIANT:
            raise CertifiedBundleError(
                "certified bundle invariant mismatch"
            )

        if stored.get("hash_algorithm") != HASH_ALGORITHM:
            raise CertifiedBundleError(
                "certified bundle hash algorithm mismatch"
            )

        closure = stored.get("closure")
        if not isinstance(closure, dict):
            raise CertifiedBundleError(
                "certified bundle closure missing"
            )

        if closure.get("policy") != CLOSURE_POLICY:
            raise CertifiedBundleError(
                "certified bundle closure policy mismatch"
            )

        if (
            closure.get("self_excluded_path")
            != BUNDLE_MANIFEST_PATH
        ):
            raise CertifiedBundleError(
                "certified bundle self-exclusion mismatch"
            )

        payload = stored.get("payload")
        if not isinstance(payload, list):
            raise CertifiedBundleError(
                "certified bundle payload must be a list"
            )

        declared_paths = []

        for item in payload:
            if not isinstance(item, dict):
                raise CertifiedBundleError(
                    "invalid certified bundle payload entry"
                )

            path = _validate_relative_path(item.get("path"))
            declared_paths.append(path)

        if len(declared_paths) != len(set(declared_paths)):
            raise CertifiedBundleError(
                "duplicate certified bundle payload path"
            )

        expected_paths = list(REQUIRED_PAYLOAD_PATHS)

        if set(declared_paths) != set(expected_paths):
            missing = sorted(
                set(expected_paths) - set(declared_paths)
            )
            unexpected = sorted(
                set(declared_paths) - set(expected_paths)
            )
            raise CertifiedBundleError(
                "certified bundle payload closure mismatch: "
                f"missing={missing}, unexpected={unexpected}"
            )

        if declared_paths != expected_paths:
            raise CertifiedBundleError(
                "certified bundle payload order mismatch"
            )

        if closure.get("payload_count") != len(expected_paths):
            raise CertifiedBundleError(
                "certified bundle payload count mismatch"
            )

        expected = build_certified_bundle_manifest(root)

        for stored_entry, expected_entry in zip(
            payload,
            expected["payload"],
        ):
            if stored_entry.get("size") != expected_entry["size"]:
                raise CertifiedBundleError(
                    "certified bundle size mismatch: "
                    f"{expected_entry['path']}"
                )

            if (
                stored_entry.get("sha256")
                != expected_entry["sha256"]
            ):
                raise CertifiedBundleError(
                    "certified bundle SHA-256 mismatch: "
                    f"{expected_entry['path']}"
                )

        if stored != expected:
            raise CertifiedBundleError(
                "certified bundle manifest does not match "
                "canonical bundle state"
            )

        return []

    except Exception as exc:
        return [str(exc)]
