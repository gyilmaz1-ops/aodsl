from __future__ import annotations

import os
from dataclasses import replace
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


DSN = os.environ.get("IDM_TEST_POSTGRES_DSN")

pytestmark = pytest.mark.skipif(
    not DSN,
    reason="IDM_TEST_POSTGRES_DSN is not configured",
)


def utc(year, month, day, hour=0):
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


def _estimate_id(values):
    return canonical_id(
        "estimate",
        {
            "subject_id": values["subject_id"],
            "metric_name": values["metric_name"],
            "period_end": values["period_end"],
            "value": values["value"],
            "unit": values["unit"],
            "scenario": values["scenario"],
            "model_version": values["model_version"],
            "as_of": values["as_of"],
            "currency": values["currency"],
        },
    )


def estimate(**overrides):
    values = {
        "subject_id": "company:acme",
        "metric_name": "financial.revenue",
        "period_end": utc(2027, 12, 31),
        "value": Decimal("123456789.12500"),
        "unit": "currency",
        "scenario": "BASE",
        "model_version": "estimate-model-v1",
        "as_of": utc(2026, 9, 26, 8),
        "currency": "USD",
    }
    values.update(overrides)

    supplied_id = values.pop("id", None)
    values["id"] = supplied_id or _estimate_id(values)

    return Estimate(**values)


@pytest.fixture
def repo():
    PostgreSQLMigrationManager(DSN).migrate()
    return PostgreSQLEvidenceRepository(DSN)


@pytest.fixture(autouse=True)
def clean_estimate_state(repo):
    with repo.connect() as con:
        with con.transaction():
            con.execute("DELETE FROM estimate_facts")
            con.execute(
                "DELETE FROM domain_nodes WHERE node_type = %s",
                ("Estimate",),
            )

    yield

    with repo.connect() as con:
        with con.transaction():
            con.execute("DELETE FROM estimate_facts")
            con.execute(
                "DELETE FROM domain_nodes WHERE node_type = %s",
                ("Estimate",),
            )


def fetch_projection(repo, estimate_id):
    with repo.connect() as con:
        return con.execute(
            """
            SELECT
                node_type,
                subject_id,
                metric_name,
                period_end,
                value,
                unit,
                scenario,
                model_version,
                as_of,
                currency
            FROM estimate_facts
            WHERE node_id = %s
            """,
            (estimate_id,),
        ).fetchone()


def test_add_estimate_persists_complete_projection(repo):
    item = estimate()

    repo.add_estimate(item)

    row = fetch_projection(repo, item.id)

    assert row is not None
    assert row[0] == "Estimate"
    assert row[1] == item.subject_id
    assert row[2] == item.metric_name
    assert row[3] == item.period_end
    assert row[4] == item.value
    assert isinstance(row[4], Decimal)
    assert not isinstance(row[4], float)
    assert row[5] == item.unit
    assert row[6] == item.scenario
    assert row[7] == item.model_version
    assert row[8] == item.as_of
    assert row[9] == item.currency

    with repo.connect() as con:
        node = con.execute(
            """
            SELECT node_type
            FROM domain_nodes
            WHERE id = %s
            """,
            (item.id,),
        ).fetchone()

    assert node == ("Estimate",)


def test_nullable_currency_round_trip(repo):
    item = estimate(
        metric_name="financial.gross_margin",
        value=Decimal("27.50000"),
        unit="percent",
        currency=None,
    )

    repo.add_estimate(item)

    row = fetch_projection(repo, item.id)

    assert row[4] == Decimal("27.50000")
    assert isinstance(row[4], Decimal)
    assert row[9] is None


def test_identical_estimate_write_is_idempotent(repo):
    item = estimate()

    repo.add_estimate(item)
    repo.add_estimate(item)

    with repo.connect() as con:
        node_count = con.execute(
            "SELECT COUNT(*) FROM domain_nodes WHERE id = %s",
            (item.id,),
        ).fetchone()[0]

        projection_count = con.execute(
            "SELECT COUNT(*) FROM estimate_facts WHERE node_id = %s",
            (item.id,),
        ).fetchone()[0]

    assert node_count == 1
    assert projection_count == 1


def test_same_id_with_corrupted_domain_node_fails_closed(repo):
    item = estimate()
    repo.add_estimate(item)

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
                    item.id,
                ),
            )

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W501: IDENTITY_CONTENT_COLLISION",
    ):
        repo.add_estimate(item)


