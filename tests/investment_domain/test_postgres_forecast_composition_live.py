import os
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from investment_domain.identity import canonical_id
from investment_domain.nodes import Estimate, Forecast
from investment_domain.postgres_migrations import PostgreSQLMigrationManager
from investment_domain.postgres_repository import (
    PostgreSQLEvidenceRepository,
    RepositoryWriteError,
)


UTC = timezone.utc
DSN = os.environ["IDM_TEST_POSTGRES_DSN"]


def _forecast(**overrides) -> Forecast:
    payload = {
        "subject_id": "company:nvidia",
        "scenario": "BASE",
        "as_of": datetime(2026, 9, 26, tzinfo=UTC),
        "model_version": "forecast-model-v1",
    }
    payload.update(overrides)
    return Forecast(
        id=canonical_id("forecast", payload),
        **payload,
    )


def _estimate(
    *,
    metric_name="financial.revenue",
    period_end=None,
    value="200000000000",
    **overrides,
) -> Estimate:
    payload = {
        "subject_id": "company:nvidia",
        "metric_name": metric_name,
        "period_end": period_end
        or datetime(2027, 1, 31, tzinfo=UTC),
        "value": Decimal(value),
        "unit": "currency",
        "scenario": "BASE",
        "model_version": "forecast-model-v1",
        "as_of": datetime(2026, 9, 26, tzinfo=UTC),
        "currency": "USD",
    }
    payload.update(overrides)
    return Estimate(
        id=canonical_id("estimate", payload),
        **payload,
    )


def _second_estimate() -> Estimate:
    return _estimate(
        metric_name="financial.gross_margin",
        period_end=datetime(2028, 1, 31, tzinfo=UTC),
        value="45.5",
        unit="percent",
        currency=None,
    )


@pytest.fixture
def repo():
    PostgreSQLMigrationManager(DSN).migrate()
    repository = PostgreSQLEvidenceRepository(DSN)

    with repository.connect() as con:
        with con.transaction():
            con.execute(
                """
                DELETE FROM domain_edges
                WHERE source_type = 'Forecast'
                   OR target_type = 'Forecast'
                """
            )
            con.execute("DELETE FROM forecast_facts")
            con.execute("DELETE FROM estimate_facts")
            con.execute(
                """
                DELETE FROM domain_nodes
                WHERE node_type IN ('Forecast', 'Estimate')
                """
            )

    return repository


def _persist(repo, *estimates):
    forecast = _forecast()
    repo.add_forecast(forecast)

    for estimate in estimates:
        repo.add_estimate(estimate)

    return forecast


def _composition_rows(repo, forecast_id):
    with repo.connect() as con:
        return con.execute(
            """
            SELECT target_id, target_type
            FROM domain_edges
            WHERE source_id = %s
              AND source_type = 'Forecast'
              AND edge_type = 'CONTAINS'
            ORDER BY target_id
            """,
            (forecast_id,),
        ).fetchall()


def test_forecast_composition_persists_exact_estimate_set(repo):
    first = _estimate()
    second = _second_estimate()
    forecast = _persist(repo, first, second)

    repo.add_forecast_estimates(
        forecast.id,
        (first.id, second.id),
    )

    assert _composition_rows(repo, forecast.id) == sorted(
        [
            (first.id, "Estimate"),
            (second.id, "Estimate"),
        ]
    )


def test_identical_forecast_composition_replay_is_idempotent(repo):
    estimate = _estimate()
    forecast = _persist(repo, estimate)

    repo.add_forecast_estimates(forecast.id, (estimate.id,))
    repo.add_forecast_estimates(forecast.id, (estimate.id,))

    assert _composition_rows(repo, forecast.id) == [
        (estimate.id, "Estimate")
    ]


def test_empty_forecast_composition_is_rejected(repo):
    forecast = _persist(repo)

    with pytest.raises(
        ValueError,
        match="estimate_ids must be a non-empty tuple",
    ):
        repo.add_forecast_estimates(forecast.id, ())


def test_duplicate_forecast_estimates_are_rejected(repo):
    estimate = _estimate()
    forecast = _persist(repo, estimate)

    with pytest.raises(
        ValueError,
        match="estimate_ids must not contain duplicates",
    ):
        repo.add_forecast_estimates(
            forecast.id,
            (estimate.id, estimate.id),
        )


