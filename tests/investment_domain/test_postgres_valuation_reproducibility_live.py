from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_EVEN, localcontext

import pytest
from psycopg.errors import CheckViolation

from investment_domain import canonical_id
from investment_domain.nodes import (
    Calculation,
    CatalystImpact,
    Estimate,
    Metric,
    Security,
    Valuation,
)
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


def utc(year, month, day, hour=0):
    return datetime(year, month, day, hour, tzinfo=UTC)


def security(seed: str = "core") -> Security:
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
    seed: str,
    unit: str,
    currency: str | None,
) -> Estimate:
    payload = {
        "subject_id": subject_id,
        "metric_name": metric_name,
        "period_end": period_end,
        "value": Decimal(value),
        "unit": unit,
        "scenario": "BASE",
        "model_version": "forecast-v1",
        "as_of": utc(2026, 9, 27, 10),
        "currency": currency,
    }
    return Estimate(
        id=canonical_id("estimate", payload),
        **payload,
    )


def provenance_metric(
    *,
    seed: str,
    value: str,
) -> Metric:
    values = {
        "subject_id": f"company:provenance:{seed}",
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

    values["id"] = canonical_id(
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

    return Metric(**values)


def provenance_calculation(
    *,
    left: Metric,
    right: Metric,
) -> Calculation:
    values = {
        "subject_id": left.subject_id,
        "formula": "SUB(REF(0),REF(1))",
        "input_ids": (left.id, right.id),
        "value": left.value - right.value,
        "unit": "currency",
        "currency": "USD",
        "model_version": "cel-v1",
    }

    values["id"] = canonical_id(
        "calculation",
        {
            "subject_id": values["subject_id"],
            "formula": values["formula"],
            "input_ids": values["input_ids"],
            "model_version": values["model_version"],
        },
    )

    return Calculation(**values)


def provenance_impact(
    *,
    seed: str,
) -> CatalystImpact:
    values = {
        "catalyst_id": f"catalyst:{seed}",
        "target_id": f"claim:{seed}",
        "direction": "POSITIVE",
        "magnitude": "HIGH",
        "probability": Decimal("0.75"),
        "confidence": Decimal("0.80"),
        "horizon": "NEAR_TERM",
        "rationale": f"Valuation provenance {seed}",
        "as_of": utc(2026, 9, 27, 9),
        "created_by": "test-agent",
    }

    return CatalystImpact(
        id=canonical_id(
            "catalyst_impact",
            values,
        ),
        **values,
    )


def expected_dcf_value() -> Decimal:
    """Independent oracle for the deterministic two-period DCF fixture."""
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


def valuation(
    *,
    security_id: str,
    value: str,
    seed: str,
) -> Valuation:
    payload = {
        "security_id": security_id,
        "method": "DCF",
        "value": Decimal(value),
        "currency": "USD",
        "as_of": utc(2026, 9, 27, 12),
        "model_version": "dcf-v1",
        "scenario": "BASE",
    }
    return Valuation(
        id=canonical_id("valuation", payload),
        **payload,
    )


@pytest.fixture
def repo():
    PostgreSQLMigrationManager(DSN).migrate()
    repository = PostgreSQLEvidenceRepository(DSN)

    def clean():
        with repository.connect() as con:
            with con.transaction():
                con.execute("DELETE FROM domain_edges")
                con.execute("DELETE FROM recommendation_facts")
                con.execute("DELETE FROM risk_facts")
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


def persist_valid_dcf(
    repo,
    *,
    seed: str,
    materialized_value: str | Decimal | None = None,
    omit_role: str | None = None,
    extra_dependency_ids: tuple[str, ...] = (),
):
    node_security = security(seed)

    fcf_1 = estimate(
        subject_id=node_security.company_id,
        metric_name="financial.free_cash_flow",
        value="100",
        period_end=utc(2027, 12, 31),
        seed=f"{seed}-fcf1",
        unit="currency",
        currency="USD",
    )
    fcf_2 = estimate(
        subject_id=node_security.company_id,
        metric_name="financial.free_cash_flow",
        value="110",
        period_end=utc(2028, 12, 31),
        seed=f"{seed}-fcf2",
        unit="currency",
        currency="USD",
    )
    wacc = estimate(
        subject_id=node_security.company_id,
        metric_name="valuation.wacc",
        value="0.10",
        period_end=utc(2026, 9, 27),
        seed=f"{seed}-wacc",
        unit="ratio",
        currency=None,
    )
    growth = estimate(
        subject_id=node_security.company_id,
        metric_name="valuation.terminal_growth_rate",
        value="0",
        period_end=utc(2026, 9, 27),
        seed=f"{seed}-growth",
        unit="ratio",
        currency=None,
    )
    net_debt = estimate(
        subject_id=node_security.company_id,
        metric_name="financial.net_debt",
        value="10",
        period_end=utc(2026, 9, 27),
        seed=f"{seed}-debt",
        unit="currency",
        currency="USD",
    )
    shares = estimate(
        subject_id=node_security.id,
        metric_name="market.diluted_shares_outstanding",
        value="100",
        period_end=utc(2026, 9, 27),
        seed=f"{seed}-shares",
        unit="shares",
        currency=None,
    )

    roles = {
        "fcf_1": fcf_1,
        "fcf_2": fcf_2,
        "wacc": wacc,
        "growth": growth,
        "net_debt": net_debt,
        "shares": shares,
    }

    dependencies = tuple(
        node
        for role, node in roles.items()
        if role != omit_role
    )

    if materialized_value is None:
        materialized_value = expected_dcf_value()

    node_valuation = valuation(
        security_id=node_security.id,
        value=str(materialized_value),
        seed=seed,
    )

    repo.add_security(node_security)

    for node in dependencies:
        repo.add_estimate(node)

    repo.add_valuation(node_valuation)
    repo.add_valuation_dependencies(
        node_valuation.id,
        tuple(node.id for node in dependencies)
        + extra_dependency_ids,
    )

    return node_valuation, node_security, roles



def test_verify_valuation_reproduces_exact_persisted_dcf(repo):
    node_valuation, _, _ = persist_valid_dcf(
        repo,
        seed="happy",
    )

    verified = repo.verify_valuation(node_valuation.id)

    assert verified == node_valuation


def test_verify_valuation_rejects_materialized_value_mismatch(repo):
    node_valuation, _, _ = persist_valid_dcf(
        repo,
        seed="value-mismatch",
        materialized_value="999",
    )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R557: VALUATION_REPRODUCIBILITY_FAILURE",
    ) as exc_info:
        repo.verify_valuation(node_valuation.id)

    assert exc_info.value.__cause__ is not None
    assert "IDM-V020: DCF_MATERIALIZED_VALUE_MISMATCH" in str(
        exc_info.value.__cause__
    )


def test_verify_valuation_rejects_missing_required_role(repo):
    node_valuation, _, _ = persist_valid_dcf(
        repo,
        seed="missing-wacc",
        materialized_value="0",
        omit_role="wacc",
    )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R557: VALUATION_REPRODUCIBILITY_FAILURE",
    ) as exc_info:
        repo.verify_valuation(node_valuation.id)

    assert exc_info.value.__cause__ is not None
    assert "IDM-V004: DCF_REQUIRED_ROLE_MISSING:wacc" in str(
        exc_info.value.__cause__
    )


