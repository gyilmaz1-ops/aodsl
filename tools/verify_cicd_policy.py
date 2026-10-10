#!/usr/bin/env python3

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/production-release.yml"

EXACT_IMAGE = (
    'ghcr.io/gyilmaz1-ops/aodsl-build-git@sha256:'
    "0574165e4b162022d2f6b79528f07d2780f99bb4105ae996128c6530dedb7597"
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


        # Interpret the explicit literal file list under with.path.
        path_starts = [
            j for j, line in enumerate(raw)
            if line == "          path: |"
        ]
        if path_starts:
            if len(path_starts) != 1:
                fail("duplicate upload path block")
            paths = []
            for line in raw[path_starts[0] + 1:]:
                if not meaningful(line):
                    continue
                if indent(line) <= 10:
                    break
                if indent(line) != 12:
                    fail("invalid upload path indentation")
                paths.append(line.strip())
            step["with"]["path"] = "\n".join(paths)

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


def permissions(lines, level=0, required=True):
    prefix = " " * level + "permissions:"
    starts = [
        i for i, line in enumerate(lines)
        if line.startswith(prefix)
    ]
    if not starts and not required:
        return {}
    if len(starts) != 1 or lines[starts[0]] != prefix:
        fail("permissions must be a single explicit mapping")
    result = {}
    for line in lines[starts[0] + 1:]:
        if not meaningful(line):
            continue
        if indent(line) <= level:
            break
        if indent(line) != level + 2 or ":" not in line:
            fail("invalid permission entry")
        key, value = line.strip().split(":", 1)
        if key in result:
            fail(f"duplicate permission: {key}")
        result[key] = value.strip()
    return result


if not WORKFLOW.is_file():
    fail("production workflow missing")

lines = WORKFLOW.read_text(
    encoding="utf-8"
).splitlines()

build_job = find_job(lines, "reproducible-build")
final_job = find_job(lines, "production-gate")

if permissions(lines) != {"contents": "read"}:
    fail("workflow permissions must be exactly contents: read")
if permissions(build_job, 4) != {"contents": "read", "packages": "read"}:
    fail("reproducible-build permissions must be contents/read and packages/read")
if permissions(final_job, 4) != {
    "contents": "read",
    "id-token": "write",
    "attestations": "write",
    "packages": "read",
}:
    fail("production-gate permissions invalid")

REGISTRY_REFERENCE = 'ghcr.io/gyilmaz1-ops/aodsl-build-git@sha256:0574165e4b162022d2f6b79528f07d2780f99bb4105ae996128c6530dedb7597'

def require_registry_credentials(job):
    starts = [
        i for i, line in enumerate(job)
        if line == "    container:"
    ]
    if len(starts) != 1:
        fail("container mapping must occur exactly once")
    block = []
    for line in job[starts[0] + 1:]:
        if not meaningful(line):
            continue
        if indent(line) <= 4:
            break
        block.append(line)
    expected = [
        "      image: " + REGISTRY_REFERENCE,
        "      credentials:",
        "        username: ${{ github.actor }}",
        "        password: ${{ secrets.GITHUB_TOKEN }}",
    ]
    if block != expected:
        fail("container image or registry credentials invalid")

require_registry_credentials(build_job)
require_registry_credentials(final_job)

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



EXPECTED_WORKSPACE_GIT_TRUST = 'python - <<\'CHECK\'\nimport os\nfrom pathlib import Path\nimport subprocess\nworkspace = Path(os.environ["GITHUB_WORKSPACE"]).resolve()\nassert workspace == Path.cwd().resolve()\nsubprocess.run([\n"/usr/bin/git", "config", "--global", "--add",\n"safe.directory", str(workspace),\n], check=True)\nprint("WORKSPACE_GIT_TRUST: PASS")\nCHECK'

def require_workspace_git_trust(steps):
    trust = find_named_step(steps, "Configure workspace Git trust")
    if executable_text(steps[trust]) != EXPECTED_WORKSPACE_GIT_TRUST:
        fail("workspace Git trust executable contract invalid")
    checkouts = [
        index for index, step in enumerate(steps)
        if step["uses"] ==
        "actions/checkout@11d5960a326750d5838078e36cf38b85af677262"
    ]
    if len(checkouts) != 1:
        fail("workspace Git trust requires exactly one pinned checkout")
    source_ref = find_exec_step(
        steps, "python tools/verify_release_source_ref.py"
    )
    if not checkouts[0] < trust < source_ref:
        fail("workspace Git trust must follow checkout and precede source ref")

build = parse_steps(build_job)
final = parse_steps(final_job)

require_workspace_git_trust(build)
require_workspace_git_trust(final)

expected_paths = [
    "dist/${{ env.AODSL_ARTIFACT }}",
    "dist/${{ env.AODSL_SBOM }}",
    "dist/release-manifest.json",
    "dist/reproducibility-manifest.json",
    "dist/certified-bundle-manifest.json",
    "dist/certified-release-identity.json",
    "certification/production-certification-manifest.json",
    "certification/production-certification-attestation.json",
    "certification/evidence/live-certification-status.json",
]
upload_index = find_named_step(final, "Upload certified artifact")
upload_settings = final[upload_index]["with"]
if upload_settings.get("path", "").splitlines() != expected_paths:
    fail("certified upload must contain exactly the approved file list")
if upload_settings.get("if-no-files-found") != "error":
    fail("certified upload must fail on missing files")

attest_action = "actions/attest@1e69f48acb82d1966a394da916b4c1698aa569d6"
attest_steps = [
    (i, step) for i, step in enumerate(final)
    if step["uses"] == attest_action
]
expected_subjects = [
    "'dist/${{ env.AODSL_ARTIFACT }}'",
    "'dist/${{ env.AODSL_SBOM }}'",
]
if [step["with"].get("subject-path") for _, step in attest_steps] != expected_subjects:
    fail("attestation subjects must use the exact approved ZIP and SBOM")
identity_index = find_exec_step(final, "python tools/verify_release_identity.py")
if not (identity_index < attest_steps[0][0] < attest_steps[1][0] < upload_index):
    fail("attestations must follow identity verification and precede upload")


def require_checkout_full_history(steps, scope):
    checkout_action = (
        "actions/checkout@"
        "11d5960a326750d5838078e36cf38b85af677262"
    )

    matches = [
        step for step in steps
        if step["uses"] == checkout_action
    ]

    if len(matches) != 1:
        fail(f"{scope} must contain exactly one checkout step")

    settings = matches[0]["with"]

    if settings.get("fetch-depth") != "0":
        fail(
            f"{scope} checkout must use fetch-depth: 0"
        )


require_action(
    build,
    "actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
    "reproducible-build",
)
require_action(
    build,
    "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
    "reproducible-build",
)
require_action(
    final,
    "actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
    "production-gate",
)

require_checkout_full_history(build, "reproducible-build")
require_checkout_full_history(final, "production-gate")

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
    "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093",
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
        "cp repro/a/${AODSL_ARTIFACT} dist/",
        "cp repro/a/${AODSL_SBOM} dist/",
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
        "cp repro/a/${AODSL_ARTIFACT} dist/",
    ),
    find_exec_step(
        final,
        "cp repro/a/${AODSL_SBOM} dist/",
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

if certified["uses"] != "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02":
    fail("certified upload action invalid")

certified_with = certified["with"]

if certified_with.get("name") != "aodsl-certified-production":
    fail("certified upload name invalid")

if certified_with.get("if-no-files-found") != "error":
    fail("certified upload must fail on missing files")

print("CI-PROD-001: PASSED")
print(
    "Two independent candidate executions are compared before "
    "exact-byte promotion and certification."
)
