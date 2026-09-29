from __future__ import annotations

import os
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from investment_domain import canonical_id
from investment_domain.nodes import Valuation
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


def _valuation_id(values):
    return canonical_id(
        "valuation",
        {
            "security_id": values["security_id"],
            "method": values["method"],
            "value": values["value"],
            "currency": values["currency"],
            "as_of": values["as_of"],
            "model_version": values["model_version"],
            "scenario": values["scenario"],
        },
    )


def valuation(**overrides):
    values = {
        "security_id": "security:nasdaq:nvda",
        "method": "DCF",
        "value": Decimal("250.12500"),
        "currency": "USD",
        "as_of": utc(2026, 9, 26, 8),
        "model_version": "valuation-model-v1",
        "scenario": "BASE",
    }
    values.update(overrides)
    supplied_id = values.pop("id", None)
    values["id"] = supplied_id or _valuation_id(values)
    return Valuation(**values)


@pytest.fixture
def repo():
    PostgreSQLMigrationManager(DSN).migrate()
    return PostgreSQLEvidenceRepository(DSN)


@pytest.fixture(autouse=True)
def clean_valuation_state(repo):
    with repo.connect() as con:
        with con.transaction():
            con.execute("DELETE FROM valuation_facts")
            con.execute(
                "DELETE FROM domain_nodes WHERE node_type = %s",
                ("Valuation",),
            )

    yield

    with repo.connect() as con:
        with con.transaction():
            con.execute("DELETE FROM valuation_facts")
            con.execute(
                "DELETE FROM domain_nodes WHERE node_type = %s",
                ("Valuation",),
            )


def fetch_projection(repo, valuation_id):
    with repo.connect() as con:
        return con.execute(
            """
            SELECT
                node_type,
                security_id,
                method,
                value,
                currency,
                as_of,
                model_version,
                scenario
            FROM valuation_facts
            WHERE node_id = %s
            """,
            (valuation_id,),
        ).fetchone()


def test_add_valuation_persists_complete_projection(repo):
    item = valuation()

    repo.add_valuation(item)

    row = fetch_projection(repo, item.id)

    assert row is not None
    assert row[0] == "Valuation"
    assert row[1] == item.security_id
    assert row[2] == item.method
    assert row[3] == item.value
    assert isinstance(row[3], Decimal)
    assert not isinstance(row[3], float)
    assert row[4] == item.currency
    assert row[5] == item.as_of
    assert row[6] == item.model_version
    assert row[7] == item.scenario

    with repo.connect() as con:
        node = con.execute(
            """
            SELECT node_type
            FROM domain_nodes
            WHERE id = %s
            """,
            (item.id,),
        ).fetchone()

    assert node == ("Valuation",)


def test_identical_valuation_write_is_idempotent(repo):
    item = valuation()

    repo.add_valuation(item)
    repo.add_valuation(item)

    with repo.connect() as con:
        node_count = con.execute(
            "SELECT COUNT(*) FROM domain_nodes WHERE id = %s",
            (item.id,),
        ).fetchone()[0]
        projection_count = con.execute(
            "SELECT COUNT(*) FROM valuation_facts WHERE node_id = %s",
            (item.id,),
        ).fetchone()[0]

    assert node_count == 1
    assert projection_count == 1


def test_same_id_with_corrupted_domain_node_fails_closed(repo):
    item = valuation()
    repo.add_valuation(item)

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
        repo.add_valuation(item)


