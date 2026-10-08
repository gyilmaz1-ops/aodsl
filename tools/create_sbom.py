#!/usr/bin/env python3

from pathlib import Path

from aodsl.certification.version_policy import (
    artifact_names,
    read_project_version,
)

from aodsl.certification.sbom import (
    LOCK_PATH,
    sha256_file,
    write_sbom,
)

ROOT = Path(__file__).resolve().parents[1]
VERSION = read_project_version(ROOT / "pyproject.toml")
_, SBOM_NAME = artifact_names(VERSION)
DIST = ROOT / "dist"
OUTPUT = DIST / SBOM_NAME

write_sbom(ROOT, OUTPUT, version=VERSION)

print("INV-053 DETERMINISTIC SBOM: CREATED")
print("SBOM:", OUTPUT.relative_to(ROOT))
print("SBOM SHA256:", sha256_file(OUTPUT))
print("Dependency lock:", LOCK_PATH)
print("Dependency lock SHA256:", sha256_file(ROOT / LOCK_PATH))
