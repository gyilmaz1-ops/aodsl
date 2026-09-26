from __future__ import annotations

import os

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from investment_domain import canonical_id
from investment_domain.nodes import Forecast
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


def forecast(**overrides):
    values = {
        "subject_id": "security:nasdaq:nvda",
        "scenario": "BASE",
        "as_of": utc(2026, 9, 26, 8),
        "model_version": "forecast-model-v1",
    }
    values.update(overrides)
    supplied_id = values.pop("id", None)
    values["id"] = supplied_id or _forecast_id(values)
    return Forecast(**values)


@pytest.fixture
def repo():
    PostgreSQLMigrationManager(DSN).migrate()
    return PostgreSQLEvidenceRepository(DSN)


@pytest.fixture(autouse=True)
def clean_forecast_state(repo):
    with repo.connect() as con:
        with con.transaction():
            con.execute("DELETE FROM forecast_facts")
            con.execute(
                "DELETE FROM domain_nodes WHERE node_type = %s",
                ("Forecast",),
            )

    yield

    with repo.connect() as con:
        with con.transaction():
            con.execute("DELETE FROM forecast_facts")
            con.execute(
                "DELETE FROM domain_nodes WHERE node_type = %s",
                ("Forecast",),
            )


def fetch_projection(repo, forecast_id):
    with repo.connect() as con:
        return con.execute(
            """
            SELECT
                node_type,
                subject_id,
                scenario,
                as_of,
                model_version
            FROM forecast_facts
            WHERE node_id = %s
            """,
            (forecast_id,),
        ).fetchone()


def test_add_forecast_persists_complete_projection(repo):
    item = forecast()

    repo.add_forecast(item)

    row = fetch_projection(repo, item.id)

    assert row is not None
    assert row[0] == "Forecast"
    assert row[1] == item.subject_id
    assert row[2] == item.scenario
    assert row[3] == item.as_of
    assert row[4] == item.model_version

    with repo.connect() as con:
        node = con.execute(
            """
            SELECT node_type
            FROM domain_nodes
            WHERE id = %s
            """,
            (item.id,),
        ).fetchone()

    assert node == ("Forecast",)


def test_identical_forecast_write_is_idempotent(repo):
    item = forecast()

    repo.add_forecast(item)
    repo.add_forecast(item)

    with repo.connect() as con:
        node_count = con.execute(
            "SELECT COUNT(*) FROM domain_nodes WHERE id = %s",
            (item.id,),
        ).fetchone()[0]

        projection_count = con.execute(
            "SELECT COUNT(*) FROM forecast_facts WHERE node_id = %s",
            (item.id,),
        ).fetchone()[0]

    assert node_count == 1
    assert projection_count == 1


def test_same_id_with_corrupted_domain_node_fails_closed(repo):
    item = forecast()
    repo.add_forecast(item)

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
        repo.add_forecast(item)


