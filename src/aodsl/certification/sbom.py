from __future__ import annotations

import hashlib
import json
import re
from importlib import metadata
from pathlib import Path

SBOM_SCHEMA = "aodsl.sbom.v1"
CYCLONEDX_FORMAT = "CycloneDX"
CYCLONEDX_SPEC_VERSION = "1.6"

LOCK_PATH = "requirements/production.lock"

LOCK_ENTRY = re.compile(
    r"^(?P<name>[A-Za-z0-9_.-]+)==(?P<version>[A-Za-z0-9_.+!-]+)"
    r"(?P<hashes>(?:\s+--hash=sha256:[0-9a-f]{64})+)$"
)

HASH_ENTRY = re.compile(
    r"--hash=sha256:(?P<digest>[0-9a-f]{64})"
)

def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def parse_production_lock(path: Path) -> list[dict]:
    path = Path(path)

    if not path.is_file():
        raise ValueError(f"production dependency lock missing: {path}")

    components: list[dict] = []
    seen: set[str] = set()

    for line_number, raw in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw.strip()

        if not line or line.startswith("#"):
            continue

        match = LOCK_ENTRY.fullmatch(line)

        if not match:
            raise ValueError(
                "invalid or unhashed production lock entry "
                f"at line {line_number}: {line!r}"
            )

        name = match.group("name").lower().replace("_", "-")
        version = match.group("version")

        hashes = sorted(
            {
                hash_match.group("digest")
                for hash_match in HASH_ENTRY.finditer(
                    match.group("hashes")
                )
            }
        )

        if not hashes:
            raise ValueError(
                f"production dependency has no SHA-256 hash: {name}"
            )

        if name in seen:
            raise ValueError(
                f"duplicate production dependency: {name}"
            )

        seen.add(name)

        components.append(
            {
                "name": name,
                "version": version,
                "purl": f"pkg:pypi/{name}@{version}",
                "hashes": hashes,
            }
        )

    components.sort(
        key=lambda item: (item["name"], item["version"])
    )

    return components

def verify_installed_dependencies(root: Path) -> list[str]:
    """Verify installed production dependency versions against the lock."""
    root = Path(root).resolve()

    try:
        components = parse_production_lock(root / LOCK_PATH)
    except Exception as exc:
        return [str(exc)]

    errors: list[str] = []

    for component in components:
        name = component["name"]
        expected = component["version"]

        try:
            actual = metadata.version(name)
        except metadata.PackageNotFoundError:
            errors.append(
                f"production dependency missing: "
                f"{name}=={expected}"
            )
            continue

        if actual != expected:
            errors.append(
                f"production dependency version mismatch: "
                f"{name}: expected {expected}, installed {actual}"
            )

    return errors


def build_sbom(root: Path) -> dict:
    root = Path(root).resolve()
    lock_path = root / LOCK_PATH

    components = parse_production_lock(lock_path)

    return {
        "bomFormat": CYCLONEDX_FORMAT,
        "specVersion": CYCLONEDX_SPEC_VERSION,
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "name": "aodsl",
                "version": "1.0.0",
            },
            "properties": [
                {
                    "name": "aodsl:schema",
                    "value": SBOM_SCHEMA,
                },
                {
                    "name": "aodsl:dependency-lock",
                    "value": LOCK_PATH,
                },
                {
                    "name": "aodsl:dependency-lock-sha256",
                    "value": sha256_file(lock_path),
                },
            ],
        },
        "components": [
            {
                "type": "library",
                "name": item["name"],
                "version": item["version"],
                "purl": item["purl"],
                "hashes": [
                    {
                        "alg": "SHA-256",
                        "content": digest,
                    }
                    for digest in item["hashes"]
                ],
            }
            for item in components
        ],
    }


def write_sbom(root: Path, output_path: Path) -> dict:
    sbom = build_sbom(root)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    output_path.write_bytes(canonical_json_bytes(sbom) + b"\n")

    return sbom


def verify_sbom(
    root: Path,
    sbom_path: Path,
) -> list[str]:
    try:
        stored = json.loads(Path(sbom_path).read_text(encoding="utf-8"))
        expected = build_sbom(root)
    except Exception as exc:
        return [str(exc)]

    errors: list[str] = []

    if stored.get("bomFormat") != CYCLONEDX_FORMAT:
        errors.append("SBOM format mismatch")

    if stored.get("specVersion") != CYCLONEDX_SPEC_VERSION:
        errors.append("SBOM specification version mismatch")

    if stored != expected:
        errors.append("SBOM does not match production dependency lock")

    return errors
