from __future__ import annotations

import ast
import re
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_ROOT = ROOT / "src" / "investment_domain"

WRITE_CODE_PATTERN = re.compile(
    r"\bIDM-(W\d{3}):\s*([A-Z0-9_]+)\b"
)


def repository_write_error_assignments():
    assignments = defaultdict(set)

    for path in sorted(PRODUCTION_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue

        tree = ast.parse(path.read_text())

        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
            ):
                continue

            for match in WRITE_CODE_PATTERN.finditer(
                node.value
            ):
                code = match.group(1)
                message = match.group(2)

                assignments[code].add(message)

    return assignments


def test_repository_write_error_codes_have_unique_semantics():
    assignments = repository_write_error_assignments()

    collisions = {
        code: tuple(sorted(messages))
        for code, messages in sorted(assignments.items())
        if len(messages) > 1
    }

    assert collisions == {}
