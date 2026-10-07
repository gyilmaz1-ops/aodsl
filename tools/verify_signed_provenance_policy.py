#!/usr/bin/env python3

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/production-release.yml"


def fail(message: str) -> None:
    print(f"PROV-001: FAILED — {message}")
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

        if meaningful(line) and indent(line) <= 2:
            end = index
            break

    return lines[start:end]


def scalar(job: list[str], key: str) -> str | None:
    prefix = f"    {key}:"

    values = [
        line[len(prefix):].strip()
        for line in job
        if line.startswith(prefix)
    ]

    if len(values) > 1:
        fail(f"duplicate job key: {key}")

    return values[0] if values else None


def matrix_build(job: list[str]) -> str | None:
    strategy = [
        i
        for i, line in enumerate(job)
        if line == "    strategy:"
    ]

    if len(strategy) != 1:
        return None

    block = job[strategy[0] + 1:]
    matrix = [
        i
        for i, line in enumerate(block)
        if line == "      matrix:"
    ]

    if len(matrix) != 1:
        return None

    values = []

    for line in block[matrix[0] + 1:]:
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

    raw_steps: list[list[str]] = []
    current: list[str] | None = None

    for line in job[starts[0] + 1:]:
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

            text = line.strip()

            if text.startswith("name:"):
                step["name"] = text[5:].strip()
                i += 1
                continue

            if text.startswith("uses:"):
                step["uses"] = text[5:].strip()
                i += 1
                continue

            if text == "with:":
                values: dict[str, str] = {}
                i += 1

                while i < len(raw):
                    child = raw[i]

                    if meaningful(child) and indent(child) <= 8:
                        break

                    if meaningful(child) and indent(child) == 10:
                        item = child.strip()

                        if ":" in item:
                            key, value = item.split(":", 1)

                            if key in values:
                                fail(
                                    f"duplicate step with key: {key}"
                                )

                            values[key] = value.strip()

                    i += 1

                step["with"] = values
                continue

            if re.match(r"^run:\s*[|>][-+]?\s*$", text):
                commands: list[str] = []
                i += 1

                while i < len(raw):
                    child = raw[i]

                    if meaningful(child) and indent(child) <= 8:
                        break

                    command = child.strip()

                    if command and not command.startswith("#"):
                        commands.append(command)

                    i += 1

                step["run"] = commands
                continue

            if text.startswith("run:"):
                command = text[4:].strip()

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

for key, expected in (
    ("contents", "read"),
    ("id-token", "write"),
    ("attestations", "write"),
):
    if perms.get(key) != expected:
        fail(
            f"workflow permission invalid: "
            f"{key}={perms.get(key)!r}"
        )

build_job = find_job(lines, "reproducible-build")
final_job = find_job(lines, "production-gate")

if matrix_build(build_job) != "[a, b]":
    fail("reproducible-build matrix must be [a, b]")

if scalar(final_job, "needs") != "reproducible-build":
    fail("production-gate needs contract invalid")

build = parse_steps(build_job)
final = parse_steps(final_job)

for required in (
    "python tools/release_gate.py --production",
    "python tools/create_sbom.py",
    "python tools/verify_sbom.py",
):
    if not any(
        step_has_exec(step, required)
        for step in build
    ):
        fail(
            "reproducible-build missing executable operation: "
            + required
        )

compare = find_exec_step(
    final,
    "python tools/verify_reproducible_artifacts.py",
)

promote_zip = find_exec_step(
    final,
    "cp repro/a/aodsl-1.0.0-production-source.zip dist/",
)

promote_sbom = find_exec_step(
    final,
    "cp repro/a/aodsl-1.0.0.cdx.json dist/",
)

find_exec_step(
    final,
    "--output dist/reproducibility-manifest.json",
)

find_exec_step(
    final,
    "rm -rf dist/certified-bundle-stage",
)

find_exec_step(
    final,
    "python tools/create_certified_bundle_manifest.py",
)

find_exec_step(
    final,
    "python tools/verify_certified_bundle_manifest.py",
)

identity_create = find_exec_step(
    final,
    "python tools/create_release_identity.py",
)

identity_verify = find_exec_step(
    final,
    "python tools/verify_release_identity.py",
)

attestations = [
    (index, step)
    for index, step in enumerate(final)
    if step["uses"] == "actions/attest@1e69f48acb82d1966a394da916b4c1698aa569d6"
]

if len(attestations) != 2:
    fail("production-gate must contain exactly two attestations")

subjects = [
    step["with"].get("subject-path")
    for _, step in attestations
]

if subjects != [
    "'dist/aodsl-*-production-source.zip'",
    "'dist/aodsl-*.cdx.json'",
]:
    fail("production-gate attestation subjects invalid")

certified_upload = find_named_step(
    final,
    "Upload certified artifact",
)

upload = final[certified_upload]

if upload["uses"] != "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02":
    fail("certified upload action invalid")

if (
    upload["with"].get("name")
    != "aodsl-certified-production"
):
    fail("certified upload name invalid")

for forbidden in (
    "python tools/release_gate.py --production",
    "python tools/create_sbom.py",
):
    if any(
        step_has_exec(step, forbidden)
        for step in final
    ):
        fail(
            "final provenance job must not rebuild subject bytes: "
            + forbidden
        )

attest_indices = [
    index
    for index, _ in attestations
]

if not (
    compare
    < promote_zip
    <= promote_sbom
    < identity_create
    < identity_verify
    < attest_indices[0]
    < attest_indices[1]
    < certified_upload
):
    fail(
        "required order is compare -> promote exact candidate -> "
        "release identity -> verify identity -> signed ZIP/SBOM "
        "provenance -> certified upload"
    )

print("PROV-001: PASSED")
print(
    "Compared and identity-bound production bytes receive "
    "GitHub OIDC/Sigstore provenance before certified upload."
)
