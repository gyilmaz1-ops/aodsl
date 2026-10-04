from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from investment_domain import canonical_id
from investment_domain.nodes import Calculation, Metric
from investment_domain.postgres_migrations import PostgreSQLMigrationManager
from investment_domain.postgres_repository import (
    PostgreSQLEvidenceRepository,
    RepositoryReadError,
)


UTC = timezone.utc
DSN = os.environ.get("IDM_TEST_POSTGRES_DSN")

pytestmark = pytest.mark.skipif(
    not DSN,
    reason="IDM_TEST_POSTGRES_DSN is not configured",
)


def utc(year, month, day, hour=0, minute=0):
    return datetime(
        year,
        month,
        day,
        hour,
        minute,
        tzinfo=UTC,
    )


def _metric_id(values):
    return canonical_id(
        "metric",
        {
            "subject_id": values["subject_id"],
            "name": values["name"],
            "period_start": values["period_start"],
            "period_end": values["period_end"],
            "effective_at": values["effective_at"],
            "observed_at": values["observed_at"],
            "published_at": values["published_at"],
            "source_id": values["source_id"],
            "source_version": values["source_version"],
        },
    )


def metric(seed, value):
    values = {
        "subject_id": "company:006e",
        "name": "financial.revenue",
        "value": Decimal(value),
        "unit": "currency",
        "currency": "USD",
        "period_start": utc(2026, 4, 1),
        "period_end": utc(2026, 6, 30),
        "effective_at": utc(2026, 6, 30),
        "observed_at": utc(2026, 9, 1, 8),
        "published_at": utc(2026, 9, 1, 9),
        "ingested_at": utc(2026, 9, 1, 10),
        "source_id": f"source:{seed}",
        "source_version": "1",
        "supersedes_id": None,
    }
    values["id"] = _metric_id(values)
    return Metric(**values)


def _calculation_id(values):
    return canonical_id(
        "calculation",
        {
            "subject_id": values["subject_id"],
            "formula": values["formula"],
            "input_ids": values["input_ids"],
            "model_version": values["model_version"],
        },
    )


def calculation(
    input_ids,
    *,
    formula="SUB(REF(0),REF(1))",
    value="7",
    unit="currency",
    currency="USD",
):
    values = {
        "subject_id": "company:006e",
        "formula": formula,
        "input_ids": tuple(input_ids),
        "value": Decimal(value),
        "unit": unit,
        "currency": currency,
        "model_version": "cel-v1",
    }
    values["id"] = _calculation_id(values)
    return Calculation(**values)


@pytest.fixture
def repo():
    PostgreSQLMigrationManager(DSN).migrate()
    return PostgreSQLEvidenceRepository(DSN)


@pytest.fixture(autouse=True)
def clean_database(repo):
    with repo.connect() as con:
        with con.transaction():
            con.execute("DELETE FROM domain_edges")
            con.execute("DELETE FROM catalyst_impact_facts")
            con.execute("DELETE FROM valuation_facts")
            con.execute("DELETE FROM forecast_facts")
            con.execute("DELETE FROM estimate_facts")
            con.execute("DELETE FROM calculation_facts")
            con.execute("DELETE FROM metric_facts")
            con.execute("DELETE FROM evidence_facts")
            con.execute("DELETE FROM claim_facts")
            con.execute("DELETE FROM domain_nodes")

    yield

    with repo.connect() as con:
        with con.transaction():
            con.execute("DELETE FROM domain_edges")
            con.execute("DELETE FROM catalyst_impact_facts")
            con.execute("DELETE FROM valuation_facts")
            con.execute("DELETE FROM forecast_facts")
            con.execute("DELETE FROM estimate_facts")
            con.execute("DELETE FROM calculation_facts")
            con.execute("DELETE FROM metric_facts")
            con.execute("DELETE FROM evidence_facts")
            con.execute("DELETE FROM claim_facts")
            con.execute("DELETE FROM domain_nodes")


