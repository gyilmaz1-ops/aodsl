from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from aodsl.certification.source_binding import canonical_source_tree_sha256

SCHEMA = "aodsl.live-certification.v1"
VERSION = "1.0.0"


def normalize_live_evidence(raw: dict) -> dict:
    try:
        architecture = raw["architecture"]["status"]
        operations = raw["operations"]["status"]
        postgres = raw["postgres"]
        production = raw["production_status"]
        tested_sha = raw["tested_source_tree_sha256"]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"INV-051 malformed raw certification evidence: {exc}") from exc

    required = {
        "architecture": (architecture, "PASSED"),
        "operations": (operations, "CERTIFIED"),
        "postgres": (postgres.get("status") if isinstance(postgres, dict) else None, "CERTIFIED"),
        "production_status": (production, "CERTIFIED"),
    }
    failures = [f"{k}={actual!r}" for k, (actual, expected) in required.items() if actual != expected]
    if failures:
        raise ValueError("INV-051 certification is not promotable: " + ", ".join(failures))
    if not isinstance(postgres, dict) or postgres.get("id") != "CERT-PG-001":
        raise ValueError("INV-051 PostgreSQL evidence identity invalid")
    if not isinstance(tested_sha, str) or len(tested_sha) != 64:
        raise ValueError("INV-051 tested source SHA-256 invalid")

    return {
        "schema": SCHEMA,
        "version": VERSION,
        "architecture": architecture,
        "operations": operations,
        "cert_pg_001": postgres["status"],
        "production_deployment": production,
        "postgres": postgres,
        "tested_source_tree_sha256": tested_sha,
    }


def _atomic_json_write(path: Path, value: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value, indent=2, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def promote_live_evidence(root: Path, raw_path: Path, canonical_path: Path, raw_archive_path: Path) -> dict:
    root = Path(root)
    raw = json.loads(Path(raw_path).read_text())
    canonical = normalize_live_evidence(raw)
    current_sha = canonical_source_tree_sha256(root)
    if canonical["tested_source_tree_sha256"] != current_sha:
        raise ValueError(
            "INV-051 source binding mismatch before promotion: "
            f"tested={canonical['tested_source_tree_sha256']!r}, current={current_sha!r}"
        )

    # Archive first; canonical evidence is the release input and is the final
    # commit point. A pre-commit failure therefore leaves canonical evidence unchanged.
    _atomic_json_write(Path(raw_archive_path), raw)
    _atomic_json_write(Path(canonical_path), canonical)
    return canonical
