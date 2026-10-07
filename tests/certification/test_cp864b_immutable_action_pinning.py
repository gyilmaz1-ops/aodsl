import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_DIR = ROOT / ".github" / "workflows"

ACTION_REF_RE = re.compile(
    r"^\s*(?:-\s*)?uses:\s*"
    r"(?P<action>[^@\s]+)"
    r"@(?P<ref>[^\s#]+)",
    re.MULTILINE,
)

IMMUTABLE_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def external_action_references():
    references = []

    for workflow in sorted(WORKFLOW_DIR.glob("*.yml")):
        text = workflow.read_text(encoding="utf-8")

        for match in ACTION_REF_RE.finditer(text):
            action = match.group("action")
            ref = match.group("ref")

            if action.startswith("./"):
                continue

            references.append(
                (workflow.relative_to(ROOT).as_posix(), action, ref)
            )

    return references


def test_all_external_github_actions_are_pinned_to_full_commit_sha():
    references = external_action_references()

    assert references, "no external GitHub Actions references discovered"

    mutable = [
        f"{workflow}: {action}@{ref}"
        for workflow, action, ref in references
        if IMMUTABLE_SHA_RE.fullmatch(ref) is None
    ]

    assert not mutable, (
        "CP-86.4B: mutable GitHub Action references are forbidden; "
        "every external action must be pinned to an immutable "
        "40-character lowercase commit SHA:\n"
        + "\n".join(mutable)
    )


def test_required_action_pins_match_certified_sha_map():
    certified = {
        "actions/attest":
            "1e69f48acb82d1966a394da916b4c1698aa569d6",
        "actions/checkout":
            "11d5960a326750d5838078e36cf38b85af677262",
        "actions/download-artifact":
            "d3f86a106a0bac45b974a628896c90dbdf5c8093",
        "actions/setup-python":
            "a26af69be951a213d495a4c3e4e4022e16d87065",
        "actions/upload-artifact":
            "ea165f8d65b6e75b540449e92b4886f43607fa02",
    }

    references = external_action_references()

    observed = {}

    for workflow, action, ref in references:
        if action in certified:
            observed.setdefault(action, set()).add(ref)

    assert set(observed) == set(certified)

    mismatches = []

    for action, expected_sha in sorted(certified.items()):
        actual_refs = observed[action]

        if actual_refs != {expected_sha}:
            mismatches.append(
                f"{action}: expected={expected_sha}, "
                f"actual={sorted(actual_refs)}"
            )

    assert not mismatches, (
        "CP-86.4B: GitHub Action pin does not match the "
        "certified SHA map:\n"
        + "\n".join(mismatches)
    )
