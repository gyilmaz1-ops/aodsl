#!/usr/bin/env python3
"""AODSL read-only checkpoint preflight v0.1."""

import argparse
import ast
import hashlib
import json
import subprocess
import sys
from pathlib import Path

EXPECTED_HEAD = (
    "c318c77a7859d5759bc42637f9281b515b61d825"
)

CHECKPOINTS = {
    "P6J-3G-4": (
        "tests/certification/"
        "test_aa3r_g4e60_release_identity_v2_binding.py",
        "tests/certification/"
        "test_inv052_release_identity_binding.py",
        "tests/certification/"
        "test_cp88ic37_release_identity_versioned_e2e.py",
    ),
}


def git(root, *args):
    return subprocess.check_output(
        ["git", *args],
        cwd=root,
        stderr=subprocess.PIPE,
    )


def snapshot(root):
    raw = git(root, "status", "--porcelain=v1", "-z")
    parts = raw.split(b"\0")
    result = []
    index = 0

    while index < len(parts):
        entry = parts[index]
        index += 1

        if not entry:
            continue

        code = entry[:2].decode("ascii")
        path = entry[3:].decode("utf-8", "surrogateescape")

        if "R" in code or "C" in code:
            index += 1

        file = root / path

        if file.is_symlink():
            digest = hashlib.sha256(
                str(file.readlink()).encode("utf-8")
            ).hexdigest()
        elif file.is_file():
            digest = hashlib.sha256(
                file.read_bytes()
            ).hexdigest()
        else:
            digest = "NOT_REGULAR_FILE"

        result.append({
            "status": code,
            "path": path,
            "sha256": digest,
        })

    return sorted(result, key=lambda x: x["path"])


def audit(root, checkpoint):
    head_before = git(
        root, "rev-parse", "HEAD"
    ).decode().strip()

    before = snapshot(root)
    tests = []
    errors = []

    def file_digest(name):
        path = root / name
        if path.is_symlink() or not path.is_file():
            return None
        return hashlib.sha256(path.read_bytes()).hexdigest()

    test_paths = CHECKPOINTS[checkpoint]
    hashes_before = {
        name: file_digest(name)
        for name in test_paths
    }

    if head_before != EXPECTED_HEAD:
        errors.append("HEAD_MISMATCH")

    for name in CHECKPOINTS[checkpoint]:
        path = root / name
        item = {"path": name}

        if not path.is_file() or path.is_symlink():
            item["result"] = "FAIL"
            errors.append("MISSING_OR_UNSAFE_FILE:" + name)
        else:
            try:
                data = path.read_bytes()
                tree = ast.parse(
                    data.decode("utf-8"),
                    filename=name,
                )
                item["sha256"] = hashlib.sha256(
                    data
                ).hexdigest()
                item["functions"] = [
                    node.name for node in tree.body
                    if isinstance(
                        node,
                        (ast.FunctionDef, ast.AsyncFunctionDef),
                    )
                ]
                item["result"] = "PASS"
            except (SyntaxError, UnicodeError, OSError) as exc:
                item["result"] = "FAIL"
                item["error"] = str(exc)
                errors.append("PARSE_ERROR:" + name)

        tests.append(item)

    head_after = git(
        root, "rev-parse", "HEAD"
    ).decode().strip()

    after = snapshot(root)

    hashes_after = {
        name: file_digest(name)
        for name in test_paths
    }

    for name in test_paths:
        if hashes_before[name] != hashes_after[name]:
            errors.append("TEST_FILE_CHANGED:" + name)

    if head_before != head_after:
        errors.append("HEAD_CHANGED")

    if before != after:
        errors.append("WORKTREE_CHANGED")

    return {
        "checkpoint": checkpoint,
        "mode": "read-only",
        "audit_type": "STATIC_PREFLIGHT",
        "head": head_before,
        "expected_head": EXPECTED_HEAD,
        "worktree_clean": not bool(before),
        "worktree_entries": len(before),
        "worktree_snapshot": before,
        "tests": tests,
        "errors": errors,
        "result": "PASS" if not errors else "BLOCKED",
        "tests_executed": False,
        "release_certified": False,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--checkpoint",
        choices=sorted(CHECKPOINTS),
        required=True,
    )
    parser.add_argument(
        "--mode",
        choices=["read-only"],
        default="read-only",
    )
    parser.add_argument(
        "--report",
        action="store_true",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent

    try:
        result = audit(root, args.checkpoint)
    except (OSError, subprocess.CalledProcessError) as exc:
        result = {
            "checkpoint": args.checkpoint,
            "mode": args.mode,
            "result": "BLOCKED",
            "errors": [str(exc)],
            "tests_executed": False,
            "release_certified": False,
        }

    if args.report:
        print(json.dumps(
            result, indent=2, sort_keys=True
        ))
    else:
        print("CHECKPOINT:", args.checkpoint)
        print("RESULT:", result["result"])
        print("ERRORS:", result["errors"])

    return 0 if result["result"] == "PASS" else 2


if __name__ == "__main__":
    sys.exit(main())
