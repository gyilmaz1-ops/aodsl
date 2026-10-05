from __future__ import annotations

import json

import os
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from investment_domain import canonical_id
from investment_domain.nodes import (
    CatalystImpact,
    Estimate,
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


def valuation(seed: str = "resolution") -> Valuation:
    payload = {
        "security_id": "security:nasdaq:nvda",
        "method": "DCF",
        "value": Decimal("200"),
        "currency": "USD",
        "as_of": utc(2026, 9, 27, 12),
        "model_version": f"model-{seed}",
        "scenario": "BASE",
    }
    return Valuation(
        id=canonical_id("valuation", payload),
        **payload,
    )


def estimate(seed: str = "resolution") -> Estimate:
    payload = {
        "subject_id": "company:nvidia",
        "metric_name": "financial.revenue",
        "period_end": utc(2027, 1, 31),
        "value": Decimal("200000000000"),
        "unit": "currency",
        "scenario": "BASE",
        "model_version": f"estimate-{seed}",
        "as_of": utc(2026, 9, 26, 12),
        "currency": "USD",
    }
    return Estimate(
        id=canonical_id("estimate", payload),
        **payload,
    )


def catalyst_impact(
    seed: str = "resolution",
    *,
    as_of=None,
) -> CatalystImpact:
    payload = {
        "catalyst_id": canonical_id(
            "catalyst",
            {"seed": seed},
        ),
        "target_id": canonical_id(
            "claim",
            {"seed": seed},
        ),
        "direction": "POSITIVE",
        "magnitude": "HIGH",
        "probability": Decimal("0.75"),
        "confidence": Decimal("0.80"),
        "horizon": "NEAR_TERM",
        "rationale": f"Catalyst impact assessment {seed}",
        "as_of": as_of or utc(2026, 9, 27, 10),
        "created_by": "test-agent",
    }
    return CatalystImpact(
        id=canonical_id("catalyst_impact", payload),
        **payload,
    )


@pytest.fixture
def repo():
    PostgreSQLMigrationManager(DSN).migrate()
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



def pin_valuation_dependency_edges_before_cutoff(
    repo,
    valuation_id: str,
) -> None:
    """Normalize repository-owned edge time for PIT fixtures."""
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
                    utc(2026, 9, 27, 11),
                    valuation_id,
                ),
            )


def persist_dependency_graph(repo, *, impact_as_of=None):
    node_valuation = valuation()
    node_estimate = estimate()
    node_impact = catalyst_impact(
        as_of=impact_as_of,
    )

    repo.add_valuation(node_valuation)
    repo.add_estimate(node_estimate)
    repo.add_catalyst_impact(node_impact)

    repo.add_valuation_dependencies(
        node_valuation.id,
        (
            node_estimate.id,
            node_impact.id,
        ),
    )
    pin_valuation_dependency_edges_before_cutoff(
        repo,
        node_valuation.id,
    )

    return (
        node_valuation,
        node_estimate,
        node_impact,
    )


def test_valuation_inputs_at_resolves_typed_dependency_nodes(repo):
    node_valuation, node_estimate, node_impact = (
        persist_dependency_graph(repo)
    )

    inputs = repo.valuation_inputs_at(
        node_valuation.id,
        utc(2026, 9, 27, 12),
    )

    assert set(inputs) == {
        node_estimate,
        node_impact,
    }

    assert any(
        isinstance(node, Estimate)
        for node in inputs
    )
    assert any(
        isinstance(node, CatalystImpact)
        for node in inputs
    )


def test_valuation_inputs_at_is_deterministic_by_dependency_id(repo):
    node_valuation, node_estimate, node_impact = (
        persist_dependency_graph(repo)
    )

    first = repo.valuation_inputs_at(
        node_valuation.id,
        utc(2026, 9, 27, 12),
    )
    second = repo.valuation_inputs_at(
        node_valuation.id,
        utc(2026, 9, 27, 12),
    )

    expected_ids = tuple(
        sorted(
            (
                node_estimate.id,
                node_impact.id,
            )
        )
    )

    assert tuple(node.id for node in first) == expected_ids
    assert first == second


