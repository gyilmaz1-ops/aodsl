import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_ROOT = ROOT / "src" / "investment_domain"
TEST_ROOT = ROOT / "tests" / "investment_domain"

EXACT_DIAGNOSTIC = re.compile(
    r"^(IDM-R[0-9]+)(?:: .*)?$"
)

# R556 is a defense-in-depth branch whose invalid dependency type is
# blocked by the normal PostgreSQL schema. It is intentionally exempt
# from the public-path exact-diagnostic coverage requirement.
COVERAGE_EXCEPTIONS = {
    "IDM-R556",
}


def diagnostic_literals(root: Path) -> tuple[set[str], set[str]]:
    exact = set()
    nonexact = set()

    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(
            path.read_text(encoding="utf-8"),
            filename=str(path),
        )

        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
            ):
                continue

            value = node.value

            if not value.startswith("IDM-R"):
                continue

            match = EXACT_DIAGNOSTIC.fullmatch(value)

            if match:
                exact.add(match.group(1))
            else:
                nonexact.add(value)

    return exact, nonexact


def inventories():
    production, production_nonexact = diagnostic_literals(
        PRODUCTION_ROOT
    )
    tests, test_nonexact = diagnostic_literals(TEST_ROOT)

    return (
        production,
        production_nonexact,
        tests,
        test_nonexact,
    )


def test_every_production_diagnostic_has_exact_test_contract():
    production, _, tests, _ = inventories()

    uncovered = production - tests - COVERAGE_EXCEPTIONS

    assert uncovered == set()


def test_tests_do_not_claim_unknown_exact_diagnostics():
    production, _, tests, _ = inventories()

    unknown = tests - production

    assert unknown == set()


def test_coverage_exceptions_are_live_and_necessary():
    production, _, tests, _ = inventories()

    assert COVERAGE_EXCEPTIONS <= production
    assert COVERAGE_EXCEPTIONS.isdisjoint(tests)


def test_production_diagnostics_use_exact_literal_contract():
    _, production_nonexact, _, _ = inventories()

    assert production_nonexact == set()
