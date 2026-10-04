import os
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from investment_domain import (
    Calculation,
    CatalystImpact,
    Estimate,
    Forecast,
    Metric,
    Security,
    canonical_id,
)
from investment_domain.postgres_repository import (
    PostgreSQLEvidenceRepository,
    RepositoryReadError,
)


DSN = os.environ.get("IDM_TEST_POSTGRES_DSN")

pytestmark = pytest.mark.skipif(
    not DSN,
    reason="IDM_TEST_POSTGRES_DSN is not configured",
)


def utc(year, month, day, hour=0):
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


def forecast(seed: str, *, as_of=None) -> Forecast:
    values = {
        "subject_id": f"security:test:{seed}",
        "scenario": "BASE",
        "as_of": as_of or utc(2026, 9, 27, 10),
        "model_version": "forecast-v1",
    }
    return Forecast(
        id=canonical_id("forecast", values),
        **values,
    )


def catalyst_impact(seed: str, *, as_of=None) -> CatalystImpact:
    values = {
        "catalyst_id": f"catalyst:test:{seed}",
        "target_id": f"security:test:{seed}",
        "direction": "POSITIVE",
        "magnitude": "MEDIUM",
        "probability": Decimal("0.70"),
        "confidence": Decimal("0.80"),
        "horizon": "MEDIUM_TERM",
        "rationale": f"R554 {seed}",
        "as_of": as_of or utc(2026, 9, 27, 10),
        "created_by": "R554",
    }
    return CatalystImpact(
        id=canonical_id("catalyst_impact", values),
        **values,
    )


def metric(seed: str, *, published_at=None, ingested_at=None) -> Metric:
    values = {
        "subject_id": f"company:test:{seed}",
        "name": "financial.free_cash_flow",
        "period_start": utc(2026, 1, 1),
        "period_end": utc(2026, 12, 31),
        "effective_at": utc(2026, 9, 27, 8),
        "observed_at": utc(2026, 9, 27, 8),
        "published_at": published_at or utc(2026, 9, 27, 9),
        "source_id": f"source:test:{seed}",
        "source_version": "r554-v1",
        "ingested_at": ingested_at or utc(2026, 9, 27, 10),
        "value": Decimal("100"),
        "unit": "currency",
        "currency": "USD",
    }
    metric_id = canonical_id(
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
    return Metric(
        id=metric_id,
        **values,
    )


@pytest.fixture
def repo():
    repository = PostgreSQLEvidenceRepository(DSN)

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

    yield repository

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




def estimate(seed: str, *, as_of=None) -> Estimate:
    values = {
        "subject_id": f"company:test:{seed}",
        "metric_name": "financial.free_cash_flow",
        "period_end": utc(2027, 12, 31),
        "value": Decimal("100"),
        "unit": "currency",
        "scenario": "BASE",
        "model_version": "forecast-v1",
        "as_of": as_of or utc(2026, 9, 27, 10),
        "currency": "USD",
    }
    return Estimate(
        id=canonical_id("estimate", values),
        **values,
    )


def test_mixed_five_type_resolution_is_exact_and_deterministic(repo):
    estimate_node = estimate("mixed-estimate")
    forecast_node = forecast("mixed-forecast")
    catalyst_node = catalyst_impact("mixed-catalyst")
    metric_node = metric("mixed-metric")

    repo.add_estimate(estimate_node)
    repo.add_forecast(forecast_node)
    repo.add_catalyst_impact(catalyst_node)
    repo.add_metric(metric_node)

    calculation_values = {
        "subject_id": metric_node.subject_id,
        "formula": "ADD(REF(0),CONST(1))",
        "input_ids": (metric_node.id,),
        "value": Decimal("101"),
        "unit": "currency",
        "currency": "USD",
        "model_version": "cel-v1",
    }
    calculation_identity = {
        "subject_id": calculation_values["subject_id"],
        "formula": calculation_values["formula"],
        "input_ids": calculation_values["input_ids"],
        "model_version": calculation_values["model_version"],
    }
    calculation_node = Calculation(
        id=canonical_id("calculation", calculation_identity),
        **calculation_values,
    )

    repo.add_calculation(calculation_node)
    repo.add_calculation_inputs(calculation_node.id)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE domain_edges
                SET created_at = %s
                WHERE source_id = %s
                  AND source_type = 'Calculation'
                  AND edge_type = 'DERIVED_FROM'
                  AND target_id = %s
                  AND target_type = 'Metric'
                """,
                (
                    utc(2026, 9, 27, 11),
                    calculation_node.id,
                    metric_node.id,
                ),
            )

    nodes = (
        estimate_node,
        forecast_node,
        catalyst_node,
        metric_node,
        calculation_node,
    )
    by_id = {node.id: node for node in nodes}
    expected_ids = tuple(sorted(by_id))

    request_a = tuple(node.id for node in nodes)
    request_b = tuple(reversed(request_a))

    result_a = repo.valuation_inputs_by_ids_at(
        request_a,
        utc(2026, 9, 27, 12),
    )
    result_b = repo.valuation_inputs_by_ids_at(
        request_b,
        utc(2026, 9, 27, 12),
    )

    assert tuple(node.id for node in result_a) == expected_ids
    assert tuple(node.id for node in result_b) == expected_ids
    assert result_a == result_b
    assert result_a == tuple(by_id[node_id] for node_id in expected_ids)


def test_missing_dependency_id_fails_closed(repo):
    missing_id = canonical_id(
        "estimate",
        {
            "subject_id": "company:test:missing",
            "metric_name": "financial.free_cash_flow",
            "period_end": utc(2027, 12, 31),
            "value": Decimal("100"),
            "unit": "currency",
            "scenario": "BASE",
            "model_version": "forecast-v1",
            "as_of": utc(2026, 9, 27, 10),
            "currency": "USD",
        },
    )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R550: VALUATION_INPUT_NOT_VISIBLE_AT_CUTOFF",
    ):
        repo.valuation_inputs_by_ids_at(
            (missing_id,),
            utc(2026, 9, 27, 12),
        )


def test_unsupported_security_dependency_fails_closed(repo):
    node = Security(
        id="security:nasdaq:r554",
        company_id="company:test-r554-unsupported",
        venue="NASDAQ",
        ticker="R554",
        currency="USD",
    )
    repo.add_security(node)

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R550: VALUATION_INPUT_NOT_VISIBLE_AT_CUTOFF",
    ):
        repo.valuation_inputs_by_ids_at(
            (node.id,),
            utc(2026, 9, 27, 12),
        )


def test_corrupt_metric_dependency_is_normalized_to_r550(repo):
    node = metric("integrity-corrupt")
    repo.add_metric(node)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE domain_nodes
                SET canonical_payload = %s
                WHERE id = %s
                """,
                ('{"corrupt":true}', node.id),
            )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R550: VALUATION_INPUT_NOT_VISIBLE_AT_CUTOFF",
    ):
        repo.valuation_inputs_by_ids_at(
            (node.id,),
            utc(2026, 9, 27, 12),
        )


