from __future__ import annotations

import os
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from investment_domain import canonical_id
from investment_domain.nodes import Catalyst
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


def _catalyst_id(values):
    return canonical_id(
        "catalyst",
        {
            "subject_id": values["subject_id"],
            "description": values["description"],
            "as_of": values["as_of"],
            "expected_at": values["expected_at"],
        },
    )


def catalyst(**overrides):
    values = {
        "subject_id": "security:nasdaq:nvda",
        "description": "Next-generation GPU platform launch",
        "as_of": utc(2026, 9, 26, 8),
        "expected_at": utc(2027, 3, 1, 8),
    }
    values.update(overrides)

    supplied_id = values.pop("id", None)
    values["id"] = supplied_id or _catalyst_id(values)

    return Catalyst(**values)


@pytest.fixture
def repo():
    PostgreSQLMigrationManager(DSN).migrate()
    return PostgreSQLEvidenceRepository(DSN)


@pytest.fixture(autouse=True)
def clean_catalyst_state(repo):
    with repo.connect() as con:
        with con.transaction():
            con.execute("DELETE FROM catalyst_facts")
            con.execute(
                "DELETE FROM domain_nodes WHERE node_type = %s",
                ("Catalyst",),
            )

    yield

    with repo.connect() as con:
        with con.transaction():
            con.execute("DELETE FROM catalyst_facts")
            con.execute(
                "DELETE FROM domain_nodes WHERE node_type = %s",
                ("Catalyst",),
            )


def fetch_projection(repo, catalyst_id):
    with repo.connect() as con:
        return con.execute(
            """
            SELECT
                node_type,
                subject_id,
                description,
                as_of,
                expected_at
            FROM catalyst_facts
            WHERE node_id = %s
            """,
            (catalyst_id,),
        ).fetchone()


def test_add_catalyst_persists_complete_projection(repo):
    item = catalyst()

    repo.add_catalyst(item)

    row = fetch_projection(repo, item.id)

    assert row is not None
    assert row[0] == "Catalyst"
    assert row[1] == item.subject_id
    assert row[2] == item.description
    assert row[3] == item.as_of
    assert row[4] == item.expected_at

    with repo.connect() as con:
        node = con.execute(
            """
            SELECT node_type
            FROM domain_nodes
            WHERE id = %s
            """,
            (item.id,),
        ).fetchone()

    assert node == ("Catalyst",)


def test_catalyst_expected_at_may_be_null(repo):
    item = catalyst(expected_at=None)

    repo.add_catalyst(item)

    row = fetch_projection(repo, item.id)

    assert row is not None
    assert row[4] is None


def test_identical_catalyst_write_is_idempotent(repo):
    item = catalyst()

    repo.add_catalyst(item)
    repo.add_catalyst(item)

    with repo.connect() as con:
        node_count = con.execute(
            "SELECT COUNT(*) FROM domain_nodes WHERE id = %s",
            (item.id,),
        ).fetchone()[0]

        projection_count = con.execute(
            "SELECT COUNT(*) FROM catalyst_facts WHERE node_id = %s",
            (item.id,),
        ).fetchone()[0]

    assert node_count == 1
    assert projection_count == 1


def test_same_id_with_corrupted_domain_node_fails_closed(repo):
    item = catalyst()

    repo.add_catalyst(item)

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
        repo.add_catalyst(item)