def test_future_catalyst_impact_dependency_fails_closed(repo):
    node_valuation, _, _ = persist_dependency_graph(
        repo,
        impact_as_of=utc(2026, 9, 28, 12),
    )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R550: VALUATION_INPUT_NOT_VISIBLE_AT_CUTOFF",
    ):
        repo.valuation_inputs_at(
            node_valuation.id,
            utc(2026, 9, 27, 12),
        )


def test_missing_valuation_fails_closed(repo):
    missing_id = canonical_id(
        "valuation",
        {"seed": "missing"},
    )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R545: VALUATION_NOT_FOUND",
    ):
        repo.valuation_inputs_at(
            missing_id,
            utc(2026, 9, 27, 12),
        )


def test_valuation_inputs_at_rejects_wrong_valuation_anchor_type(repo):
    node = valuation("r546-public-regression")
    repo.add_valuation(node)

    try:
        with repo.connect() as con:
            with con.transaction():
                con.execute(
                    """
                    ALTER TABLE valuation_facts
                    DROP CONSTRAINT valuation_domain_node_fk
                    """
                )
                con.execute(
                    """
                    UPDATE domain_nodes
                    SET node_type = 'Claim'
                    WHERE id = %s
                    """,
                    (node.id,),
                )

        with pytest.raises(
            RepositoryReadError,
            match="IDM-R546: VALUATION_TYPE_MISMATCH",
        ):
            repo.valuation_inputs_at(
                node.id,
                utc(2026, 9, 30, 12),
            )

    finally:
        with repo.connect() as con:
            with con.transaction():
                con.execute(
                    """
                    UPDATE domain_nodes
                    SET node_type = 'Valuation'
                    WHERE id = %s
                    """,
                    (node.id,),
                )
                con.execute(
                    """
                    ALTER TABLE valuation_facts
                    ADD CONSTRAINT valuation_domain_node_fk
                    FOREIGN KEY (node_id, node_type)
                    REFERENCES domain_nodes(id, node_type)
                    ON DELETE RESTRICT
                    """
                )


def test_naive_research_cutoff_is_rejected(repo):
    node_valuation, _, _ = persist_dependency_graph(repo)

    with pytest.raises(
        ValueError,
        match="research_cutoff must be timezone-aware",
    ):
        repo.valuation_inputs_at(
            node_valuation.id,
            datetime(2026, 9, 27, 12),
        )


# ============================================================
# FULL VALUATION INPUT VOCABULARY — RED CONTRACT
# ============================================================

from investment_domain.nodes import (
    Calculation,
    Forecast,
    Metric,
)


def _forecast_id(values):
    return canonical_id(
        "forecast",
        {
            "subject_id": values["subject_id"],
            "scenario": values["scenario"],
            "as_of": values["as_of"],
            "model_version": values["model_version"],
        },
    )


def forecast(seed: str = "resolver-forecast") -> Forecast:
    values = {
        "subject_id": "security:nasdaq:nvda",
        "scenario": "BASE",
        "as_of": utc(2026, 9, 26, 11),
        "model_version": f"forecast-{seed}",
    }
    values["id"] = _forecast_id(values)
    return Forecast(**values)


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


def metric(seed: str = "resolver-metric") -> Metric:
    values = {
        "subject_id": f"company:{seed}",
        "name": "financial.revenue",
        "value": Decimal("100.00"),
        "unit": "currency",
        "currency": "USD",
        "period_start": utc(2026, 4, 1),
        "period_end": utc(2026, 6, 30),
        "effective_at": utc(2026, 6, 30),
        "observed_at": utc(2026, 9, 20),
        "published_at": utc(2026, 9, 21),
        "ingested_at": utc(2026, 9, 22),
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
    input_id: str,
    seed: str = "resolver-calculation",
) -> Calculation:
    values = {
        "subject_id": f"company:{seed}",
        "formula": "ADD(REF(0),CONST(1))",
        "input_ids": (input_id,),
        "value": Decimal("101.00"),
        "unit": "currency",
        "currency": "USD",
        "model_version": "cel-v1",
    }
    values["id"] = _calculation_id(values)
    return Calculation(**values)


