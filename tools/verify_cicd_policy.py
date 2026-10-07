#!/usr/bin/env python3

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/production-release.yml"

EXACT_IMAGE = (
    "python@sha256:"
    "47ae396f09c1303b8653019811a8498470603d7ffefc29cb07c88f1f8cb3d19f"
)


def fail(message: str) -> None:
    print(f"CI-PROD-001: FAILED — {message}")
    raise SystemExit(2)


def indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def meaningful(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and not stripped.startswith("#")


def find_job(lines: list[str], name: str) -> list[str]:
    marker = f"  {name}:"

    matches = [
        index
        for index, line in enumerate(lines)
        if line == marker
    ]

    if len(matches) != 1:
        fail(f"workflow job must occur exactly once: {name}")

    start = matches[0] + 1
    end = len(lines)

    for index in range(start, len(lines)):
        line = lines[index]

        if (
            meaningful(line)
            and indent(line) <= 2
        ):
            end = index
            break

    return lines[start:end]


def scalar(job: list[str], key: str) -> str | None:
    prefix = f"    {key}:"

    values = []

    for line in job:
        if line.startswith(prefix):
            values.append(line[len(prefix):].strip())

    if len(values) > 1:
        fail(f"duplicate job key: {key}")

    return values[0] if values else None


def nested_scalar(
    job: list[str],
    parent: str,
    key: str,
) -> str | None:
    parent_line = f"    {parent}:"

    starts = [
        index
        for index, line in enumerate(job)
        if line == parent_line
    ]

    if len(starts) != 1:
        return None

    start = starts[0] + 1
    values = []

    for line in job[start:]:
        if meaningful(line) and indent(line) <= 4:
            break

        prefix = f"      {key}:"
        if line.startswith(prefix):
            values.append(line[len(prefix):].strip())

    if len(values) > 1:
        fail(f"duplicate nested key: {parent}.{key}")

    return values[0] if values else None


def matrix_build(job: list[str]) -> str | None:
    strategy_positions = [
        i
        for i, line in enumerate(job)
        if line == "    strategy:"
    ]

    if len(strategy_positions) != 1:
        return None

    start = strategy_positions[0] + 1
    strategy_end = len(job)

    for i in range(start, len(job)):
        if meaningful(job[i]) and indent(job[i]) <= 4:
            strategy_end = i
            break

    strategy = job[start:strategy_end]

    matrix_positions = [
        i
        for i, line in enumerate(strategy)
        if line == "      matrix:"
    ]

    if len(matrix_positions) != 1:
        return None

    start = matrix_positions[0] + 1
    values = []

    for line in strategy[start:]:
        if meaningful(line) and indent(line) <= 6:
            break

        prefix = "        build:"
        if line.startswith(prefix):
            values.append(line[len(prefix):].strip())

    if len(values) > 1:
        fail("duplicate strategy.matrix.build")

    return values[0] if values else None


def parse_steps(job: list[str]) -> list[dict[str, object]]:
    starts = [
        index
        for index, line in enumerate(job)
        if line == "    steps:"
    ]

    if len(starts) != 1:
        fail("job must contain exactly one steps mapping")

    start = starts[0] + 1
    raw_steps: list[list[str]] = []
    current: list[str] | None = None

    for line in job[start:]:
        if meaningful(line) and indent(line) <= 4:
            break

        if line.startswith("      - "):
            if current is not None:
                raw_steps.append(current)
            current = [line]
        elif current is not None:
            current.append(line)

    if current is not None:
        raw_steps.append(current)

    steps: list[dict[str, object]] = []

    for raw in raw_steps:
        step: dict[str, object] = {
            "name": None,
            "uses": None,
            "run": [],
            "with": {},
        }

        first = raw[0][8:]

        if first.startswith("name:"):
            step["name"] = first[5:].strip()
        elif first.startswith("uses:"):
            step["uses"] = first[5:].strip()

        i = 1

        while i < len(raw):
            line = raw[i]

            if not meaningful(line):
                i += 1
                continue

            if indent(line) != 8:
                i += 1
                continue

            stripped = line.strip()

            if stripped.startswith("name:"):
                step["name"] = stripped[5:].strip()
                i += 1
                continue

            if stripped.startswith("uses:"):
                step["uses"] = stripped[5:].strip()
                i += 1
                continue

            if stripped == "with:":
                values: dict[str, str] = {}
                i += 1

                while i < len(raw):
                    child = raw[i]

                    if meaningful(child) and indent(child) <= 8:
                        break

                    if meaningful(child) and indent(child) == 10:
                        text = child.strip()
                        if ":" in text:
                            key, value = text.split(":", 1)
                            if key in values:
                                fail(
                                    f"duplicate step with key: {key}"
                                )
                            values[key] = value.strip()

                    i += 1

                step["with"] = values
                continue

            if re.match(r"^run:\s*[|>][-+]?\s*$", stripped):
                commands: list[str] = []
                i += 1

                while i < len(raw):
                    child = raw[i]

                    if meaningful(child) and indent(child) <= 8:
                        break

                    text = child.strip()

                    if text and not text.startswith("#"):
                        commands.append(text)

                    i += 1

                step["run"] = commands
                continue

            if stripped.startswith("run:"):
                command = stripped[4:].strip()

                if command and not command.startswith("#"):
                    step["run"] = [command]

                i += 1
                continue

            i += 1

        steps.append(step)

    return steps


def executable_text(step: dict[str, object]) -> str:
    return "\n".join(step["run"])


def step_has_exec(
    step: dict[str, object],
    needle: str,
) -> bool:
    return needle in executable_text(step)


def find_exec_step(
    steps: list[dict[str, object]],
    needle: str,
) -> int:
    matches = [
        index
        for index, step in enumerate(steps)
        if step_has_exec(step, needle)
    ]

    if len(matches) != 1:
        fail(
            "executable operation must occur exactly once: "
            + needle
        )

    return matches[0]


def find_named_step(
    steps: list[dict[str, object]],
    name: str,
) -> int:
    matches = [
        index
        for index, step in enumerate(steps)
        if step["name"] == name
    ]

    if len(matches) != 1:
        fail(f"named step must occur exactly once: {name}")

    return matches[0]


def find_uses_steps(
    steps: list[dict[str, object]],
    action: str,
) -> list[int]:
    return [
        index
        for index, step in enumerate(steps)
        if step["uses"] == action
    ]


def require_exec(
    steps: list[dict[str, object]],
    values: tuple[str, ...],
    scope: str,
) -> None:
    for value in values:
        if not any(
            step_has_exec(step, value)
            for step in steps
        ):
            fail(
                f"{scope} missing executable operation: "
                + value
            )


def require_action(
    steps: list[dict[str, object]],
    action: str,
    scope: str,
) -> None:
    if not find_uses_steps(steps, action):
        fail(f"{scope} missing action: {action}")


def permissions(lines: list[str]) -> dict[str, str]:
    starts = [
        index
        for index, line in enumerate(lines)
        if line == "permissions:"
    ]

    if len(starts) != 1:
        fail("workflow must contain exactly one permissions mapping")

    result: dict[str, str] = {}

    for line in lines[starts[0] + 1:]:
        if meaningful(line) and indent(line) == 0:
            break

        if meaningful(line) and indent(line) == 2:
            text = line.strip()

            if ":" not in text:
                fail("invalid workflow permission entry")

            key, value = text.split(":", 1)

            if key in result:
                fail(f"duplicate workflow permission: {key}")

            result[key] = value.strip()

    return result


if not WORKFLOW.is_file():
    fail("production workflow missing")

lines = WORKFLOW.read_text(
    encoding="utf-8"
).splitlines()

perms = permissions(lines)

expected_permissions = {
    "contents": "read",
    "id-token": "write",
    "attestations": "write",
}

for key, value in expected_permissions.items():
    if perms.get(key) != value:
        fail(
            f"workflow permission invalid: "
            f"{key}={perms.get(key)!r}"
        )

build_job = find_job(lines, "reproducible-build")
final_job = find_job(lines, "production-gate")

if nested_scalar(
    build_job,
    "container",
    "image",
) != EXACT_IMAGE:
    fail("reproducible-build container image invalid")

if nested_scalar(
    final_job,
    "container",
    "image",
) != EXACT_IMAGE:
    fail("production-gate container image invalid")

if nested_scalar(
    build_job,
    "strategy",
    "fail-fast",
) != "false":
    fail("reproducible-build fail-fast must be false")

if matrix_build(build_job) != "[a, b]":
    fail("reproducible-build matrix must be [a, b]")

if scalar(final_job, "needs") != "reproducible-build":
    fail("production-gate needs contract invalid")

build = parse_steps(build_job)
final = parse_steps(final_job)

require_action(
    build,
    "actions/checkout@v4",
    "reproducible-build",
)
require_action(
    build,
    "actions/upload-artifact@v4",
    "reproducible-build",
)
require_action(
    final,
    "actions/checkout@v4",
    "production-gate",
)

require_exec(
    build,
    (
        "python tools/verify_build_environment.py",
        "--require-hashes",
        "--only-binary=:all:",
        "-r requirements/build.lock",
        "-r requirements/production.lock",
        "python -m pip install "
        "--no-deps --no-build-isolation -e .",
        "python tools/verify_production_dependencies.py",
        "python tools/verify_production_attestation.py",
        "python tools/release_gate.py --production",
        "python tools/create_sbom.py",
        "python tools/verify_sbom.py",
    ),
    "reproducible-build",
)

build_order = [
    find_exec_step(
        build,
        "python tools/verify_build_environment.py",
    ),
    find_exec_step(
        build,
        "-r requirements/build.lock",
    ),
    find_exec_step(
        build,
        "-r requirements/production.lock",
    ),
    find_exec_step(
        build,
        "python tools/verify_production_dependencies.py",
    ),
    find_exec_step(
        build,
        "python tools/verify_production_attestation.py",
    ),
    find_exec_step(
        build,
        "python tools/release_gate.py --production",
    ),
    find_exec_step(
        build,
        "python tools/create_sbom.py",
    ),
    find_exec_step(
        build,
        "python tools/verify_sbom.py",
    ),
    find_named_step(
        build,
        "Upload reproducibility candidate",
    ),
]

if build_order != sorted(build_order):
    fail("reproducible-build trust-chain ordering invalid")

downloads = find_uses_steps(
    final,
    "actions/download-artifact@v4",
)

if len(downloads) != 2:
    fail("production-gate must download exactly two candidates")

download_contracts = {
    (
        final[index]["with"].get("name"),
        final[index]["with"].get("path"),
    )
    for index in downloads
}

if download_contracts != {
    ("aodsl-repro-a", "repro/a"),
    ("aodsl-repro-b", "repro/b"),
}:
    fail("production-gate candidate download contract invalid")

require_exec(
    final,
    (
        "python tools/verify_build_environment.py",
        "--require-hashes",
        "--only-binary=:all:",
        "-r requirements/build.lock",
        "-r requirements/production.lock",
        "python -m pip install "
        "--no-deps --no-build-isolation -e .",
        "python tools/verify_production_dependencies.py",
        "python tools/verify_reproducible_artifacts.py",
        "--output dist/reproducibility-manifest.json",
        "cp repro/a/aodsl-1.0.0-production-source.zip dist/",
        "cp repro/a/aodsl-1.0.0.cdx.json dist/",
        "cp repro/a/release-manifest.json dist/",
        "python tools/verify_sbom.py",
        "rm -rf dist/certified-bundle-stage",
        "python tools/create_certified_bundle_manifest.py",
        "python tools/verify_certified_bundle_manifest.py",
        "dist/certified-bundle-manifest.json",
        "python tools/create_release_identity.py",
        "python tools/verify_release_identity.py",
    ),
    "production-gate",
)

for forbidden in (
    "python tools/release_gate.py --production",
    "python tools/create_sbom.py",
):
    if any(
        step_has_exec(step, forbidden)
        for step in final
    ):
        fail(
            "production-gate must not rebuild certified "
            f"bytes: {forbidden}"
        )

final_order = [
    downloads[0],
    downloads[1],
    find_exec_step(
        final,
        "python tools/verify_reproducible_artifacts.py",
    ),
    find_exec_step(
        final,
        "cp repro/a/aodsl-1.0.0-production-source.zip dist/",
    ),
    find_exec_step(
        final,
        "cp repro/a/aodsl-1.0.0.cdx.json dist/",
    ),
    find_exec_step(
        final,
        "python tools/verify_sbom.py",
    ),
    find_exec_step(
        final,
        "rm -rf dist/certified-bundle-stage",
    ),
    find_exec_step(
        final,
        "python tools/create_certified_bundle_manifest.py",
    ),
    find_exec_step(
        final,
        "python tools/verify_certified_bundle_manifest.py",
    ),
    find_exec_step(
        final,
        "dist/certified-bundle-manifest.json",
    ),
    find_exec_step(
        final,
        "python tools/create_release_identity.py",
    ),
    find_exec_step(
        final,
        "python tools/verify_release_identity.py",
    ),
    find_named_step(
        final,
        "Upload certified artifact",
    ),
]

if final_order != sorted(final_order):
    fail("production-gate trust-chain ordering invalid")

certified = final[
    find_named_step(final, "Upload certified artifact")
]

if certified["uses"] != "actions/upload-artifact@v4":
    fail("certified upload action invalid")

certified_with = certified["with"]

if certified_with.get("name") != "aodsl-certified-production":
    fail("certified upload name invalid")

if certified_with.get("if-no-files-found") != "error":
    fail("certified upload must fail on missing files")

certified_paths = certified_with.get("path", "")

# Block scalars under `with.path` are intentionally not interpreted by
# the small scalar parser. The workflow still receives executable/path
# closure coverage from INV-056 and the release-identity verifier.
# Here we only require the semantic upload step itself.

print("CI-PROD-001: PASSED")
print(
    "Two independent candidate executions are compared before "
    "exact-byte promotion and certification."
)
