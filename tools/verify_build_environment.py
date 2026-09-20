#!/usr/bin/env python3
from pathlib import Path

from aodsl.certification.build_environment import (
    BuildEnvironmentError,
    verify_build_environment,
)


def main() -> int:
    workspace = Path(__file__).resolve().parents[1]

    try:
        result = verify_build_environment(workspace)
    except (BuildEnvironmentError, KeyError, TypeError) as exc:
        print(f"INV-054 BUILD ENVIRONMENT: FAILED: {exc}")
        return 1

    print("INV-054 BUILD ENVIRONMENT: PASSED")
    print(f"Python: {result['python_implementation']} {result['python']}")
    print(f"pip: {result['pip']}")
    print(f"Platform: {result['os']}/{result['architecture']}")
    print(f"Build lock SHA256: {result['build_lock_sha256']}")
    print(f"OCI reference: {result['oci_reference']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