def test_valuation_inputs_at_resolves_forecast(repo):
    node_valuation = valuation("forecast-input")
    node_forecast = forecast()

    repo.add_valuation(node_valuation)
    repo.add_forecast(node_forecast)
    repo.add_valuation_dependencies(
        node_valuation.id,
        (node_forecast.id,),
    )
    pin_valuation_dependency_edges_before_cutoff(
        repo,
        node_valuation.id,
    )

    inputs = repo.valuation_inputs_at(
        node_valuation.id,
        utc(2026, 9, 27, 12),
    )

    assert inputs == (node_forecast,)
    assert isinstance(inputs[0], Forecast)


def test_valuation_inputs_at_resolves_metric(repo):
    node_valuation = valuation("metric-input")
    node_metric = metric()

    repo.add_valuation(node_valuation)
    repo.add_metric(node_metric)
    repo.add_valuation_dependencies(
        node_valuation.id,
        (node_metric.id,),
    )
    pin_valuation_dependency_edges_before_cutoff(
        repo,
        node_valuation.id,
    )

    inputs = repo.valuation_inputs_at(
        node_valuation.id,
        utc(2026, 9, 27, 12),
    )

    assert inputs == (node_metric,)
    assert isinstance(inputs[0], Metric)


def test_valuation_inputs_at_resolves_calculation(repo):
    node_valuation = valuation("calculation-input")
    node_metric = metric("calculation-source")
    node_calculation = calculation(node_metric.id)

    repo.add_metric(node_metric)
    repo.add_calculation(node_calculation)
    repo.add_calculation_inputs(node_calculation.id)

    # Test fixture normalization:
    # add_calculation_inputs() assigns repository-owned
    # materialization time. Pin it before the PIT cutoff.
    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE domain_edges
                SET created_at = %s
                WHERE source_id = %s
                  AND source_type = 'Calculation'
                  AND edge_type = 'DERIVED_FROM'
                """,
                (
                    utc(2026, 9, 27, 11),
                    node_calculation.id,
                ),
            )

    repo.add_valuation(node_valuation)
    repo.add_valuation_dependencies(
        node_valuation.id,
        (node_calculation.id,),
    )
    pin_valuation_dependency_edges_before_cutoff(
        repo,
        node_valuation.id,
    )

    inputs = repo.valuation_inputs_at(
        node_valuation.id,
        utc(2026, 9, 27, 12),
    )

    assert inputs == (node_calculation,)
    assert isinstance(inputs[0], Calculation)

def test_future_valuation_anchor_fails_closed(repo):
    node_valuation = valuation("future-anchor")

    repo.add_valuation(node_valuation)

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R551: VALUATION_NOT_VISIBLE_AT_CUTOFF",
    ):
        repo.valuation_inputs_at(
            node_valuation.id,
            utc(2026, 9, 27, 11),
        )

def test_future_valuation_dependency_edge_fails_closed(repo):
    node_metric = metric("future-valuation-dependency-edge")
    node_valuation = valuation("future-valuation-dependency-edge")
    cutoff = utc(2026, 9, 27, 12)

    repo.add_metric(node_metric)
    repo.add_valuation(node_valuation)
    repo.add_valuation_dependencies(
        node_valuation.id,
        (node_metric.id,),
    )

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE domain_edges
                SET created_at = %s
                WHERE source_id = %s
                  AND source_type = 'Valuation'
                  AND edge_type = 'DEPENDS_ON'
                  AND target_id = %s
                """,
                (
                    utc(2026, 9, 27, 13),
                    node_valuation.id,
                    node_metric.id,
                ),
            )

    with pytest.raises(
        RepositoryReadError,
        match=(
            "IDM-R552: "
            "VALUATION_DEPENDENCY_EDGE_NOT_VISIBLE_AT_CUTOFF"
        ),
    ):
        repo.valuation_inputs_at(
            node_valuation.id,
            cutoff,
        )


