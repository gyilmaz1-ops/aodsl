from __future__ import annotations

import os
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_EVEN, localcontext

import pytest

from investment_domain import canonical_id
from investment_domain.nodes import Estimate, Security
from investment_domain.postgres_migrations import PostgreSQLMigrationManager
from investment_domain.postgres_repository import (
    PostgreSQLEvidenceRepository,
    RepositoryReadError,
)
from investment_domain.valuation_execution import (
    ValuationExecutionRequest,
    ValuationExecutionService,
)


UTC = timezone.utc
DSN = os.environ.get("IDM_TEST_POSTGRES_DSN")

pytestmark = pytest.mark.skipif(
    not DSN,
    reason="IDM_TEST_POSTGRES_DSN is not configured",
)


def utc(year, month, day, hour=0):
    return datetime(year, month, day, hour, tzinfo=UTC)


def security(seed: str) -> Security:
    return Security(
        id="security:nasdaq:nvda",
        company_id=f"company:nvidia:{seed}",
        venue="NASDAQ",
        ticker="NVDA",
        currency="USD",
    )


def estimate(
    *,
    subject_id: str,
    metric_name: str,
    value: str,
    period_end,
    unit: str,
    currency: str | None,
    as_of=None,
) -> Estimate:
    payload = {
        "subject_id": subject_id,
        "metric_name": metric_name,
        "period_end": period_end,
        "value": Decimal(value),
        "unit": unit,
        "scenario": "BASE",
        "model_version": "forecast-v1",
        "as_of": as_of or utc(2026, 9, 27, 10),
        "currency": currency,
    }
    return Estimate(
        id=canonical_id("estimate", payload),
        **payload,
    )


def dcf_dependencies(
    node_security: Security,
) -> tuple[Estimate, ...]:
    return (
        estimate(
            subject_id=node_security.company_id,
            metric_name="financial.free_cash_flow",
            value="100",
            period_end=utc(2027, 12, 31),
            unit="currency",
            currency="USD",
        ),
        estimate(
            subject_id=node_security.company_id,
            metric_name="financial.free_cash_flow",
            value="110",
            period_end=utc(2028, 12, 31),
            unit="currency",
            currency="USD",
        ),
        estimate(
            subject_id=node_security.company_id,
            metric_name="valuation.wacc",
            value="0.10",
            period_end=utc(2026, 9, 27),
            unit="ratio",
            currency=None,
        ),
        estimate(
            subject_id=node_security.company_id,
            metric_name="valuation.terminal_growth_rate",
            value="0",
            period_end=utc(2026, 9, 27),
            unit="ratio",
            currency=None,
        ),
        estimate(
            subject_id=node_security.company_id,
            metric_name="financial.net_debt",
            value="10",
            period_end=utc(2026, 9, 27),
            unit="currency",
            currency="USD",
        ),
        estimate(
            subject_id=node_security.id,
            metric_name="market.diluted_shares_outstanding",
            value="100",
            period_end=utc(2026, 9, 27),
            unit="shares",
            currency=None,
        ),
    )


def expected_dcf_value() -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 34
        ctx.rounding = ROUND_HALF_EVEN

        one = Decimal("1")
        wacc = Decimal("0.10")
        growth = Decimal("0")
        fcf_1 = Decimal("100")
        fcf_2 = Decimal("110")
        net_debt = Decimal("10")
        shares = Decimal("100")

        discount_base = one + wacc

        enterprise_value = (
            fcf_1 / discount_base
            + fcf_2 / (discount_base ** 2)
        )

        terminal_value = (
            fcf_2
            * (one + growth)
            / (wacc - growth)
        )

        enterprise_value += (
            terminal_value
            / (discount_base ** 2)
        )

        return +(
            (enterprise_value - net_debt)
            / shares
        )


@pytest.fixture
def repo():
    PostgreSQLMigrationManager(DSN).migrate()
    repository = PostgreSQLEvidenceRepository(DSN)

    def clean():
        with repository.connect() as con:
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

    clean()
    yield repository
    clean()


def persist_inputs(
    repo,
    *,
    seed: str,
) -> tuple[Security, tuple[Estimate, ...]]:
    node_security = security(seed)
    dependencies = dcf_dependencies(node_security)

    repo.add_security(node_security)

    for dependency in dependencies:
        repo.add_estimate(dependency)

    return node_security, dependencies


def valuation_count(repo) -> int:
    with repo.connect() as con:
        return con.execute(
            "SELECT COUNT(*) FROM valuation_facts"
        ).fetchone()[0]


def valuation_dependency_edges(
    repo,
    valuation_id: str,
) -> tuple[str, ...]:
    with repo.connect() as con:
        rows = con.execute(
            """
            SELECT target_id
            FROM domain_edges
            WHERE source_id = %s
              AND source_type = 'Valuation'
              AND edge_type = 'DEPENDS_ON'
            ORDER BY target_id
            """,
            (valuation_id,),
        ).fetchall()

    return tuple(str(row[0]) for row in rows)


