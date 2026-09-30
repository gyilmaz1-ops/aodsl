from __future__ import annotations

from decimal import Decimal

import os
from datetime import datetime, timezone

import pytest
from psycopg import sql

from investment_domain import canonical_id
from investment_domain.nodes import Risk, Claim, Forecast, Valuation
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


def _risk(**overrides) -> Risk:
    values = {
        "subject_id": "security:nasdaq:nvda",
        "description": "Next-generation GPU platform launch",
        "as_of": utc(2026, 9, 26, 8),
    }
    values.update(overrides)

    return Risk(
        id=canonical_id("risk", values),
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
                    WHERE source_type = 'Risk'
                       OR target_type = 'Risk'
                    """
                )
                con.execute("DELETE FROM risk_facts")
                con.execute("DELETE FROM forecast_facts")
                con.execute("DELETE FROM claim_facts")
                con.execute(
                    """
                    DELETE FROM domain_nodes
                    WHERE node_type IN (
                        'Risk',
                        'Forecast',
                        'Claim'
                    )
                    """
                )

    clean()
    yield
    clean()

def _valuation() -> Valuation:
    payload = {
        "security_id": "security:nasdaq:nvda",
        "method": "DCF",
        "value": Decimal("250.00"),
        "currency": "USD",
        "as_of": utc(2026, 9, 30, 12),
        "model_version": "1",
        "scenario": "BASE",
    }
    return Valuation(
        id=canonical_id("valuation", payload),
        **payload,
    )


def _rows(repo, risk_id):
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
              AND source_type = 'Risk'
              AND edge_type = 'AFFECTS'
            ORDER BY target_id
            """,
            (risk_id,),
        ).fetchall()


def _persist_source(repo):
    risk = _risk()
    repo.add_risk(risk)
    return risk


def test_risk_affects_claim_persists(repo):
    risk = _persist_source(repo)
    claim = _claim("claim-target")
    repo.add_claim(claim)

    repo.add_risk_affects(
        risk.id,
        (claim.id,),
    )

    assert _rows(repo, risk.id) == [
        (
            "Risk",
            "AFFECTS",
            claim.id,
            "Claim",
        )
    ]


def test_risk_affects_forecast_persists(repo):
    risk = _persist_source(repo)
    forecast = _forecast("forecast-target")
    repo.add_forecast(forecast)

    repo.add_risk_affects(
        risk.id,
        (forecast.id,),
    )

    assert _rows(repo, risk.id) == [
        (
            "Risk",
            "AFFECTS",
            forecast.id,
            "Forecast",
        )
    ]

def test_risk_affects_valuation_persists(repo):
    risk = _persist_source(repo)
    valuation = _valuation()
    repo.add_valuation(valuation)

    repo.add_risk_affects(
        risk.id,
        (valuation.id,),
    )

    assert _rows(repo, risk.id) == [
        (
            "Risk",
            "AFFECTS",
            valuation.id,
            "Valuation",
        )
    ]


def test_mixed_claim_forecast_aggregate_persists(repo):
    risk = _persist_source(repo)
    claim = _claim("mixed-claim")
    forecast = _forecast("mixed-forecast")

    repo.add_claim(claim)
    repo.add_forecast(forecast)

    repo.add_risk_affects(
        risk.id,
        (claim.id, forecast.id),
    )

    rows = _rows(repo, risk.id)

    assert set(rows) == {
        (
            "Risk",
            "AFFECTS",
            claim.id,
            "Claim",
        ),
        (
            "Risk",
            "AFFECTS",
            forecast.id,
            "Forecast",
        ),
    }
    assert len(rows) == 2


def test_exact_replay_is_idempotent(repo):
    risk = _persist_source(repo)
    claim = _claim("replay-claim")
    forecast = _forecast("replay-forecast")

    repo.add_claim(claim)
    repo.add_forecast(forecast)

    targets = (claim.id, forecast.id)

    repo.add_risk_affects(risk.id, targets)
    first = _rows(repo, risk.id)

    repo.add_risk_affects(risk.id, targets)
    second = _rows(repo, risk.id)

    assert second == first
    assert len(second) == 2


def test_missing_source_fails_closed(repo):
    claim = _claim("missing-source")
    repo.add_claim(claim)

    missing_risk = _risk(
        description="Missing source risk",
    )

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W507: EDGE_SOURCE_NOT_FOUND",
    ):
        repo.add_risk_affects(
            missing_risk.id,
            (claim.id,),
        )

    assert _rows(repo, missing_risk.id) == []


def test_wrong_source_type_fails_closed(repo):
    # A canonical Risk ID exists, but its stored node type is corrupted
    # to Claim. Projection is intentionally absent because source type must
    # fail before projection validation.
    risk = _risk(
        description="Wrong source type risk",
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
                    risk.id,
                    "0" * 64,
                ),
            )

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W509: EDGE_SOURCE_TYPE_MISMATCH",
    ):
        repo.add_risk_affects(
            risk.id,
            (claim.id,),
        )

    assert _rows(repo, risk.id) == []


def test_missing_target_fails_closed(repo):
    risk = _persist_source(repo)
    missing = _claim("missing-target")

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W508: EDGE_TARGET_NOT_FOUND",
    ):
        repo.add_risk_affects(
            risk.id,
            (missing.id,),
        )

    assert _rows(repo, risk.id) == []