def test_valuation_inputs_at_rejects_calculation_with_future_transitive_provenance(
    repo,
):
    node_valuation = valuation(
        "future-transitive-calculation-provenance"
    )
    cutoff = utc(2026, 9, 27, 12)

    leaf_metric = metric(
        "future-transitive-calculation-provenance"
    )

    child_calculation = calculation(
        leaf_metric.id,
        seed="future-transitive-child",
    )

    parent_calculation = calculation(
        child_calculation.id,
        seed="future-transitive-parent",
    )

    repo.add_metric(leaf_metric)

    repo.add_calculation(child_calculation)
    repo.add_calculation_inputs(child_calculation.id)

    repo.add_calculation(parent_calculation)
    repo.add_calculation_inputs(parent_calculation.id)

    repo.add_valuation(node_valuation)
    repo.add_valuation_dependencies(
        node_valuation.id,
        (parent_calculation.id,),
    )

    pin_valuation_dependency_edges_before_cutoff(
        repo,
        node_valuation.id,
    )

    # Normalize the complete Calculation provenance chain before
    # the PIT cutoff so the test has exactly one future condition.
    with repo.connect() as con:
        with con.transaction():
            normalized = con.execute(
                """
                UPDATE domain_edges
                SET created_at = %s
                WHERE source_id = ANY(%s)
                  AND source_type = 'Calculation'
                  AND edge_type = 'DERIVED_FROM'
                RETURNING source_id, target_id
                """,
                (
                    utc(2026, 9, 27, 11),
                    [
                        child_calculation.id,
                        parent_calculation.id,
                    ],
                ),
            ).fetchall()

            assert set(normalized) == {
                (child_calculation.id, leaf_metric.id),
                (parent_calculation.id, child_calculation.id),
            }

            future_edge = con.execute(
                """
                UPDATE domain_edges
                SET created_at = %s
                WHERE source_id = %s
                  AND source_type = 'Calculation'
                  AND edge_type = 'DERIVED_FROM'
                  AND target_id = %s
                  AND target_type = 'Metric'
                RETURNING source_id, target_id
                """,
                (
                    utc(2026, 9, 27, 13),
                    child_calculation.id,
                    leaf_metric.id,
                ),
            ).fetchall()

            assert future_edge == [
                (child_calculation.id, leaf_metric.id)
            ]

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R550: VALUATION_INPUT_NOT_VISIBLE_AT_CUTOFF",
    ):
        repo.valuation_inputs_at(
            node_valuation.id,
            cutoff,
        )


def test_valuation_inputs_at_rejects_missing_valuation_projection(
    repo,
):
    node = valuation("r547-public-regression")
    repo.add_valuation(node)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                "DELETE FROM valuation_facts WHERE node_id = %s",
                (node.id,),
            )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R547: VALUATION_PROJECTION_NOT_FOUND",
    ):
        repo.valuation_inputs_at(
            node.id,
            utc(2026, 9, 30, 12),
        )


def test_valuation_inputs_at_rejects_invalid_stored_valuation(
    repo,
):
    node = valuation("r548-public-regression")
    repo.add_valuation(node)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE valuation_facts
                SET method = ''
                WHERE node_id = %s
                """,
                (node.id,),
            )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R548: INVALID_STORED_VALUATION",
    ):
        repo.valuation_inputs_at(
            node.id,
            utc(2026, 9, 30, 12),
        )


def test_valuation_inputs_at_rejects_valuation_canonical_anchor_tampering(
    repo,
):
    node = valuation("r549-public-regression")
    repo.add_valuation(node)

    with repo.connect() as con:
        original = con.execute(
            """
            SELECT canonical_payload
            FROM domain_nodes
            WHERE id = %s
            """,
            (node.id,),
        ).fetchone()

        assert original is not None

        corrupted_payload = dict(original[0])
        corrupted_payload["value"] = "999.99"

        con.execute(
            """
            UPDATE domain_nodes
            SET canonical_payload = %s::jsonb
            WHERE id = %s
            """,
            (
                json.dumps(
                    corrupted_payload,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                node.id,
            ),
        )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R549: VALUATION_INTEGRITY_FAILURE",
    ):
        repo.valuation_inputs_at(
            node.id,
            utc(2026, 9, 30, 12),
        )
