#!/usr/bin/env python3
"""Check archive/canonical consistency without certifying a release."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from aodsl.certification.evidence_promotion import normalize_live_evidence
from aodsl.certification.version_policy import read_project_version


def read_object(path: Path) -> dict:
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    data = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=unique_object,
    )
    if not isinstance(data, dict):
        raise ValueError("evidence must be a JSON object")
    return data


def verify_pair(root: Path) -> list[str]:
    folder = root / "certification/evidence"
    try:
        raw = read_object(folder / "live-certification-status.raw.json")
        canonical = read_object(folder / "live-certification-status.json")
        version = read_project_version(root / "pyproject.toml")
        for label, evidence in (("raw", raw), ("canonical", canonical)):
            value = evidence.get("tested_source_tree_sha256")
            if not isinstance(value, str) or not re.fullmatch(
                r"[0-9a-f]{64}", value
            ):
                raise ValueError(f"{label}: invalid tested source SHA-256")
        normalized = normalize_live_evidence(raw, version)
        if normalized != canonical:
            keys = sorted(set(normalized) | set(canonical))
            differences = [
                key for key in keys
                if key not in normalized or key not in canonical
                or normalized[key] != canonical[key]
            ]
            return [
                "EVIDENCE-PAIR-001: normalized raw differs from canonical: "
                + ", ".join(differences)
            ]
        return []
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return [f"EVIDENCE-PAIR-001: {exc}"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    args = parser.parse_args()
    errors = verify_pair(args.root.resolve())
    print(json.dumps({
        "check": "EVIDENCE-PAIR-001",
        "mode": "read-only",
        "evidence_pair": "FAIL" if errors else "PASS",
        "release_certified": False,
        "errors": errors,
    }, indent=2, sort_keys=True))
    return 2 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