def test_verify_valuation_rejects_missing_security(repo):
    node_valuation, node_security, _ = persist_valid_dcf(
        repo,
        seed="missing-security",
    )

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                DELETE FROM domain_nodes
                WHERE id = %s
                """,
                (node_security.id,),
            )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R555: VALUATION_SECURITY_NOT_FOUND",
    ):
        repo.verify_valuation(node_valuation.id)



def test_verify_valuation_rejects_corrupt_estimate_dependency(repo):
    node_valuation, _, roles = persist_valid_dcf(
        repo,
        seed="corrupt-estimate",
    )
    corrupt = roles["fcf_1"]

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE estimate_facts
                SET value = value + 1
                WHERE node_id = %s
                """,
                (corrupt.id,),
            )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R554: VALUATION_DEPENDENCY_INTEGRITY_FAILURE",
    ):
        repo.verify_valuation(node_valuation.id)


def test_valuation_dependency_type_is_rejected_by_db_invariant(repo):
    node_valuation, _, roles = persist_valid_dcf(
        repo,
        seed="unsupported-edge-type",
    )
    dependency = roles["fcf_1"]

    with pytest.raises(CheckViolation):
        with repo.connect() as con:
            with con.transaction():
                con.execute(
                    """
                    UPDATE domain_edges
                    SET target_type = 'Security'
                    WHERE source_id = %s
                      AND source_type = 'Valuation'
                      AND edge_type = 'DEPENDS_ON'
                      AND target_id = %s
                    """,
                    (
                        node_valuation.id,
                        dependency.id,
                    ),
                )


