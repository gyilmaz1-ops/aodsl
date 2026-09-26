from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
from psycopg import sql

from investment_domain import canonical_id
from investment_domain.nodes import Catalyst, Claim, Forecast
from investment_domain.postgres_migrations import PostgreSQLMigrationManager
from investment_domain.postgres_repository import (
    PostgreSQLEvidenceRepository,
    RepositoryWriteError,
)


UTC = timezone.utc
DSN = os.getenv("IDM_TEST_POSTGRES_DSN")

pytestmark = pytest.mark.skipif(
    not DSN,
    reason="IDM_TEST_POSTGRES_DSN is not configured",
)


def utc(year, month, day, hour=0):
    return datetime(year, month, day, hour, tzinfo=UTC)


def _catalyst(**overrides) -> Catalyst:
    values = {
        "subject_id": "security:nasdaq:nvda",
        "description": "Next-generation GPU platform launch",
        "as_of": utc(2026, 9, 26, 8),
        "expected_at": utc(2027, 3, 1, 8),
    }
    values.update(overrides)

    return Catalyst(
        id=canonical_id("catalyst", values),
        **values,
    )


def _claim(seed="base") -> Claim:
    payload = {
        "subject_id": f"company:{seed}",
        "predicate": "revenue_growth",
        "object_value": "positive",
        "object_ref": None,
        "polarity": "POSITIVE",
        "scope": "company",
        "as_of": utc(2026, 9, 26),
    }

    return Claim(
        id=canonical_id("claim", payload),
        **payload,
        created_by="Fundamental_Analyst",
    )


def _forecast(seed="base") -> Forecast:
    payload = {
        "subject_id": f"security:nasdaq:{seed}",
        "scenario": "BASE",
        "as_of": utc(2026, 9, 26, 8),
        "model_version": "forecast-model-v1",
    }

    return Forecast(
        id=canonical_id("forecast", payload),
        **payload,
    )


@pytest.fixture
def repo():
    PostgreSQLMigrationManager(DSN).migrate()
    return PostgreSQLEvidenceRepository(DSN)


@pytest.fixture(autouse=True)
def clean_state(repo):
    def clean():
        with repo.connect() as con:
            with con.transaction():
                con.execute(
                    """
                    DELETE FROM domain_edges
                    WHERE source_type = 'Catalyst'
                       OR target_type = 'Catalyst'
                    """
                )
                con.execute("DELETE FROM catalyst_facts")
                con.execute("DELETE FROM forecast_facts")
                con.execute("DELETE FROM claim_facts")
                con.execute(
                    """
                    DELETE FROM domain_nodes
                    WHERE node_type IN (
                        'Catalyst',
                        'Forecast',
                        'Claim'
                    )
                    """
                )

    clean()
    yield
    clean()


def _rows(repo, catalyst_id):
    with repo.connect() as con:
        return con.execute(
            """
            SELECT
                source_type,
                edge_type,
                target_id,
                target_type
            FROM domain_edges
            WHERE source_id = %s
              AND source_type = 'Catalyst'
              AND edge_type = 'AFFECTS'
            ORDER BY target_id
            """,
            (catalyst_id,),
        ).fetchall()


def _persist_source(repo):
    catalyst = _catalyst()
    repo.add_catalyst(catalyst)
    return catalyst


def test_catalyst_affects_claim_persists(repo):
    catalyst = _persist_source(repo)
    claim = _claim("claim-target")
    repo.add_claim(claim)

    repo.add_catalyst_affects(
        catalyst.id,
        (claim.id,),
    )

    assert _rows(repo, catalyst.id) == [
        (
            "Catalyst",
            "AFFECTS",
            claim.id,
            "Claim",
        )
    ]


def test_catalyst_affects_forecast_persists(repo):
    catalyst = _persist_source(repo)
    forecast = _forecast("forecast-target")
    repo.add_forecast(forecast)

    repo.add_catalyst_affects(
        catalyst.id,
        (forecast.id,),
    )

    assert _rows(repo, catalyst.id) == [
        (
            "Catalyst",
            "AFFECTS",
            forecast.id,
            "Forecast",
        )
    ]