def test_unsupported_target_type_fails_closed(repo):
    risk = _persist_source(repo)

    other_risk = _risk(
        description="Unsupported target risk",
    )
    repo.add_risk(other_risk)

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W510: EDGE_TARGET_TYPE_MISMATCH",
    ):
        repo.add_risk_affects(
            risk.id,
            (other_risk.id,),
        )

    assert _rows(repo, risk.id) == []


def test_missing_risk_projection_fails_closed(repo):
    risk = _persist_source(repo)
    claim = _claim("missing-projection")
    repo.add_claim(claim)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                DELETE FROM risk_facts
                WHERE node_id = %s
                """,
                (risk.id,),
            )

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W554: RISK_PROJECTION_WRITE_LOST",
    ):
        repo.add_risk_affects(
            risk.id,
            (claim.id,),
        )

    assert _rows(repo, risk.id) == []


def test_corrupt_risk_projection_fails_closed(repo):
    risk = _persist_source(repo)
    claim = _claim("corrupt-projection")
    repo.add_claim(claim)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE risk_facts
                SET description = %s
                WHERE node_id = %s
                """,
                (
                    "corrupted risk description",
                    risk.id,
                ),
            )

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W555: RISK_PROJECTION_MISMATCH",
    ):
        repo.add_risk_affects(
            risk.id,
            (claim.id,),
        )

    assert _rows(repo, risk.id) == []


def test_corrupt_risk_domain_payload_fails_closed(repo):
    risk = _persist_source(repo)
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
                    risk.id,
                ),
            )

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W501: IDENTITY_CONTENT_COLLISION",
    ):
        repo.add_risk_affects(
            risk.id,
            (claim.id,),
        )

    assert _rows(repo, risk.id) == []


def test_partial_existing_set_fails_closed(repo):
    risk = _persist_source(repo)
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
                    'Risk',
                    'AFFECTS',
                    %s,
                    'Claim',
                    CURRENT_TIMESTAMP
                )
                """,
                (
                    risk.id,
                    claim.id,
                ),
            )

    before = _rows(repo, risk.id)

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W557: RISK_AFFECTS_SET_MISMATCH",
    ):
        repo.add_risk_affects(
            risk.id,
            (claim.id, forecast.id),
        )

    assert _rows(repo, risk.id) == before


def test_divergent_existing_set_fails_closed(repo):
    risk = _persist_source(repo)

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
                    'Risk',
                    'AFFECTS',
                    %s,
                    'Claim',
                    CURRENT_TIMESTAMP
                )
                """,
                (
                    risk.id,
                    unexpected_claim.id,
                ),
            )

    before = _rows(repo, risk.id)

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W557: RISK_AFFECTS_SET_MISMATCH",
    ):
        repo.add_risk_affects(
            risk.id,
            (expected_claim.id,),
        )

    assert _rows(repo, risk.id) == before


def test_invalid_second_target_writes_no_edges(repo):
    risk = _persist_source(repo)

    valid = _claim("atomic-valid")
    missing = _forecast("atomic-missing")

    repo.add_claim(valid)

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W508: EDGE_TARGET_NOT_FOUND",
    ):
        repo.add_risk_affects(
            risk.id,
            (valid.id, missing.id),
        )

    assert _rows(repo, risk.id) == []


def test_second_insert_database_failure_rolls_back_all_edges(repo):
    risk = _persist_source(repo)

    first = _claim("trigger-first")
    second = _forecast("trigger-second")

    repo.add_claim(first)
    repo.add_forecast(second)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                CREATE OR REPLACE FUNCTION
                    reject_second_risk_affects_for_atomicity_test()
                RETURNS trigger
                LANGUAGE plpgsql
                AS $$
                BEGIN
                    IF NEW.target_id = TG_ARGV[0] THEN
                        RAISE EXCEPTION
                            'forced second risk affects failure';
                    END IF;
                    RETURN NEW;
                END;
                $$
                """
            )

            trigger_sql = sql.SQL(
                """
                CREATE TRIGGER
                    reject_second_risk_affects_for_atomicity_test
                BEFORE INSERT ON domain_edges
                FOR EACH ROW
                WHEN (
                    NEW.source_type = 'Risk'
                    AND NEW.edge_type = 'AFFECTS'
                )
                EXECUTE FUNCTION
                    reject_second_risk_affects_for_atomicity_test(
                        {}
                    )
                """
            ).format(sql.Literal(second.id))

            con.execute(trigger_sql)

    try:
        with pytest.raises(
            Exception,
            match="forced second risk affects failure",
        ):
            repo.add_risk_affects(
                risk.id,
                (first.id, second.id),
            )

        assert _rows(repo, risk.id) == []

    finally:
        with repo.connect() as con:
            with con.transaction():
                con.execute(
                    """
                    DROP TRIGGER IF EXISTS
                        reject_second_risk_affects_for_atomicity_test
                    ON domain_edges
                    """
                )
                con.execute(
                    """
                    DROP FUNCTION IF EXISTS
                        reject_second_risk_affects_for_atomicity_test()
                    """
                )