def test_resolves_exact_forecast_at_cutoff(repo):
    node = forecast("forecast-visible")
    repo.add_forecast(node)

    result = repo.valuation_inputs_by_ids_at(
        (node.id,),
        utc(2026, 9, 27, 12),
    )

    assert result == (node,)


def test_future_forecast_fails_closed(repo):
    node = forecast(
        "forecast-future",
        as_of=utc(2026, 9, 27, 13),
    )
    repo.add_forecast(node)

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R550: VALUATION_INPUT_NOT_VISIBLE_AT_CUTOFF",
    ):
        repo.valuation_inputs_by_ids_at(
            (node.id,),
            utc(2026, 9, 27, 12),
        )


def test_resolves_exact_catalyst_impact_at_cutoff(repo):
    node = catalyst_impact("catalyst-visible")
    repo.add_catalyst_impact(node)

    result = repo.valuation_inputs_by_ids_at(
        (node.id,),
        utc(2026, 9, 27, 12),
    )

    assert result == (node,)


def test_future_catalyst_impact_fails_closed(repo):
    node = catalyst_impact(
        "catalyst-future",
        as_of=utc(2026, 9, 27, 13),
    )
    repo.add_catalyst_impact(node)

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R550: VALUATION_INPUT_NOT_VISIBLE_AT_CUTOFF",
    ):
        repo.valuation_inputs_by_ids_at(
            (node.id,),
            utc(2026, 9, 27, 12),
        )



