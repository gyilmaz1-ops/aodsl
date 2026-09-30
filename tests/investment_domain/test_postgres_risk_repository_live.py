from datetime import datetime, timezone

import pytest

from investment_domain.identity import canonical_id
from investment_domain.nodes import Risk
from investment_domain.postgres_migrations import PostgreSQLMigrationManager
from investment_domain.postgres_repository import PostgreSQLEvidenceRepository


DSN = "postgresql://aodsl:aodsl_cert@127.0.0.1:55433/aodsl_cert"


@pytest.fixture
def repo():
    PostgreSQLMigrationManager(DSN).migrate()
    repository = PostgreSQLEvidenceRepository(DSN)

    with repository.connect() as con:
        with con.transaction():
            con.execute(
                """
                DELETE FROM risk_facts
                """
            )
            con.execute(
                """
                DELETE FROM domain_nodes
                WHERE node_type = 'Risk'
                """
            )

    return repository


def make_risk() -> Risk:
    kwargs = {
        "subject_id": "security:TEST",
        "description": "Demand contraction risk",
        "as_of": datetime(
            2026,
            9,
            30,
            12,
            0,
            tzinfo=timezone.utc,
        ),
    }

    return Risk(
        id=canonical_id("risk", kwargs),
        **kwargs,
    )


def test_add_risk_persists_domain_node_and_projection(repo):
    risk = make_risk()

    repo.add_risk(risk)

    with repo.connect() as con:
        node_row = con.execute(
            """
            SELECT node_type
            FROM domain_nodes
            WHERE id = %s
            """,
            (risk.id,),
        ).fetchone()

        projection_row = con.execute(
            """
            SELECT
                node_type,
                subject_id,
                description,
                as_of
            FROM risk_facts
            WHERE node_id = %s
            """,
            (risk.id,),
        ).fetchone()

    assert node_row is not None
    assert str(node_row[0]) == "Risk"

    assert projection_row is not None
    assert (
        str(projection_row[0]),
        str(projection_row[1]),
        str(projection_row[2]),
        projection_row[3],
    ) == (
        "Risk",
        risk.subject_id,
        risk.description,
        risk.as_of,
    )


def test_add_risk_exact_replay_is_idempotent(repo):
    risk = make_risk()

    repo.add_risk(risk)
    repo.add_risk(risk)

    with repo.connect() as con:
        node_count = con.execute(
            """
            SELECT COUNT(*)
            FROM domain_nodes
            WHERE id = %s
            """,
            (risk.id,),
        ).fetchone()[0]

        projection_count = con.execute(
            """
            SELECT COUNT(*)
            FROM risk_facts
            WHERE node_id = %s
            """,
            (risk.id,),
        ).fetchone()[0]

    assert node_count == 1
    assert projection_count == 1


def test_add_risk_rejects_missing_projection(repo):
    risk = make_risk()
    repo.add_risk(risk)

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
        Exception,
        match="IDM-W554: RISK_PROJECTION_WRITE_LOST",
    ):
        repo.add_risk(risk)


def test_add_risk_rejects_projection_mismatch(repo):
    risk = make_risk()
    repo.add_risk(risk)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE risk_facts
                SET description = %s
                WHERE node_id = %s
                """,
                (
                    "Tampered risk description",
                    risk.id,
                ),
            )

    with pytest.raises(
        Exception,
        match="IDM-W555: RISK_PROJECTION_MISMATCH",
    ):
        repo.add_risk(risk)


def test_add_risk_rejects_orphan_projection(repo):
    risk = make_risk()
    repo.add_risk(risk)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                ALTER TABLE risk_facts
                DROP CONSTRAINT risk_domain_node_fk
                """
            )
            con.execute(
                """
                DELETE FROM domain_nodes
                WHERE id = %s
                """,
                (risk.id,),
            )

    try:
        with pytest.raises(
            Exception,
            match="IDM-W556: RISK_ORPHAN_PROJECTION",
        ):
            repo.add_risk(risk)
    finally:
        with repo.connect() as con:
            with con.transaction():
                con.execute(
                    """
                    DELETE FROM risk_facts
                    WHERE node_id = %s
                    """,
                    (risk.id,),
                )
                con.execute(
                    """
                    ALTER TABLE risk_facts
                    ADD CONSTRAINT risk_domain_node_fk
                    FOREIGN KEY (node_id, node_type)
                    REFERENCES domain_nodes(id, node_type)
                    ON DELETE RESTRICT
                    """
                )
