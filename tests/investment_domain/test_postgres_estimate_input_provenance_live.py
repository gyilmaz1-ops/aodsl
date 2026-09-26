from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from investment_domain import canonical_id
from investment_domain.nodes import Estimate
from investment_domain.postgres_migrations import PostgreSQLMigrationManager
from investment_domain.postgres_repository import (
    PostgreSQLEvidenceRepository,
    RepositoryWriteError,
)


DSN = os.getenv("IDM_TEST_POSTGRES_DSN")

pytestmark = pytest.mark.skipif(
    not DSN,
    reason="IDM_TEST_POSTGRES_DSN is not configured",
)


def _estimate_id(
    *,
    subject_id="company:acme",
    metric_name="financial.revenue",
    period_end=None,
    value=Decimal("150"),
    unit="currency",
    scenario="BASE",
    model_version="estimate-v1",
    as_of=None,
    currency="USD",
):
    period_end = period_end or datetime(2027, 12, 31, tzinfo=timezone.utc)
    as_of = as_of or datetime(2026, 9, 26, tzinfo=timezone.utc)

    return canonical_id(
        "estimate",
        {
            "subject_id": subject_id,
            "metric_name": metric_name,
            "period_end": period_end,
            "value": value,
            "unit": unit,
            "scenario": scenario,
            "model_version": model_version,
            "as_of": as_of,
            "currency": currency,
        },
    )


def _estimate():
    period_end = datetime(2027, 12, 31, tzinfo=timezone.utc)
    as_of = datetime(2026, 9, 26, tzinfo=timezone.utc)

    return Estimate(
        id=_estimate_id(period_end=period_end, as_of=as_of),
        subject_id="company:acme",
        metric_name="financial.revenue",
        period_end=period_end,
        value=Decimal("150"),
        unit="currency",
        scenario="BASE",
        model_version="estimate-v1",
        as_of=as_of,
        currency="USD",
    )


@pytest.fixture
def repo():
    PostgreSQLMigrationManager(DSN).migrate()
    repository = PostgreSQLEvidenceRepository(DSN)

    estimate_id = _estimate().id
    owned_target_ids = (
        "metric:test-revenue",
        "calculation:test-growth",
        "claim:test-guidance",
        "evidence:not-valid-estimate-input",
    )

    def cleanup():
        with repository.connect() as con:
            with con.transaction():
                con.execute(
                    """
                    DELETE FROM domain_edges
                    WHERE source_id = %s
                    """,
                    (estimate_id,),
                )
                con.execute(
                    """
                    DELETE FROM estimate_facts
                    WHERE node_id = %s
                    """,
                    (estimate_id,),
                )
                con.execute(
                    """
                    DELETE FROM domain_nodes
                    WHERE id = %s
                    """,
                    (estimate_id,),
                )
                con.execute(
                    """
                    DELETE FROM domain_nodes
                    WHERE id = ANY(%s)
                    """,
                    (list(owned_target_ids),),
                )

    cleanup()
    yield repository
    cleanup()


def _insert_domain_node(repo, *, node_id, node_type):
    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                INSERT INTO domain_nodes (
                    id,
                    node_type,
                    canonical_payload,
                    payload_hash
                )
                VALUES (%s, %s, '{}'::jsonb, repeat('0', 64))
                ON CONFLICT (id) DO NOTHING
                """,
                (node_id, node_type),
            )


def _edge_rows(repo, estimate_id):
    with repo.connect() as con:
        return con.execute(
            """
            SELECT target_id, target_type
            FROM domain_edges
            WHERE source_id = %s
              AND source_type = 'Estimate'
              AND edge_type = 'DERIVED_FROM'
            ORDER BY target_id
            """,
            (estimate_id,),
        ).fetchall()


def test_add_estimate_inputs_persists_exact_dependency_set(repo):
    estimate = _estimate()
    repo.add_estimate(estimate)

    metric_id = "metric:test-revenue"
    calculation_id = "calculation:test-growth"
    claim_id = "claim:test-guidance"

    _insert_domain_node(repo, node_id=metric_id, node_type="Metric")
    _insert_domain_node(repo, node_id=calculation_id, node_type="Calculation")
    _insert_domain_node(repo, node_id=claim_id, node_type="Claim")

    repo.add_estimate_inputs(
        estimate.id,
        (metric_id, calculation_id, claim_id),
    )

    assert set(_edge_rows(repo, estimate.id)) == {
        (metric_id, "Metric"),
        (calculation_id, "Calculation"),
        (claim_id, "Claim"),
    }


def test_identical_estimate_input_replay_is_idempotent(repo):
    estimate = _estimate()
    repo.add_estimate(estimate)

    metric_id = "metric:test-revenue"
    _insert_domain_node(repo, node_id=metric_id, node_type="Metric")

    repo.add_estimate_inputs(estimate.id, (metric_id,))
    repo.add_estimate_inputs(estimate.id, (metric_id,))

    assert _edge_rows(repo, estimate.id) == [(metric_id, "Metric")]


def test_missing_estimate_source_fails_closed(repo):
    estimate_id = _estimate().id

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W507: EDGE_SOURCE_NOT_FOUND",
    ):
        repo.add_estimate_inputs(
            estimate_id,
            ("metric:test-revenue",),
        )


def test_missing_dependency_fails_without_partial_edge_set(repo):
    estimate = _estimate()
    repo.add_estimate(estimate)

    metric_id = "metric:test-revenue"
    missing_id = "claim:missing"

    _insert_domain_node(repo, node_id=metric_id, node_type="Metric")

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W508: EDGE_TARGET_NOT_FOUND",
    ):
        repo.add_estimate_inputs(
            estimate.id,
            (metric_id, missing_id),
        )

    assert _edge_rows(repo, estimate.id) == []


def test_invalid_dependency_type_is_rejected(repo):
    estimate = _estimate()
    repo.add_estimate(estimate)

    evidence_id = "evidence:not-valid-estimate-input"
    _insert_domain_node(repo, node_id=evidence_id, node_type="Evidence")

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W510: EDGE_TARGET_TYPE_MISMATCH",
    ):
        repo.add_estimate_inputs(
            estimate.id,
            (evidence_id,),
        )

    assert _edge_rows(repo, estimate.id) == []


def test_existing_partial_edge_set_is_not_silently_repaired(repo):
    estimate = _estimate()
    repo.add_estimate(estimate)

    metric_id = "metric:test-revenue"
    claim_id = "claim:test-guidance"

    _insert_domain_node(repo, node_id=metric_id, node_type="Metric")
    _insert_domain_node(repo, node_id=claim_id, node_type="Claim")

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                INSERT INTO domain_edges (
                    source_id,
                    source_type,
                    edge_type,
                    target_id,
                    target_type,
                    created_at
                )
                VALUES (
                    %s,
                    'Estimate',
                    'DERIVED_FROM',
                    %s,
                    'Metric',
                    CURRENT_TIMESTAMP
                )
                """,
                (estimate.id, metric_id),
            )

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W531: ESTIMATE_INPUT_SET_MISMATCH",
    ):
        repo.add_estimate_inputs(
            estimate.id,
            (metric_id, claim_id),
        )

    assert _edge_rows(repo, estimate.id) == [(metric_id, "Metric")]


def test_add_estimate_inputs_rejects_noncanonical_estimate_id(repo):
    with pytest.raises(
        ValueError,
        match="estimate_id must be a canonical Estimate ID",
    ):
        repo.add_estimate_inputs(
            "estimate:not-canonical",
            ("metric:test-revenue",),
        )