def test_verify_valuation_is_exact_not_pit_edge_timestamp_filtered(repo):
    node_valuation, _, _ = persist_valid_dcf(
        repo,
        seed="exact-not-pit",
    )

    future_edge_time = utc(2035, 1, 1)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE domain_edges
                SET created_at = %s
                WHERE source_id = %s
                  AND source_type = 'Valuation'
                  AND edge_type = 'DEPENDS_ON'
                """,
                (
                    future_edge_time,
                    node_valuation.id,
                ),
            )

    verified = repo.verify_valuation(node_valuation.id)

    assert verified == node_valuation



def test_verify_valuation_rejects_corrupt_metric_provenance(repo):
    extra = provenance_metric(
        seed="metric-provenance",
        value="500",
    )
    repo.add_metric(extra)

    node_valuation, _, _ = persist_valid_dcf(
        repo,
        seed="metric-provenance",
        extra_dependency_ids=(extra.id,),
    )

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE domain_nodes
                SET payload_hash = %s
                WHERE id = %s
                """,
                ("0" * 64, extra.id),
            )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R554: VALUATION_DEPENDENCY_INTEGRITY_FAILURE",
    ):
        repo.verify_valuation(node_valuation.id)




def test_verify_valuation_rejects_corrupt_catalyst_impact_provenance(repo):
    extra = provenance_impact(
        seed="impact-provenance",
    )
    repo.add_catalyst_impact(extra)

    node_valuation, _, _ = persist_valid_dcf(
        repo,
        seed="impact-provenance",
        extra_dependency_ids=(extra.id,),
    )

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE catalyst_impact_facts
                SET rationale = %s
                WHERE node_id = %s
                """,
                (
                    "CORRUPTED PROJECTION",
                    extra.id,
                ),
            )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R554: VALUATION_DEPENDENCY_INTEGRITY_FAILURE",
    ):
        repo.verify_valuation(node_valuation.id)




def test_verify_valuation_recursively_rejects_corrupt_calculation_input(repo):
    left = provenance_metric(
        seed="calculation-left",
        value="10",
    )
    right = provenance_metric(
        seed="calculation-right",
        value="3",
    )

    repo.add_metric(left)
    repo.add_metric(right)

    calc = provenance_calculation(
        left=left,
        right=right,
    )

    repo.add_calculation(calc)
    repo.add_calculation_inputs(calc.id)

    node_valuation, _, _ = persist_valid_dcf(
        repo,
        seed="calculation-provenance",
        extra_dependency_ids=(calc.id,),
    )

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE metric_facts
                SET value = %s
                WHERE node_id = %s
                """,
                (
                    Decimal("999"),
                    left.id,
                ),
            )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R554: VALUATION_DEPENDENCY_INTEGRITY_FAILURE",
    ):
        repo.verify_valuation(node_valuation.id)



def test_verify_valuation_rejects_noncanonical_id_before_db(repo):
    with pytest.raises(
        ValueError,
        match="valuation_id must be a canonical Valuation ID",
    ):
        repo.verify_valuation("valuation:not-canonical")