def test_mixed_claim_forecast_aggregate_persists(repo):
    catalyst = _persist_source(repo)
    claim = _claim("mixed-claim")
    forecast = _forecast("mixed-forecast")

    repo.add_claim(claim)
    repo.add_forecast(forecast)

    repo.add_catalyst_affects(
        catalyst.id,
        (claim.id, forecast.id),
    )

    rows = _rows(repo, catalyst.id)

    assert set(rows) == {
        (
            "Catalyst",
            "AFFECTS",
            claim.id,
            "Claim",
        ),
        (
            "Catalyst",
            "AFFECTS",
            forecast.id,
            "Forecast",
        ),
    }
    assert len(rows) == 2


def test_exact_replay_is_idempotent(repo):
    catalyst = _persist_source(repo)
    claim = _claim("replay-claim")
    forecast = _forecast("replay-forecast")

    repo.add_claim(claim)
    repo.add_forecast(forecast)

    targets = (claim.id, forecast.id)

    repo.add_catalyst_affects(catalyst.id, targets)
    first = _rows(repo, catalyst.id)

    repo.add_catalyst_affects(catalyst.id, targets)
    second = _rows(repo, catalyst.id)

    assert second == first
    assert len(second) == 2


def test_missing_source_fails_closed(repo):
    claim = _claim("missing-source")
    repo.add_claim(claim)

    missing_catalyst = _catalyst(
        description="Missing source catalyst",
    )

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W507: EDGE_SOURCE_NOT_FOUND",
    ):
        repo.add_catalyst_affects(
            missing_catalyst.id,
            (claim.id,),
        )

    assert _rows(repo, missing_catalyst.id) == []


def test_wrong_source_type_fails_closed(repo):
    # A canonical Catalyst ID exists, but its stored node type is corrupted
    # to Claim. Projection is intentionally absent because source type must
    # fail before projection validation.
    catalyst = _catalyst(
        description="Wrong source type catalyst",
    )
    claim = _claim("wrong-source-target")
    repo.add_claim(claim)

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
                    'Claim',
                    '{}'::jsonb,
                    %s
                )
                """,
                (
                    catalyst.id,
                    "0" * 64,
                ),
            )

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W509: EDGE_SOURCE_TYPE_MISMATCH",
    ):
        repo.add_catalyst_affects(
            catalyst.id,
            (claim.id,),
        )

    assert _rows(repo, catalyst.id) == []


def test_missing_target_fails_closed(repo):
    catalyst = _persist_source(repo)
    missing = _claim("missing-target")

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W508: EDGE_TARGET_NOT_FOUND",
    ):
        repo.add_catalyst_affects(
            catalyst.id,
            (missing.id,),
        )

    assert _rows(repo, catalyst.id) == []


def test_unsupported_target_type_fails_closed(repo):
    catalyst = _persist_source(repo)

    other_catalyst = _catalyst(
        description="Unsupported target catalyst",
    )
    repo.add_catalyst(other_catalyst)

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W510: EDGE_TARGET_TYPE_MISMATCH",
    ):
        repo.add_catalyst_affects(
            catalyst.id,
            (other_catalyst.id,),
        )

    assert _rows(repo, catalyst.id) == []


def test_missing_catalyst_projection_fails_closed(repo):
    catalyst = _persist_source(repo)
    claim = _claim("missing-projection")
    repo.add_claim(claim)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                DELETE FROM catalyst_facts
                WHERE node_id = %s
                """,
                (catalyst.id,),
            )

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W541: CATALYST_PROJECTION_WRITE_LOST",
    ):
        repo.add_catalyst_affects(
            catalyst.id,
            (claim.id,),
        )

    assert _rows(repo, catalyst.id) == []