def test_noncanonical_forecast_source_id_is_rejected(repo):
    estimate = _estimate()
    repo.add_estimate(estimate)

    with pytest.raises(
        ValueError,
        match="forecast_id must be a canonical Forecast ID",
    ):
        repo.add_forecast_estimates(
            "forecast:not-canonical",
            (estimate.id,),
        )


def test_missing_forecast_source_fails_closed(repo):
    estimate = _estimate()
    repo.add_estimate(estimate)
    forecast = _forecast()

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W507: EDGE_SOURCE_NOT_FOUND",
    ):
        repo.add_forecast_estimates(
            forecast.id,
            (estimate.id,),
        )


def test_missing_estimate_target_fails_without_partial_composition(repo):
    first = _estimate()
    forecast = _persist(repo, first)

    missing = _estimate(
        period_end=datetime(2029, 1, 31, tzinfo=UTC),
        value="400000000000",
    )

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W508: EDGE_TARGET_NOT_FOUND",
    ):
        repo.add_forecast_estimates(
            forecast.id,
            (first.id, missing.id),
        )

    assert _composition_rows(repo, forecast.id) == []


def test_wrong_forecast_composition_target_type_fails_closed(repo):
    forecast = _persist(repo)
    evidence_id = "evidence:forecast-composition-wrong-type"

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
                VALUES (
                    %s,
                    'Evidence',
                    '{}'::jsonb,
                    repeat('0', 64)
                )
                """,
                (evidence_id,),
            )

    try:
        with pytest.raises(
            RepositoryWriteError,
            match="IDM-W510: EDGE_TARGET_TYPE_MISMATCH",
        ):
            repo.add_forecast_estimates(
                forecast.id,
                (evidence_id,),
            )

        assert _composition_rows(repo, forecast.id) == []
    finally:
        with repo.connect() as con:
            with con.transaction():
                con.execute(
                    "DELETE FROM domain_nodes WHERE id = %s",
                    (evidence_id,),
                )


@pytest.mark.parametrize(
    "estimate_override",
    [
        {"subject_id": "company:amd"},
        {"scenario": "BULL"},
        {"model_version": "forecast-model-v2"},
        {"as_of": datetime(2026, 9, 25, tzinfo=UTC)},
    ],
    ids=[
        "subject-id",
        "scenario",
        "model-version",
        "as-of",
    ],
)
def test_forecast_estimate_semantic_mismatch_fails_closed(
    repo,
    estimate_override,
):
    estimate = _estimate(**estimate_override)
    forecast = _persist(repo, estimate)

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W540: FORECAST_ESTIMATE_SEMANTIC_MISMATCH",
    ):
        repo.add_forecast_estimates(
            forecast.id,
            (estimate.id,),
        )

    assert _composition_rows(repo, forecast.id) == []


def test_different_metrics_and_periods_are_valid_composition(repo):
    first = _estimate(
        metric_name="financial.revenue",
        period_end=datetime(2027, 1, 31, tzinfo=UTC),
    )
    second = _estimate(
        metric_name="financial.gross_margin",
        period_end=datetime(2029, 1, 31, tzinfo=UTC),
        value="47.25",
        unit="percent",
        currency=None,
    )
    forecast = _persist(repo, first, second)

    repo.add_forecast_estimates(
        forecast.id,
        (first.id, second.id),
    )

    assert len(_composition_rows(repo, forecast.id)) == 2


def test_existing_partial_forecast_composition_is_not_repaired(repo):
    first = _estimate()
    second = _second_estimate()
    forecast = _persist(repo, first, second)

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
                    'Forecast',
                    'CONTAINS',
                    %s,
                    'Estimate',
                    CURRENT_TIMESTAMP
                )
                """,
                (forecast.id, first.id),
            )

    before = _composition_rows(repo, forecast.id)

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W539: FORECAST_COMPOSITION_SET_MISMATCH",
    ):
        repo.add_forecast_estimates(
            forecast.id,
            (first.id, second.id),
        )

    assert before == [(first.id, "Estimate")]
    assert _composition_rows(repo, forecast.id) == before


