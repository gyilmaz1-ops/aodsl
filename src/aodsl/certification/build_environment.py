from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any


class BuildEnvironmentError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BuildEnvironmentError(
            f"cannot load build environment manifest: {exc}"
        ) from exc

    if data.get("schema") != "aodsl.build-environment.v1":
        raise BuildEnvironmentError("invalid build environment schema")
    if data.get("invariant") != "INV-054":
        raise BuildEnvironmentError("invalid build environment invariant")

    return data


def pip_version() -> str:
    proc = subprocess.run(
        [sys.executable, "-m", "pip", "--version"],
        check=True,
        capture_output=True,
        text=True,
    )
    parts = proc.stdout.strip().split()
    if len(parts) < 2 or parts[0] != "pip":
        raise BuildEnvironmentError("cannot determine pip version")
    return parts[1]


def normalized_architecture(machine: str) -> str:
    value = machine.lower()
    aliases = {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }
    return aliases.get(value, value)


def verify_build_environment(
    workspace: Path,
    *,
    manifest_path: Path | None = None,
) -> dict[str, str]:
    workspace = workspace.resolve()
    manifest_path = (
        manifest_path.resolve()
        if manifest_path is not None
        else workspace / "architecture" / "build-environment.v1.json"
    )

    manifest = load_manifest(manifest_path)

    lock_rel = manifest["build_toolchain"]["lock_path"]
    lock_path = workspace / lock_rel
    if not lock_path.is_file():
        raise BuildEnvironmentError(f"missing build lock: {lock_rel}")

    actual_lock_sha = sha256_file(lock_path)
    expected_lock_sha = manifest["build_toolchain"]["lock_sha256"]
    if actual_lock_sha != expected_lock_sha:
        raise BuildEnvironmentError(
            f"build lock SHA256 mismatch: "
            f"expected {expected_lock_sha}, got {actual_lock_sha}"
        )

    expected_impl = manifest["python"]["implementation"]
    actual_impl = platform.python_implementation()
    if actual_impl != expected_impl:
        raise BuildEnvironmentError(
            f"Python implementation mismatch: "
            f"expected {expected_impl}, got {actual_impl}"
        )

    expected_python = manifest["python"]["version"]
    actual_python = platform.python_version()
    if actual_python != expected_python:
        raise BuildEnvironmentError(
            f"Python version mismatch: "
            f"expected {expected_python}, got {actual_python}"
        )

    expected_os = manifest["platform"]["os"]
    actual_os = platform.system().lower()
    if actual_os != expected_os:
        raise BuildEnvironmentError(
            f"OS mismatch: expected {expected_os}, got {actual_os}"
        )

    expected_arch = manifest["platform"]["architecture"]
    actual_arch = normalized_architecture(platform.machine())
    if actual_arch != expected_arch:
        raise BuildEnvironmentError(
            f"architecture mismatch: expected {expected_arch}, got {actual_arch}"
        )

    expected_pip = manifest["bootstrap"]["pip_version"]
    actual_pip = pip_version()
    if actual_pip != expected_pip:
        raise BuildEnvironmentError(
            f"pip version mismatch: expected {expected_pip}, got {actual_pip}"
        )

    policy = manifest["build_toolchain"]["installation_policy"]
    if policy.get("require_hashes") is not True:
        raise BuildEnvironmentError("require_hashes policy must be true")
    if policy.get("only_binary") is not True:
        raise BuildEnvironmentError("only_binary policy must be true")

    oci = manifest["oci"]
    digest = oci["digest"]
    reference = oci["reference"]

    if not digest.startswith("sha256:") or len(digest) != 71:
        raise BuildEnvironmentError("invalid OCI SHA256 digest")
    if reference != f'{oci["image"]}@{digest}':
        raise BuildEnvironmentError("OCI reference/digest mismatch")

    return {
        "python": actual_python,
        "python_implementation": actual_impl,
        "pip": actual_pip,
        "os": actual_os,
        "architecture": actual_arch,
        "build_lock_sha256": actual_lock_sha,
        "oci_reference": reference,
    }