def test_existing_valuation_projection_mismatch_fails_closed(repo):
    item = valuation()
    repo.add_valuation(item)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE valuation_facts
                SET scenario = %s
                WHERE node_id = %s
                """,
                ("BULL", item.id),
            )

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W533: VALUATION_PROJECTION_MISMATCH",
    ):
        repo.add_valuation(item)


def test_missing_projection_on_replay_fails_closed(repo):
    item = valuation()
    repo.add_valuation(item)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                "DELETE FROM valuation_facts WHERE node_id = %s",
                (item.id,),
            )

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W532: VALUATION_PROJECTION_WRITE_LOST",
    ):
        repo.add_valuation(item)


def test_validation_failure_writes_nothing(repo):
    item = valuation()
    bad = replace(
        item,
        id="valuation:not-canonical",
    )

    with pytest.raises(ValueError):
        repo.add_valuation(bad)

    with repo.connect() as con:
        node_count = con.execute(
            "SELECT COUNT(*) FROM domain_nodes WHERE id = %s",
            (bad.id,),
        ).fetchone()[0]
        projection_count = con.execute(
            "SELECT COUNT(*) FROM valuation_facts WHERE node_id = %s",
            (bad.id,),
        ).fetchone()[0]

    assert node_count == 0
    assert projection_count == 0


def test_database_rejects_valuation_projection_without_parent(repo):
    item = valuation()

    with repo.connect() as con:
        with pytest.raises(Exception) as exc_info:
            with con.transaction():
                con.execute(
                    """
                    INSERT INTO valuation_facts (
                        node_id,
                        node_type,
                        security_id,
                        method,
                        value,
                        currency,
                        as_of,
                        model_version,
                        scenario
                    )
                    VALUES (
                        %s, 'Valuation', %s, %s, %s,
                        %s, %s, %s, %s
                    )
                    """,
                    (
                        item.id,
                        item.security_id,
                        item.method,
                        item.value,
                        item.currency,
                        item.as_of,
                        item.model_version,
                        item.scenario,
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
        == "valuation_domain_node_fk"
    )


def test_database_rejects_non_valuation_projection_type(repo):
    item = valuation()

    with pytest.raises(Exception) as exc_info:
        with repo.connect() as con:
            with con.transaction():
                con.execute(
                    """
                    INSERT INTO valuation_facts (
                        node_id,
                        node_type,
                        security_id,
                        method,
                        value,
                        currency,
                        as_of,
                        model_version,
                        scenario
                    )
                    VALUES (
                        %s, 'Metric', %s, %s, %s,
                        %s, %s, %s, %s
                    )
                    """,
                    (
                        item.id,
                        item.security_id,
                        item.method,
                        item.value,
                        item.currency,
                        item.as_of,
                        item.model_version,
                        item.scenario,
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
        == "valuation_node_type"
    )


def test_valuation_write_rolls_back_domain_node_when_projection_insert_fails(
    repo,
):
    item = valuation()

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                CREATE OR REPLACE FUNCTION
                    reject_valuation_projection_for_atomicity_test()
                RETURNS trigger
                LANGUAGE plpgsql
                AS $$
                BEGIN
                    RAISE EXCEPTION
                        'forced valuation projection failure';
                END;
                $$
                """
            )
            con.execute(
                """
                CREATE TRIGGER
                    reject_valuation_projection_for_atomicity_test
                BEFORE INSERT ON valuation_facts
                FOR EACH ROW
                EXECUTE FUNCTION
                    reject_valuation_projection_for_atomicity_test()
                """
            )

    try:
        with pytest.raises(Exception):
            repo.add_valuation(item)

        with repo.connect() as con:
            node_count = con.execute(
                "SELECT COUNT(*) FROM domain_nodes WHERE id = %s",
                (item.id,),
            ).fetchone()[0]
            projection_count = con.execute(
                "SELECT COUNT(*) FROM valuation_facts WHERE node_id = %s",
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
                        reject_valuation_projection_for_atomicity_test
                    ON valuation_facts
                    """
                )
                con.execute(
                    """
                    DROP FUNCTION IF EXISTS
                        reject_valuation_projection_for_atomicity_test()
                    """
                )


def test_orphan_valuation_projection_fails_closed(repo):
    item = valuation()

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
                INSERT INTO valuation_facts (
                    node_id,
                    node_type,
                    security_id,
                    method,
                    value,
                    currency,
                    as_of,
                    model_version,
                    scenario
                )
                VALUES (
                    %s, 'Valuation', %s, %s, %s,
                    %s, %s, %s, %s
                )
                """,
                (
                    item.id,
                    item.security_id,
                    item.method,
                    item.value,
                    item.currency,
                    item.as_of,
                    item.model_version,
                    item.scenario,
                ),
            )

    try:
        with pytest.raises(
            RepositoryWriteError,
            match="IDM-W534: VALUATION_ORPHAN_PROJECTION",
        ):
            repo.add_valuation(item)

    finally:
        with repo.connect() as con:
            with con.transaction():
                con.execute(
                    "DELETE FROM valuation_facts WHERE node_id = %s",
                    (item.id,),
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



def test_valuation_precheck_uses_single_statement_snapshot():
    """
    R559.4E regression contract.

    The valuation node/projection existence observation must be
    performed by one SQL statement.

    Under READ COMMITTED, separate statements may observe different
    committed snapshots and can therefore manufacture a transient
    node-absent / projection-present state during exact concurrent
    replay.

    One statement containing both EXISTS predicates prevents that
    split-snapshot classification while preserving the W532/W534
    integrity guards.
    """
    from pathlib import Path

    source = Path(
        "src/investment_domain/postgres_repository.py"
    ).read_text()

    method_start = source.index(
        "    def _add_valuation_in_transaction("
    )

    next_method = source.find(
        "\n    def ",
        method_start + 5,
    )

    if next_method == -1:
        block = source[method_start:]
    else:
        block = source[method_start:next_method]

    assert (
        "node_exists, projection_exists = con.execute("
        in block
    )

    assert block.count("FROM domain_nodes") >= 1
    assert block.count("FROM valuation_facts") >= 1

    # Both observations must live inside the same SELECT statement.
    assignment_start = block.index(
        "node_exists, projection_exists = con.execute("
    )

    assignment_end = block.index(
        ").fetchone()",
        assignment_start,
    )

    observation = block[
        assignment_start:
        assignment_end
    ]

    assert observation.count("EXISTS (") == 2
    assert "FROM domain_nodes" in observation
    assert "FROM valuation_facts" in observation

    # The obsolete split-statement probes must not return.
    assert "node_exists = (" not in block
    assert "projection_exists = (" not in block

    # Integrity semantics remain fail-closed.
    assert (
        "IDM-W532: VALUATION_PROJECTION_WRITE_LOST"
        in block
    )
    assert (
        "IDM-W534: VALUATION_ORPHAN_PROJECTION"
        in block
    )