def test_existing_forecast_projection_mismatch_fails_closed(repo):
    item = forecast()
    repo.add_forecast(item)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE forecast_facts
                SET scenario = %s
                WHERE node_id = %s
                """,
                ("BULL", item.id),
            )

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W537: FORECAST_PROJECTION_MISMATCH",
    ):
        repo.add_forecast(item)


def test_missing_projection_on_replay_fails_closed(repo):
    item = forecast()
    repo.add_forecast(item)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                "DELETE FROM forecast_facts WHERE node_id = %s",
                (item.id,),
            )

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W536: FORECAST_PROJECTION_WRITE_LOST",
    ):
        repo.add_forecast(item)


def test_validation_failure_writes_nothing(repo):
    item = forecast()
    bad = replace(
        item,
        id="forecast:not-canonical",
    )

    with pytest.raises(ValueError):
        repo.add_forecast(bad)

    with repo.connect() as con:
        node_count = con.execute(
            "SELECT COUNT(*) FROM domain_nodes WHERE id = %s",
            (bad.id,),
        ).fetchone()[0]

        projection_count = con.execute(
            "SELECT COUNT(*) FROM forecast_facts WHERE node_id = %s",
            (bad.id,),
        ).fetchone()[0]

    assert node_count == 0
    assert projection_count == 0


def test_database_rejects_forecast_projection_without_parent(repo):
    item = forecast()

    with repo.connect() as con:
        with pytest.raises(Exception) as exc_info:
            with con.transaction():
                con.execute(
                    """
                    INSERT INTO forecast_facts (
                        node_id,
                        node_type,
                        subject_id,
                        scenario,
                        as_of,
                        model_version
                    )
                    VALUES (
                        %s, 'Forecast', %s, %s, %s, %s
                    )
                    """,
                    (
                        item.id,
                        item.subject_id,
                        item.scenario,
                        item.as_of,
                        item.model_version,
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
        == "forecast_domain_node_fk"
    )


def test_database_rejects_non_forecast_projection_type(repo):
    item = forecast()

    with pytest.raises(Exception) as exc_info:
        with repo.connect() as con:
            with con.transaction():
                con.execute(
                    """
                    INSERT INTO forecast_facts (
                        node_id,
                        node_type,
                        subject_id,
                        scenario,
                        as_of,
                        model_version
                    )
                    VALUES (
                        %s, 'Metric', %s, %s, %s, %s
                    )
                    """,
                    (
                        item.id,
                        item.subject_id,
                        item.scenario,
                        item.as_of,
                        item.model_version,
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
        == "forecast_node_type"
    )


def test_forecast_write_rolls_back_domain_node_when_projection_insert_fails(
    repo,
):
    item = forecast()

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                CREATE OR REPLACE FUNCTION
                    reject_forecast_projection_for_atomicity_test()
                RETURNS trigger
                LANGUAGE plpgsql
                AS $$
                BEGIN
                    RAISE EXCEPTION
                        'forced forecast projection failure';
                END;
                $$
                """
            )

            con.execute(
                """
                CREATE TRIGGER
                    reject_forecast_projection_for_atomicity_test
                BEFORE INSERT ON forecast_facts
                FOR EACH ROW
                EXECUTE FUNCTION
                    reject_forecast_projection_for_atomicity_test()
                """
            )

    try:
        with pytest.raises(Exception):
            repo.add_forecast(item)

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
                FROM forecast_facts
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
                        reject_forecast_projection_for_atomicity_test
                    ON forecast_facts
                    """
                )

                con.execute(
                    """
                    DROP FUNCTION IF EXISTS
                        reject_forecast_projection_for_atomicity_test()
                    """
                )


def test_orphan_forecast_projection_fails_closed(repo):
    item = forecast()

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                ALTER TABLE forecast_facts
                DROP CONSTRAINT forecast_domain_node_fk
                """
            )

            con.execute(
                """
                INSERT INTO forecast_facts (
                    node_id,
                    node_type,
                    subject_id,
                    scenario,
                    as_of,
                    model_version
                )
                VALUES (
                    %s, 'Forecast', %s, %s, %s, %s
                )
                """,
                (
                    item.id,
                    item.subject_id,
                    item.scenario,
                    item.as_of,
                    item.model_version,
                ),
            )

    try:
        with pytest.raises(
            RepositoryWriteError,
            match="IDM-W538: FORECAST_ORPHAN_PROJECTION",
        ):
            repo.add_forecast(item)

    finally:
        with repo.connect() as con:
            with con.transaction():
                con.execute(
                    "DELETE FROM forecast_facts WHERE node_id = %s",
                    (item.id,),
                )

                con.execute(
                    """
                    ALTER TABLE forecast_facts
                    ADD CONSTRAINT forecast_domain_node_fk
                    FOREIGN KEY (node_id, node_type)
                    REFERENCES domain_nodes(id, node_type)
                    ON DELETE RESTRICT
                    """
                )