def persist_metric_calculation(repo, *, persisted_value="7"):
    left = metric("left", "10")
    right = metric("right", "3")

    calc = calculation(
        (left.id, right.id),
        value=persisted_value,
    )

    repo.add_metric(left)
    repo.add_metric(right)
    repo.add_calculation(calc)
    repo.add_calculation_inputs(calc.id)

    return left, right, calc


def test_verify_calculation_rejects_noncanonical_id_before_db(repo):
    with pytest.raises(ValueError):
        repo.verify_calculation("calculation:not-canonical")


def test_metric_only_calculation_is_reproducible(repo):
    _, _, calc = persist_metric_calculation(repo)

    result = repo.verify_calculation(calc.id)

    assert result == calc


def test_reproduction_uses_input_ids_operand_order(repo):
    left = metric("left", "10")
    right = metric("right", "3")

    calc = calculation(
        (right.id, left.id),
        formula="SUB(REF(0),REF(1))",
        value="-7",
    )

    repo.add_metric(left)
    repo.add_metric(right)
    repo.add_calculation(calc)
    repo.add_calculation_inputs(calc.id)

    result = repo.verify_calculation(calc.id)

    assert result == calc


def test_nonreproducible_materialized_value_fails_closed(repo):
    _, _, calc = persist_metric_calculation(
        repo,
        persisted_value="999",
    )

    with pytest.raises(RepositoryReadError):
        repo.verify_calculation(calc.id)


