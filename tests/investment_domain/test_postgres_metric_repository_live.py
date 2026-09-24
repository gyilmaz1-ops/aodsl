from __future__ import annotations

import os
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from investment_domain import canonical_id
from investment_domain.nodes import Metric
from investment_domain.postgres_migrations import PostgreSQLMigrationManager
from investment_domain.postgres_repository import (
    PostgreSQLEvidenceRepository,
    RepositoryWriteError,
)


DSN = os.environ.get("IDM_TEST_POSTGRES_DSN")

pytestmark = pytest.mark.skipif(
    not DSN,
    reason="IDM_TEST_POSTGRES_DSN is not configured",
)


def utc(year, month, day, hour=0):
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


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


def revenue_metric(**overrides):
    values = {
        "subject_id": "company:acme",
        "name": "financial.revenue",
        "value": Decimal("123456789.12500"),
        "unit": "currency",
        "currency": "USD",
        "period_start": utc(2026, 4, 1),
        "period_end": utc(2026, 6, 30),
        "effective_at": utc(2026, 6, 30),
        "observed_at": utc(2026, 7, 20),
        "published_at": utc(2026, 7, 21),
        "ingested_at": utc(2026, 7, 22),
        "source_id": "sec:10-q",
        "source_version": "2026-q2-v1",
        "supersedes_id": None,
    }
    values.update(overrides)

    supplied_id = values.pop("id", None)
    values["id"] = supplied_id or _metric_id(values)

    return Metric(**values)


def price_metric(**overrides):
    values = {
        "subject_id": "security:acme-common",
        "name": "market.price",
        "value": Decimal("217.3400"),
        "unit": "currency",
        "currency": "USD",
        "period_start": None,
        "period_end": utc(2026, 9, 23, 20),
        "effective_at": utc(2026, 9, 23, 20),
        "observed_at": utc(2026, 9, 23, 20),
        "published_at": utc(2026, 9, 23, 20),
        "ingested_at": utc(2026, 9, 23, 21),
        "source_id": "market-feed",
        "source_version": "20260923T200000Z",
        "supersedes_id": None,
    }
    values.update(overrides)

    supplied_id = values.pop("id", None)
    values["id"] = supplied_id or _metric_id(values)

    return Metric(**values)


@pytest.fixture
def repo():
    PostgreSQLMigrationManager(DSN).migrate()
    return PostgreSQLEvidenceRepository(DSN)


@pytest.fixture(autouse=True)
def clean_metric_state(repo):
    with repo.connect() as con:
        with con.transaction():
            con.execute("DELETE FROM metric_facts")
            con.execute(
                "DELETE FROM domain_nodes WHERE node_type = %s",
                ("Metric",),
            )

    yield

    with repo.connect() as con:
        with con.transaction():
            con.execute("DELETE FROM metric_facts")
            con.execute(
                "DELETE FROM domain_nodes WHERE node_type = %s",
                ("Metric",),
            )


def fetch_metric_projection(repo, metric_id):
    with repo.connect() as con:
        return con.execute(
            """
            SELECT
                node_type,
                subject_id,
                name,
                value,
                unit,
                currency,
                period_start,
                period_end,
                effective_at,
                observed_at,
                published_at,
                ingested_at,
                source_id,
                source_version,
                supersedes_id
            FROM metric_facts
            WHERE node_id = %s
            """,
            (metric_id,),
        ).fetchone()


# MP004 / MP005 / MP008 / MP010 / MP011 / MP012
def test_add_metric_persists_complete_duration_projection(repo):
    metric = revenue_metric()

    repo.add_metric(metric)

    row = fetch_metric_projection(repo, metric.id)

    assert row == (
        "Metric",
        metric.subject_id,
        metric.name,
        metric.value,
        metric.unit,
        metric.currency,
        metric.period_start,
        metric.period_end,
        metric.effective_at,
        metric.observed_at,
        metric.published_at,
        metric.ingested_at,
        metric.source_id,
        metric.source_version,
        metric.supersedes_id,
    )

    with repo.connect() as con:
        node = con.execute(
            """
            SELECT node_type
            FROM domain_nodes
            WHERE id = %s
            """,
            (metric.id,),
        ).fetchone()

    assert node == ("Metric",)


