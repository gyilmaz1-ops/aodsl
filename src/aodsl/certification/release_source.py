"""Bind production release inputs to the archived Git commit."""

from pathlib import Path
import subprocess
import tempfile

from aodsl.certification.attestation import (
    INCLUDED_ROOTS,
    INCLUDED_TOP_LEVEL,
    source_tree_entries,
)

CERTIFICATION_FILES = (
    "certification/evidence/live-certification-status.json",
    "certification/production-certification-manifest.json",
    "certification/production-certification-attestation.json",
)


def verify_production_source_alignment(root: Path) -> None:
    root = Path(root).resolve()
    env = {
        key: value for key, value in __import__("os").environ.items()
        if not key.startswith("GIT_")
    }

    def git(*args):
        return subprocess.check_output(
            ["git", *args], cwd=root, env=env,
            stderr=subprocess.PIPE, timeout=30,
        )

    head = git("rev-parse", "--verify", "HEAD").decode().strip()
    records = git("ls-tree", "-r", "-z", head)
    blobs = {}

    with tempfile.TemporaryDirectory(prefix="aodsl-release-source-") as directory:
        committed = Path(directory)
        for record in records.split(b"\0"):
            if not record:
                continue
            metadata, raw_path = record.split(b"\t", 1)
            mode, kind, oid = metadata.decode("ascii").split()
            name = raw_path.decode("utf-8")
            parts = name.split("/")
            if (
                name.startswith("/") or chr(92) in name
                or any(part in ("", ".", "..") for part in parts)
            ):
                raise ValueError("PRODUCTION-SOURCE-001: unsafe Git path")
            included = name in INCLUDED_TOP_LEVEL or any(
                name.startswith(prefix + "/") for prefix in INCLUDED_ROOTS
            )
            certification = name in CERTIFICATION_FILES
            if not included and not certification:
                continue
            if kind != "blob" or mode not in ("100644", "100755"):
                raise ValueError(
                    "PRODUCTION-SOURCE-001: unsupported release input"
                )
            data = git("cat-file", "blob", oid)
            blobs[name] = data
            if included:
                destination = committed / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)

        if source_tree_entries(root) != source_tree_entries(committed):
            raise ValueError(
                "PRODUCTION-SOURCE-001: working source differs from HEAD"
            )

    for name in CERTIFICATION_FILES:
        path = root / name
        if (
            name not in blobs or not path.is_file()
            or path.is_symlink() or path.read_bytes() != blobs[name]
        ):
            raise ValueError(
                "PRODUCTION-SOURCE-002: certification differs from HEAD: "
                + name
            )

    if git("rev-parse", "--verify", "HEAD").decode().strip() != head:
        raise ValueError("PRODUCTION-SOURCE-001: HEAD changed during verification")