def test_execute_persists_and_exactly_verifies_live_dcf(repo):
    node_security, dependencies = persist_inputs(
        repo,
        seed="execution-happy",
    )

    dependency_ids = tuple(
        dependency.id
        for dependency in dependencies
    )

    request = ValuationExecutionRequest(
        security_id=node_security.id,
        method="DCF",
        model_version="dcf-v1",
        scenario="BASE",
        currency="USD",
        as_of=utc(2026, 9, 27, 12),
        research_cutoff=utc(2026, 9, 27, 12),
        dependency_ids=dependency_ids,
    )

    result = ValuationExecutionService(repo).execute(request)

    assert result.valuation.security_id == node_security.id
    assert result.valuation.method == "DCF"
    assert result.valuation.model_version == "dcf-v1"
    assert result.valuation.scenario == "BASE"
    assert result.valuation.value == expected_dcf_value()

    assert result.dependency_ids == dependency_ids

    assert valuation_count(repo) == 1

    assert valuation_dependency_edges(
        repo,
        result.valuation.id,
    ) == tuple(sorted(dependency_ids))

    assert repo.verify_valuation(
        result.valuation.id
    ) == result.valuation


def test_execute_rejects_future_dependency_before_persistence(repo):
    node_security, dependencies = persist_inputs(
        repo,
        seed="execution-future",
    )

    future_dependency = replace(
        dependencies[0],
        as_of=utc(2026, 9, 28, 10),
    )

    future_payload = {
        "subject_id": future_dependency.subject_id,
        "metric_name": future_dependency.metric_name,
        "period_end": future_dependency.period_end,
        "value": future_dependency.value,
        "unit": future_dependency.unit,
        "scenario": future_dependency.scenario,
        "model_version": future_dependency.model_version,
        "as_of": future_dependency.as_of,
        "currency": future_dependency.currency,
    }

    future_dependency = replace(
        future_dependency,
        id=canonical_id(
            "estimate",
            future_payload,
        ),
    )

    repo.add_estimate(future_dependency)

    dependency_ids = (
        future_dependency.id,
        *tuple(
            dependency.id
            for dependency in dependencies[1:]
        ),
    )

    request = ValuationExecutionRequest(
        security_id=node_security.id,
        method="DCF",
        model_version="dcf-v1",
        scenario="BASE",
        currency="USD",
        as_of=utc(2026, 9, 27, 12),
        research_cutoff=utc(2026, 9, 27, 12),
        dependency_ids=dependency_ids,
    )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R550: VALUATION_INPUT_NOT_VISIBLE_AT_CUTOFF",
    ):
        ValuationExecutionService(repo).execute(request)

    assert valuation_count(repo) == 0

    with repo.connect() as con:
        edge_count = con.execute(
            """
            SELECT COUNT(*)
            FROM domain_edges
            WHERE source_type = 'Valuation'
              AND edge_type = 'DEPENDS_ON'
            """
        ).fetchone()[0]

    assert edge_count == 0






def test_execute_rolls_back_entire_valuation_when_dependency_insert_fails(repo):
    """
    R555B durable atomicity contract.

    Failure is injected by PostgreSQL itself at the Valuation -> DEPENDS_ON
    persistence boundary. Therefore this test remains valid even if service
    orchestration is refactored behind a repository aggregate operation.
    """
    node_security, dependencies = persist_inputs(
        repo,
        seed="execution-db-atomicity-dependency-insert-failure",
    )
    dependency_ids = tuple(
        dependency.id
        for dependency in dependencies
    )

    request = ValuationExecutionRequest(
        security_id=node_security.id,
        method="DCF",
        model_version="dcf-v1",
        scenario="BASE",
        currency="USD",
        as_of=utc(2026, 9, 27, 12),
        research_cutoff=utc(2026, 9, 27, 12),
        dependency_ids=dependency_ids,
    )

    trigger_name = "r555b_fail_valuation_dependency_insert"
    function_name = "r555b_fail_valuation_dependency_insert_fn"

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                f"""
                CREATE OR REPLACE FUNCTION {function_name}()
                RETURNS trigger
                LANGUAGE plpgsql
                AS $$
                BEGIN
                    IF NEW.source_type = 'Valuation'
                       AND NEW.edge_type = 'DEPENDS_ON'
                    THEN
                        RAISE EXCEPTION
                            'R555B_TEST_DEPENDENCY_INSERT_FAILURE';
                    END IF;

                    RETURN NEW;
                END;
                $$;
                """
            )

            con.execute(
                f"""
                CREATE TRIGGER {trigger_name}
                BEFORE INSERT ON domain_edges
                FOR EACH ROW
                EXECUTE FUNCTION {function_name}()
                """
            )

    try:
        with pytest.raises(
            Exception,
            match="R555B_TEST_DEPENDENCY_INSERT_FAILURE",
        ):
            ValuationExecutionService(repo).execute(request)

        # Atomic execution invariant:
        #
        # A failure while persisting valuation provenance must roll back
        # the Valuation node and its projection as well as all dependency
        # edges. Pre-existing input nodes are intentionally unaffected.
        assert valuation_count(repo) == 0

        with repo.connect() as con:
            valuation_node_count = con.execute(
                """
                SELECT COUNT(*)
                FROM domain_nodes
                WHERE node_type = 'Valuation'
                """
            ).fetchone()[0]

            valuation_fact_count = con.execute(
                """
                SELECT COUNT(*)
                FROM valuation_facts
                """
            ).fetchone()[0]

            valuation_edge_count = con.execute(
                """
                SELECT COUNT(*)
                FROM domain_edges
                WHERE source_type = 'Valuation'
                  AND edge_type = 'DEPENDS_ON'
                """
            ).fetchone()[0]

        assert valuation_node_count == 0
        assert valuation_fact_count == 0
        assert valuation_edge_count == 0

    finally:
        with repo.connect() as con:
            with con.transaction():
                con.execute(
                    f"""
                    DROP TRIGGER IF EXISTS {trigger_name}
                    ON domain_edges
                    """
                )
                con.execute(
                    f"""
                    DROP FUNCTION IF EXISTS {function_name}()
                    """
                )