# MP006
def test_metric_numeric_round_trip_returns_decimal_without_float(repo):
    metric = revenue_metric(
        value=Decimal("123456789.12500"),
    )

    repo.add_metric(metric)
    row = fetch_metric_projection(repo, metric.id)

    stored_value = row[3]

    assert isinstance(stored_value, Decimal)
    assert not isinstance(stored_value, float)
    assert stored_value == metric.value


# Diagnostic only: scale is observed, but is not an IDM-005B invariant.
def test_metric_numeric_round_trip_reports_postgres_scale(repo):
    metric = revenue_metric(value=Decimal("10.500"))

    repo.add_metric(metric)
    stored_value = fetch_metric_projection(repo, metric.id)[3]

    assert stored_value == Decimal("10.500")


# MP009
def test_instant_metric_preserves_null_period_start(repo):
    metric = price_metric()

    repo.add_metric(metric)
    row = fetch_metric_projection(repo, metric.id)

    assert row[6] is None
    assert row[7] == metric.period_end


# MP013 was superseded by IDM-005E Metric revision enforcement.
def test_supersedes_id_requires_existing_revision_predecessor(repo):
    metric = revenue_metric(
        supersedes_id="metric:not-present-in-database",
    )

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W512: REVISION_PREDECESSOR_NOT_FOUND",
    ):
        repo.add_metric(metric)


# MP014
def test_identical_metric_write_is_idempotent(repo):
    metric = revenue_metric()

    repo.add_metric(metric)
    repo.add_metric(metric)

    with repo.connect() as con:
        node_count = con.execute(
            "SELECT COUNT(*) FROM domain_nodes WHERE id = %s",
            (metric.id,),
        ).fetchone()[0]
        projection_count = con.execute(
            "SELECT COUNT(*) FROM metric_facts WHERE node_id = %s",
            (metric.id,),
        ).fetchone()[0]

    assert node_count == 1
    assert projection_count == 1


# MP015
def test_same_id_with_different_canonical_content_fails_closed(repo):
    metric = revenue_metric()
    repo.add_metric(metric)

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
                    metric.id,
                ),
            )

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W501: IDENTITY_CONTENT_COLLISION",
    ):
        repo.add_metric(metric)


# MP016
def test_existing_metric_projection_mismatch_fails_closed(repo):
    metric = revenue_metric()
    repo.add_metric(metric)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE metric_facts
                SET value = %s
                WHERE node_id = %s
                """,
                (Decimal("999.00"), metric.id),
            )

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W517: METRIC_PROJECTION_MISMATCH",
    ):
        repo.add_metric(metric)


# MP017
def test_invalid_metric_is_rejected_before_persistence(repo):
    metric = revenue_metric(currency="usd")

    with pytest.raises(ValueError):
        repo.add_metric(metric)

    with repo.connect() as con:
        node_count = con.execute(
            "SELECT COUNT(*) FROM domain_nodes WHERE id = %s",
            (metric.id,),
        ).fetchone()[0]
        projection_count = con.execute(
            "SELECT COUNT(*) FROM metric_facts WHERE node_id = %s",
            (metric.id,),
        ).fetchone()[0]

    assert node_count == 0
    assert projection_count == 0


# MP018
def test_projection_failure_rolls_back_domain_node_insert(repo):
    metric = revenue_metric()

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                ALTER TABLE metric_facts
                ADD CONSTRAINT metric_test_forced_failure
                CHECK (name <> 'financial.revenue')
                """
            )

    try:
        with pytest.raises(Exception):
            repo.add_metric(metric)

        with repo.connect() as con:
            node_count = con.execute(
                "SELECT COUNT(*) FROM domain_nodes WHERE id = %s",
                (metric.id,),
            ).fetchone()[0]
            projection_count = con.execute(
                "SELECT COUNT(*) FROM metric_facts WHERE node_id = %s",
                (metric.id,),
            ).fetchone()[0]

        assert node_count == 0
        assert projection_count == 0
    finally:
        with repo.connect() as con:
            with con.transaction():
                con.execute(
                    """
                    ALTER TABLE metric_facts
                    DROP CONSTRAINT IF EXISTS metric_test_forced_failure
                    """
                )