def test_exact_metric_id_is_not_replaced_by_newer_revision(repo):
    original = metric("exact-revision-root")

    successor_values = {
        "subject_id": original.subject_id,
        "name": original.name,
        "period_start": original.period_start,
        "period_end": original.period_end,
        "effective_at": original.effective_at,
        "observed_at": utc(2026, 9, 27, 9),
        "published_at": utc(2026, 9, 27, 10),
        "source_id": original.source_id,
        "source_version": "r554-v2",
        "ingested_at": utc(2026, 9, 27, 11),
        "value": Decimal("125"),
        "unit": original.unit,
        "currency": original.currency,
        "supersedes_id": original.id,
    }

    successor_identity = {
        "subject_id": successor_values["subject_id"],
        "name": successor_values["name"],
        "period_start": successor_values["period_start"],
        "period_end": successor_values["period_end"],
        "effective_at": successor_values["effective_at"],
        "observed_at": successor_values["observed_at"],
        "published_at": successor_values["published_at"],
        "source_id": successor_values["source_id"],
        "source_version": successor_values["source_version"],
    }

    successor = Metric(
        id=canonical_id("metric", successor_identity),
        **successor_values,
    )

    repo.add_metric(original)
    repo.add_metric(successor)

    cutoff = utc(2026, 9, 27, 12)

    result = repo.valuation_inputs_by_ids_at(
        (original.id,),
        cutoff,
    )

    assert result == (original,)
    assert result[0].id == original.id
    assert result[0].id != successor.id
    assert result[0].value == Decimal("100")


def test_resolves_exact_metric_when_available_at_cutoff(repo):
    node = metric("metric-visible")
    repo.add_metric(node)

    result = repo.valuation_inputs_by_ids_at(
        (node.id,),
        utc(2026, 9, 27, 12),
    )

    assert result == (node,)


@pytest.mark.parametrize(
    ("published_at", "ingested_at"),
    [
        (utc(2026, 9, 27, 13), utc(2026, 9, 27, 14)),
        (utc(2026, 9, 27, 9), utc(2026, 9, 27, 13)),
    ],
)
def test_unavailable_metric_fails_closed(
    repo,
    published_at,
    ingested_at,
):
    node = metric(
        "metric-future",
        published_at=published_at,
        ingested_at=ingested_at,
    )
    repo.add_metric(node)

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R550: VALUATION_INPUT_NOT_VISIBLE_AT_CUTOFF",
    ):
        repo.valuation_inputs_by_ids_at(
            (node.id,),
            utc(2026, 9, 27, 12),
        )


def test_resolves_calculation_when_provenance_is_visible(repo):
    metric_node = metric("calculation-visible")
    repo.add_metric(metric_node)

    values = {
        "subject_id": metric_node.subject_id,
        "formula": "ADD(REF(0),CONST(1))",
        "input_ids": (metric_node.id,),
        "value": Decimal("101"),
        "unit": "currency",
        "currency": "USD",
        "model_version": "cel-v1",
    }
    identity = {
        "subject_id": values["subject_id"],
        "formula": values["formula"],
        "input_ids": values["input_ids"],
        "model_version": values["model_version"],
    }
    calculation = Calculation(
        id=canonical_id("calculation", identity),
        **values,
    )

    repo.add_calculation(calculation)
    repo.add_calculation_inputs(calculation.id)

    # add_calculation_inputs() assigns repository-owned CURRENT_TIMESTAMP.
    # Pin historical materialization time so this PIT assertion is
    # independent of the wall-clock time at which the test executes.
    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE domain_edges
                SET created_at = %s
                WHERE source_id = %s
                  AND source_type = 'Calculation'
                  AND edge_type = 'DERIVED_FROM'
                  AND target_id = %s
                  AND target_type = 'Metric'
                """,
                (
                    utc(2026, 9, 27, 11),
                    calculation.id,
                    metric_node.id,
                ),
            )

    result = repo.valuation_inputs_by_ids_at(
        (calculation.id,),
        utc(2026, 9, 27, 12),
    )

    assert result == (calculation,)


def test_future_calculation_provenance_edge_fails_closed(repo):
    metric_node = metric("calculation-future-edge")
    repo.add_metric(metric_node)

    values = {
        "subject_id": metric_node.subject_id,
        "formula": "ADD(REF(0),CONST(1))",
        "input_ids": (metric_node.id,),
        "value": Decimal("101"),
        "unit": "currency",
        "currency": "USD",
        "model_version": "cel-v1",
    }
    identity = {
        "subject_id": values["subject_id"],
        "formula": values["formula"],
        "input_ids": values["input_ids"],
        "model_version": values["model_version"],
    }
    calculation = Calculation(
        id=canonical_id("calculation", identity),
        **values,
    )

    repo.add_calculation(calculation)
    repo.add_calculation_inputs(calculation.id)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE domain_edges
                SET created_at = %s
                WHERE source_id = %s
                  AND source_type = 'Calculation'
                  AND edge_type = 'DERIVED_FROM'
                  AND target_id = %s
                """,
                (
                    utc(2026, 9, 27, 13),
                    calculation.id,
                    metric_node.id,
                ),
            )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R550: VALUATION_INPUT_NOT_VISIBLE_AT_CUTOFF",
    ):
        repo.valuation_inputs_by_ids_at(
            (calculation.id,),
            utc(2026, 9, 27, 12),
        )
