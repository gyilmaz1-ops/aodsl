import ast
from pathlib import Path

import investment_domain


ROOT = Path(__file__).resolve().parents[2]
INIT = ROOT / "src" / "investment_domain" / "__init__.py"
TEST_ROOT = ROOT / "tests" / "investment_domain"


def declared_exports() -> list[str]:
    tree = ast.parse(
        INIT.read_text(encoding="utf-8"),
        filename=str(INIT),
    )

    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue

        if not any(
            isinstance(target, ast.Name)
            and target.id == "__all__"
            for target in node.targets
        ):
            continue

        value = ast.literal_eval(node.value)

        assert isinstance(value, list)
        assert all(isinstance(name, str) for name in value)

        return value

    raise AssertionError(
        "investment_domain.__all__ is not statically declared"
    )


def test_export_contract_is_unique_and_runtime_resolvable():
    exports = declared_exports()

    assert len(exports) == len(set(exports))

    missing = {
        name
        for name in exports
        if not hasattr(investment_domain, name)
    }

    assert missing == set()


def test_star_import_matches_declared_export_contract():
    exports = set(declared_exports())

    namespace = {}

    exec(
        "from investment_domain import *",
        {},
        namespace,
    )

    actual = {
        name
        for name in namespace
        if not name.startswith("__")
    }

    assert actual == exports


def test_every_export_has_investment_domain_test_binding():
    exports = set(declared_exports())
    bound = set()

    for path in sorted(TEST_ROOT.rglob("test_*.py")):
        tree = ast.parse(
            path.read_text(encoding="utf-8"),
            filename=str(path),
        )

        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue

            module = node.module or ""

            if not (
                module == "investment_domain"
                or module.startswith("investment_domain.")
            ):
                continue

            for alias in node.names:
                if alias.name in exports:
                    bound.add(alias.name)

    unbound = exports - bound

    assert unbound == set()