def test_existing_catalyst_projection_mismatch_fails_closed(repo):
    item = catalyst()

    repo.add_catalyst(item)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE catalyst_facts
                SET description = %s
                WHERE node_id = %s
                """,
                ("corrupted catalyst", item.id),
            )

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W542: CATALYST_PROJECTION_MISMATCH",
    ):
        repo.add_catalyst(item)


def test_missing_projection_on_replay_fails_closed(repo):
    item = catalyst()

    repo.add_catalyst(item)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                "DELETE FROM catalyst_facts WHERE node_id = %s",
                (item.id,),
            )

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W541: CATALYST_PROJECTION_WRITE_LOST",
    ):
        repo.add_catalyst(item)


def test_validation_failure_writes_nothing(repo):
    item = catalyst()

    bad = replace(
        item,
        id="catalyst:not-canonical",
    )

    with pytest.raises(ValueError):
        repo.add_catalyst(bad)

    with repo.connect() as con:
        node_count = con.execute(
            "SELECT COUNT(*) FROM domain_nodes WHERE id = %s",
            (bad.id,),
        ).fetchone()[0]

        projection_count = con.execute(
            "SELECT COUNT(*) FROM catalyst_facts WHERE node_id = %s",
            (bad.id,),
        ).fetchone()[0]

    assert node_count == 0
    assert projection_count == 0


def test_database_rejects_catalyst_projection_without_parent(repo):
    item = catalyst()

    with repo.connect() as con:
        with pytest.raises(Exception) as exc_info:
            with con.transaction():
                con.execute(
                    """
                    INSERT INTO catalyst_facts (
                        node_id,
                        node_type,
                        subject_id,
                        description,
                        as_of,
                        expected_at
                    )
                    VALUES (
                        %s, 'Catalyst', %s, %s, %s, %s
                    )
                    """,
                    (
                        item.id,
                        item.subject_id,
                        item.description,
                        item.as_of,
                        item.expected_at,
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
        == "catalyst_domain_node_fk"
    )


def test_database_rejects_non_catalyst_projection_type(repo):
    item = catalyst()

    with pytest.raises(Exception) as exc_info:
        with repo.connect() as con:
            with con.transaction():
                con.execute(
                    """
                    INSERT INTO catalyst_facts (
                        node_id,
                        node_type,
                        subject_id,
                        description,
                        as_of,
                        expected_at
                    )
                    VALUES (
                        %s, 'Forecast', %s, %s, %s, %s
                    )
                    """,
                    (
                        item.id,
                        item.subject_id,
                        item.description,
                        item.as_of,
                        item.expected_at,
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
        == "catalyst_node_type"
    )


def test_catalyst_write_rolls_back_domain_node_when_projection_insert_fails(
    repo,
):
    item = catalyst()

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                CREATE OR REPLACE FUNCTION
                    reject_catalyst_projection_for_atomicity_test()
                RETURNS trigger
                LANGUAGE plpgsql
                AS $$
                BEGIN
                    RAISE EXCEPTION
                        'forced catalyst projection failure';
                END;
                $$
                """
            )

            con.execute(
                """
                CREATE TRIGGER
                    reject_catalyst_projection_for_atomicity_test
                BEFORE INSERT ON catalyst_facts
                FOR EACH ROW
                EXECUTE FUNCTION
                    reject_catalyst_projection_for_atomicity_test()
                """
            )

    try:
        with pytest.raises(Exception):
            repo.add_catalyst(item)

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
                FROM catalyst_facts
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
                        reject_catalyst_projection_for_atomicity_test
                    ON catalyst_facts
                    """
                )

                con.execute(
                    """
                    DROP FUNCTION IF EXISTS
                        reject_catalyst_projection_for_atomicity_test()
                    """
                )


def test_orphan_catalyst_projection_fails_closed(repo):
    item = catalyst()

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                ALTER TABLE catalyst_facts
                DROP CONSTRAINT catalyst_domain_node_fk
                """
            )

            con.execute(
                """
                INSERT INTO catalyst_facts (
                    node_id,
                    node_type,
                    subject_id,
                    description,
                    as_of,
                    expected_at
                )
                VALUES (
                    %s, 'Catalyst', %s, %s, %s, %s
                )
                """,
                (
                    item.id,
                    item.subject_id,
                    item.description,
                    item.as_of,
                    item.expected_at,
                ),
            )

    try:
        with pytest.raises(
            RepositoryWriteError,
            match="IDM-W543: CATALYST_ORPHAN_PROJECTION",
        ):
            repo.add_catalyst(item)

    finally:
        with repo.connect() as con:
            with con.transaction():
                con.execute(
                    "DELETE FROM catalyst_facts WHERE node_id = %s",
                    (item.id,),
                )

                con.execute(
                    """
                    ALTER TABLE catalyst_facts
                    ADD CONSTRAINT catalyst_domain_node_fk
                    FOREIGN KEY (node_id, node_type)
                    REFERENCES domain_nodes(id, node_type)
                    ON DELETE RESTRICT
                    """
                )