def test_missing_provenance_edge_fails_closed(repo):
    _, right, calc = persist_metric_calculation(repo)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                DELETE FROM domain_edges
                WHERE source_id = %s
                  AND source_type = 'Calculation'
                  AND edge_type = 'DERIVED_FROM'
                  AND target_id = %s
                """,
                (calc.id, right.id),
            )

    with pytest.raises(RepositoryReadError):
        repo.verify_calculation(calc.id)


def persist_nested_calculation(
    repo,
    *,
    child_value="7",
    parent_value="14",
):
    left = metric("nested-left", "10")
    right = metric("nested-right", "3")

    child = calculation(
        (left.id, right.id),
        formula="SUB(REF(0),REF(1))",
        value=child_value,
    )

    parent = calculation(
        (child.id,),
        formula="MUL(REF(0),CONST(2))",
        value=parent_value,
    )

    repo.add_metric(left)
    repo.add_metric(right)

    repo.add_calculation(child)
    repo.add_calculation_inputs(child.id)

    repo.add_calculation(parent)
    repo.add_calculation_inputs(parent.id)

    return left, right, child, parent


def test_nested_calculation_is_recursively_reproducible(repo):
    _, _, _, parent = persist_nested_calculation(repo)

    result = repo.verify_calculation(parent.id)

    assert result == parent


def test_nonreproducible_child_makes_parent_fail_closed(repo):
    _, _, _, parent = persist_nested_calculation(
        repo,
        child_value="999",
        parent_value="1998",
    )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R606: CALCULATION_MATERIALIZATION_NOT_REPRODUCIBLE",
    ):
        repo.verify_calculation(parent.id)


def test_prior_child_verification_does_not_poison_parent_traversal(repo):
    left = metric("shared-left", "10")
    right = metric("shared-right", "3")

    child = calculation(
        (left.id, right.id),
        formula="SUB(REF(0),REF(1))",
        value="7",
    )

    parent = calculation(
        (child.id,),
        formula="MUL(REF(0),CONST(2))",
        value="14",
    )

    repo.add_metric(left)
    repo.add_metric(right)

    repo.add_calculation(child)
    repo.add_calculation_inputs(child.id)

    repo.add_calculation(parent)
    repo.add_calculation_inputs(parent.id)

    # Verify the same immutable child independently first. A traversal
    # history must not be mistaken for an active recursion cycle.
    assert repo.verify_calculation(child.id) == child
    assert repo.verify_calculation(parent.id) == parent


def test_shared_calculation_diamond_is_reproducible(repo):
    base_left = metric("diamond-left", "10")
    base_right = metric("diamond-right", "3")

    shared = calculation(
        (base_left.id, base_right.id),
        formula="SUB(REF(0),REF(1))",
        value="7",
    )

    left = calculation(
        (shared.id,),
        formula="MUL(REF(0),CONST(2))",
        value="14",
    )

    right = calculation(
        (shared.id,),
        formula="MUL(REF(0),CONST(3))",
        value="21",
    )

    root = calculation(
        (left.id, right.id),
        formula="ADD(REF(0),REF(1))",
        value="35",
    )

    repo.add_metric(base_left)
    repo.add_metric(base_right)

    repo.add_calculation(shared)
    repo.add_calculation_inputs(shared.id)

    repo.add_calculation(left)
    repo.add_calculation_inputs(left.id)

    repo.add_calculation(right)
    repo.add_calculation_inputs(right.id)

    repo.add_calculation(root)
    repo.add_calculation_inputs(root.id)

    assert repo.verify_calculation(root.id) == root


def test_materialized_unit_mismatch_fails_closed(repo):
    left = metric("unit-left", "10")
    right = metric("unit-right", "3")

    calc = calculation(
        (left.id, right.id),
        formula="SUB(REF(0),REF(1))",
        value="7",
    )

    # Calculation identity excludes materialized value/unit/currency.
    # Replace only the materialized unit before persistence.
    calc = Calculation(
        id=calc.id,
        subject_id=calc.subject_id,
        formula=calc.formula,
        input_ids=calc.input_ids,
        value=calc.value,
        unit="shares",
        currency=calc.currency,
        model_version=calc.model_version,
    )

    repo.add_metric(left)
    repo.add_metric(right)
    repo.add_calculation(calc)
    repo.add_calculation_inputs(calc.id)

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R606: CALCULATION_MATERIALIZATION_NOT_REPRODUCIBLE",
    ):
        repo.verify_calculation(calc.id)


def test_materialized_currency_mismatch_fails_closed(repo):
    left = metric("currency-left", "10")
    right = metric("currency-right", "3")

    calc = calculation(
        (left.id, right.id),
        formula="SUB(REF(0),REF(1))",
        value="7",
    )

    # Identity remains unchanged because currency is materialized output,
    # not Calculation identity-bearing state.
    calc = Calculation(
        id=calc.id,
        subject_id=calc.subject_id,
        formula=calc.formula,
        input_ids=calc.input_ids,
        value=calc.value,
        unit=calc.unit,
        currency="EUR",
        model_version=calc.model_version,
    )

    repo.add_metric(left)
    repo.add_metric(right)
    repo.add_calculation(calc)
    repo.add_calculation_inputs(calc.id)

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R606: CALCULATION_MATERIALIZATION_NOT_REPRODUCIBLE",
    ):
        repo.verify_calculation(calc.id)


def test_verify_calculation_is_read_only(repo):
    _, _, calc = persist_metric_calculation(repo)

    def snapshot():
        with repo.connect() as con:
            nodes = con.execute(
                """
                SELECT *
                FROM domain_nodes
                ORDER BY id
                """
            ).fetchall()

            edges = con.execute(
                """
                SELECT *
                FROM domain_edges
                ORDER BY
                    source_id,
                    source_type,
                    edge_type,
                    target_id,
                    target_type
                """
            ).fetchall()

            calculations = con.execute(
                """
                SELECT *
                FROM calculation_facts
                ORDER BY node_id
                """
            ).fetchall()

            metrics = con.execute(
                """
                SELECT *
                FROM metric_facts
                ORDER BY node_id
                """
            ).fetchall()

        return (
            nodes,
            edges,
            calculations,
            metrics,
        )

    before = snapshot()

    assert repo.verify_calculation(calc.id) == calc

    after = snapshot()

    assert after == before