def test_existing_estimate_projection_mismatch_fails_closed(repo):
    item = estimate()
    repo.add_estimate(item)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE estimate_facts
                SET scenario = %s
                WHERE node_id = %s
                """,
                ("STRESS", item.id),
            )

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W529: ESTIMATE_PROJECTION_MISMATCH",
    ):
        repo.add_estimate(item)


def test_missing_projection_on_replay_fails_closed(repo):
    item = estimate()
    repo.add_estimate(item)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                "DELETE FROM estimate_facts WHERE node_id = %s",
                (item.id,),
            )

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W528: ESTIMATE_PROJECTION_WRITE_LOST",
    ):
        repo.add_estimate(item)


def test_validation_failure_writes_nothing(repo):
    item = estimate()
    bad = replace(
        item,
        id="estimate:not-canonical",
    )

    with pytest.raises(ValueError):
        repo.add_estimate(bad)

    with repo.connect() as con:
        node_count = con.execute(
            "SELECT COUNT(*) FROM domain_nodes WHERE id = %s",
            (bad.id,),
        ).fetchone()[0]

        projection_count = con.execute(
            "SELECT COUNT(*) FROM estimate_facts WHERE node_id = %s",
            (bad.id,),
        ).fetchone()[0]

    assert node_count == 0
    assert projection_count == 0


def test_database_rejects_estimate_projection_without_parent(repo):
    item = estimate()

    with repo.connect() as con:
        with pytest.raises(Exception) as exc_info:
            with con.transaction():
                con.execute(
                    """
                    INSERT INTO estimate_facts (
                        node_id,
                        node_type,
                        subject_id,
                        metric_name,
                        period_end,
                        value,
                        unit,
                        scenario,
                        model_version,
                        as_of,
                        currency
                    )
                    VALUES (
                        %s, 'Estimate', %s, %s, %s, %s,
                        %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        item.id,
                        item.subject_id,
                        item.metric_name,
                        item.period_end,
                        item.value,
                        item.unit,
                        item.scenario,
                        item.model_version,
                        item.as_of,
                        item.currency,
                    ),
                )

    exc = exc_info.value
    assert getattr(exc, "sqlstate", None) == "23503"
    assert (
        getattr(
            getattr(exc, "diag", None),
            "constraint_name",
            None,
        )
        == "estimate_domain_node_fk"
    )


def test_database_rejects_non_estimate_projection_type(repo):
    item = estimate()

    with pytest.raises(Exception) as exc_info:
        with repo.connect() as con:
            with con.transaction():
                con.execute(
                    """
                    INSERT INTO estimate_facts (
                        node_id,
                        node_type,
                        subject_id,
                        metric_name,
                        period_end,
                        value,
                        unit,
                        scenario,
                        model_version,
                        as_of,
                        currency
                    )
                    VALUES (
                        %s, 'Metric', %s, %s, %s, %s,
                        %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        item.id,
                        item.subject_id,
                        item.metric_name,
                        item.period_end,
                        item.value,
                        item.unit,
                        item.scenario,
                        item.model_version,
                        item.as_of,
                        item.currency,
                    ),
                )

    exc = exc_info.value
    assert getattr(exc, "sqlstate", None) == "23514"
    assert (
        getattr(
            getattr(exc, "diag", None),
            "constraint_name",
            None,
        )
        == "estimate_node_type"
    )


def test_estimate_write_rolls_back_domain_node_when_projection_insert_fails(
    repo,
):
    item = estimate()

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                CREATE OR REPLACE FUNCTION
                    reject_estimate_projection_for_atomicity_test()
                RETURNS trigger
                LANGUAGE plpgsql
                AS $$
                BEGIN
                    RAISE EXCEPTION
                        'forced estimate projection failure';
                END;
                $$
                """
            )
            con.execute(
                """
                CREATE TRIGGER
                    reject_estimate_projection_for_atomicity_test
                BEFORE INSERT ON estimate_facts
                FOR EACH ROW
                EXECUTE FUNCTION
                    reject_estimate_projection_for_atomicity_test()
                """
            )

    try:
        with pytest.raises(Exception):
            repo.add_estimate(item)

        with repo.connect() as con:
            node_count = con.execute(
                """
                SELECT COUNT(*)
                FROM domain_nodes
                WHERE id = %s
                """,
                (item.id,),
            ).fetchone()[0]

            projection_count = con.execute(
                """
                SELECT COUNT(*)
                FROM estimate_facts
                WHERE node_id = %s
                """,
                (item.id,),
            ).fetchone()[0]

        assert node_count == 0
        assert projection_count == 0

    finally:
        with repo.connect() as con:
            with con.transaction():
                con.execute(
                    """
                    DROP TRIGGER IF EXISTS
                        reject_estimate_projection_for_atomicity_test
                    ON estimate_facts
                    """
                )
                con.execute(
                    """
                    DROP FUNCTION IF EXISTS
                        reject_estimate_projection_for_atomicity_test()
                    """
                )