def test_corrupted_forecast_domain_node_blocks_composition(repo):
    estimate = _estimate()
    forecast = _persist(repo, estimate)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE domain_nodes
                SET canonical_payload = %s::jsonb,
                    payload_hash = %s
                WHERE id = %s
                """,
                (
                    '{"corrupted":true}',
                    "0" * 64,
                    forecast.id,
                ),
            )

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W501: IDENTITY_CONTENT_COLLISION",
    ):
        repo.add_forecast_estimates(
            forecast.id,
            (estimate.id,),
        )

    assert _composition_rows(repo, forecast.id) == []


def test_corrupted_forecast_projection_blocks_composition(repo):
    estimate = _estimate()
    forecast = _persist(repo, estimate)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE forecast_facts
                SET scenario = 'BULL'
                WHERE node_id = %s
                """,
                (forecast.id,),
            )

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W537: FORECAST_PROJECTION_MISMATCH",
    ):
        repo.add_forecast_estimates(
            forecast.id,
            (estimate.id,),
        )

    assert _composition_rows(repo, forecast.id) == []


def test_missing_forecast_projection_blocks_composition(repo):
    estimate = _estimate()
    forecast = _persist(repo, estimate)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                "DELETE FROM forecast_facts WHERE node_id = %s",
                (forecast.id,),
            )

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W536: FORECAST_PROJECTION_WRITE_LOST",
    ):
        repo.add_forecast_estimates(
            forecast.id,
            (estimate.id,),
        )

    assert _composition_rows(repo, forecast.id) == []


def test_database_rejects_invalid_forecast_contains_target_type(repo):
    forecast = _persist(repo)
    evidence_id = "evidence:invalid-forecast-composition-db-test"

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
                VALUES (
                    %s,
                    'Evidence',
                    '{}'::jsonb,
                    repeat('0', 64)
                )
                """,
                (evidence_id,),
            )

    try:
        with pytest.raises(Exception) as exc_info:
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
                            'Forecast',
                            'CONTAINS',
                            %s,
                            'Evidence',
                            CURRENT_TIMESTAMP
                        )
                        """,
                        (forecast.id, evidence_id),
                    )

        exc = exc_info.value
        assert getattr(exc, "sqlstate", None) == "23514"
        assert (
            getattr(
                getattr(exc, "diag", None),
                "constraint_name",
                None,
            )
            == "domain_edges_source_relation_target_type"
        )

        assert _composition_rows(repo, forecast.id) == []
    finally:
        with repo.connect() as con:
            with con.transaction():
                con.execute(
                    "DELETE FROM domain_nodes WHERE id = %s",
                    (evidence_id,),
                )


def test_forecast_composition_rolls_back_on_second_insert_failure(repo):
    first = _estimate()
    second = _second_estimate()
    forecast = _persist(repo, first, second)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                CREATE OR REPLACE FUNCTION
                    reject_second_forecast_composition_for_atomicity_test()
                RETURNS trigger
                LANGUAGE plpgsql
                AS $$
                BEGIN
                    IF NEW.target_id = TG_ARGV[0] THEN
                        RAISE EXCEPTION
                            'forced second forecast composition failure';
                    END IF;
                    RETURN NEW;
                END;
                $$
                """
            )

            from psycopg import sql

            trigger_sql = sql.SQL(
                """
                CREATE TRIGGER
                    reject_second_forecast_composition_for_atomicity_test
                BEFORE INSERT ON domain_edges
                FOR EACH ROW
                WHEN (
                    NEW.source_type = 'Forecast'
                    AND NEW.edge_type = 'CONTAINS'
                )
                EXECUTE FUNCTION
                    reject_second_forecast_composition_for_atomicity_test(
                        {}
                    )
                """
            ).format(sql.Literal(second.id))

            con.execute(trigger_sql)

    try:
        with pytest.raises(
            Exception,
            match="forced second forecast composition failure",
        ):
            repo.add_forecast_estimates(
                forecast.id,
                (first.id, second.id),
            )

        assert _composition_rows(repo, forecast.id) == []
    finally:
        with repo.connect() as con:
            with con.transaction():
                con.execute(
                    """
                    DROP TRIGGER IF EXISTS
                        reject_second_forecast_composition_for_atomicity_test
                    ON domain_edges
                    """
                )
                con.execute(
                    """
                    DROP FUNCTION IF EXISTS
                        reject_second_forecast_composition_for_atomicity_test()
                    """
                )