def test_corrupt_catalyst_projection_fails_closed(repo):
    catalyst = _persist_source(repo)
    claim = _claim("corrupt-projection")
    repo.add_claim(claim)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE catalyst_facts
                SET description = %s
                WHERE node_id = %s
                """,
                (
                    "corrupted catalyst description",
                    catalyst.id,
                ),
            )

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W542: CATALYST_PROJECTION_MISMATCH",
    ):
        repo.add_catalyst_affects(
            catalyst.id,
            (claim.id,),
        )

    assert _rows(repo, catalyst.id) == []


def test_corrupt_catalyst_domain_payload_fails_closed(repo):
    catalyst = _persist_source(repo)
    claim = _claim("corrupt-domain")
    repo.add_claim(claim)

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
                    catalyst.id,
                ),
            )

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W501: IDENTITY_CONTENT_COLLISION",
    ):
        repo.add_catalyst_affects(
            catalyst.id,
            (claim.id,),
        )

    assert _rows(repo, catalyst.id) == []


def test_partial_existing_set_fails_closed(repo):
    catalyst = _persist_source(repo)
    claim = _claim("partial-claim")
    forecast = _forecast("partial-forecast")

    repo.add_claim(claim)
    repo.add_forecast(forecast)

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
                    'Catalyst',
                    'AFFECTS',
                    %s,
                    'Claim',
                    CURRENT_TIMESTAMP
                )
                """,
                (
                    catalyst.id,
                    claim.id,
                ),
            )

    before = _rows(repo, catalyst.id)

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W544: CATALYST_AFFECTS_SET_MISMATCH",
    ):
        repo.add_catalyst_affects(
            catalyst.id,
            (claim.id, forecast.id),
        )

    assert _rows(repo, catalyst.id) == before


def test_divergent_existing_set_fails_closed(repo):
    catalyst = _persist_source(repo)

    expected_claim = _claim("expected-claim")
    unexpected_claim = _claim("unexpected-claim")

    repo.add_claim(expected_claim)
    repo.add_claim(unexpected_claim)

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
                    'Catalyst',
                    'AFFECTS',
                    %s,
                    'Claim',
                    CURRENT_TIMESTAMP
                )
                """,
                (
                    catalyst.id,
                    unexpected_claim.id,
                ),
            )

    before = _rows(repo, catalyst.id)

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W544: CATALYST_AFFECTS_SET_MISMATCH",
    ):
        repo.add_catalyst_affects(
            catalyst.id,
            (expected_claim.id,),
        )

    assert _rows(repo, catalyst.id) == before


def test_invalid_second_target_writes_no_edges(repo):
    catalyst = _persist_source(repo)

    valid = _claim("atomic-valid")
    missing = _forecast("atomic-missing")

    repo.add_claim(valid)

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W508: EDGE_TARGET_NOT_FOUND",
    ):
        repo.add_catalyst_affects(
            catalyst.id,
            (valid.id, missing.id),
        )

    assert _rows(repo, catalyst.id) == []


def test_second_insert_database_failure_rolls_back_all_edges(repo):
    catalyst = _persist_source(repo)

    first = _claim("trigger-first")
    second = _forecast("trigger-second")

    repo.add_claim(first)
    repo.add_forecast(second)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                CREATE OR REPLACE FUNCTION
                    reject_second_catalyst_affects_for_atomicity_test()
                RETURNS trigger
                LANGUAGE plpgsql
                AS $$
                BEGIN
                    IF NEW.target_id = TG_ARGV[0] THEN
                        RAISE EXCEPTION
                            'forced second catalyst affects failure';
                    END IF;
                    RETURN NEW;
                END;
                $$
                """
            )

            trigger_sql = sql.SQL(
                """
                CREATE TRIGGER
                    reject_second_catalyst_affects_for_atomicity_test
                BEFORE INSERT ON domain_edges
                FOR EACH ROW
                WHEN (
                    NEW.source_type = 'Catalyst'
                    AND NEW.edge_type = 'AFFECTS'
                )
                EXECUTE FUNCTION
                    reject_second_catalyst_affects_for_atomicity_test(
                        {}
                    )
                """
            ).format(sql.Literal(second.id))

            con.execute(trigger_sql)

    try:
        with pytest.raises(
            Exception,
            match="forced second catalyst affects failure",
        ):
            repo.add_catalyst_affects(
                catalyst.id,
                (first.id, second.id),
            )

        assert _rows(repo, catalyst.id) == []

    finally:
        with repo.connect() as con:
            with con.transaction():
                con.execute(
                    """
                    DROP TRIGGER IF EXISTS
                        reject_second_catalyst_affects_for_atomicity_test
                    ON domain_edges
                    """
                )
                con.execute(
                    """
                    DROP FUNCTION IF EXISTS
                        reject_second_catalyst_affects_for_atomicity_test()
                    """
                )
