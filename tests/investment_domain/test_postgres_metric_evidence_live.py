from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from psycopg.errors import CheckViolation, ForeignKeyViolation

from investment_domain import (
    PostgreSQLEvidenceRepository,
    RepositoryWriteError,
    canonical_id,
)
from investment_domain.edges import EdgeType
from investment_domain.metrics import MetricEvidenceLink
from investment_domain.nodes import Evidence, Metric
from investment_domain.postgres_migrations import PostgreSQLMigrationManager


DSN = os.getenv("IDM_POSTGRES_DSN")

pytestmark = pytest.mark.skipif(
    not DSN,
    reason="IDM_POSTGRES_DSN not configured",
)

T0 = datetime(2026, 9, 1, 8, tzinfo=timezone.utc)
T1 = datetime(2026, 9, 1, 9, tzinfo=timezone.utc)
T2 = datetime(2026, 9, 1, 10, tzinfo=timezone.utc)
T3 = datetime(2026, 9, 1, 11, tzinfo=timezone.utc)


def _metric(seed: str = "metric-edge") -> Metric:
    payload = {
        "subject_id": f"company:{seed}",
        "name": "financial.revenue",
        "period_start": datetime(
            2026, 1, 1, tzinfo=timezone.utc
        ),
        "period_end": datetime(
            2026, 3, 31, tzinfo=timezone.utc
        ),
        "effective_at": datetime(
            2026, 3, 31, tzinfo=timezone.utc
        ),
        "observed_at": T0,
        "published_at": T1,
        "source_id": f"source:{seed}",
        "source_version": "1",
    }

    return Metric(
        id=canonical_id("metric", payload),
        **payload,
        value=Decimal("100.00"),
        unit="currency",
        currency="USD",
        ingested_at=T2,
    )


def _evidence(seed: str = "metric-edge") -> Evidence:
    payload = {
        "source_id": f"source:{seed}",
        "source_version": "1",
        "content_hash": "a" * 64,
        "effective_at": datetime(
            2026, 8, 31, tzinfo=timezone.utc
        ),
        "observed_at": T0,
        "published_at": T1,
    }

    return Evidence(
        id=canonical_id("evidence", payload),
        **payload,
        ingested_at=T2,
        source_uri=f"https://example.test/{seed}",
    )


def _link(
    metric: Metric,
    evidence: Evidence,
    *,
    relation: EdgeType = EdgeType.SUPPORTED_BY,
    created_at: datetime = T3,
) -> MetricEvidenceLink:
    return MetricEvidenceLink(
        metric_id=metric.id,
        evidence_id=evidence.id,
        relation=relation,
        created_at=created_at,
    )


@pytest.fixture
def repo():
    PostgreSQLMigrationManager(DSN).migrate()
    repository = PostgreSQLEvidenceRepository(DSN)

    with repository.connect() as con:
        with con.transaction():
            con.execute("DELETE FROM domain_edges")
            con.execute("DELETE FROM valuation_facts")
            con.execute("DELETE FROM forecast_facts")
            con.execute("DELETE FROM estimate_facts")
            con.execute("DELETE FROM calculation_facts")
            con.execute("DELETE FROM metric_facts")
            con.execute("DELETE FROM evidence_facts")
            con.execute("DELETE FROM claim_facts")
            con.execute("DELETE FROM domain_nodes")

    return repository


def test_live_persists_metric_supported_by_evidence(repo):
    metric = _metric("persist")
    evidence = _evidence("persist")
    link = _link(metric, evidence)

    repo.add_metric(metric)
    repo.add_evidence(evidence)
    repo.add_metric_evidence_link(link)

    with repo.connect() as con:
        row = con.execute(
            """
            SELECT
                source_id,
                source_type,
                edge_type,
                target_id,
                target_type,
                created_at
            FROM domain_edges
            WHERE source_id = %s
              AND edge_type = %s
              AND target_id = %s
            """,
            (
                metric.id,
                EdgeType.SUPPORTED_BY.value,
                evidence.id,
            ),
        ).fetchone()

    assert row == (
        metric.id,
        "Metric",
        "SUPPORTED_BY",
        evidence.id,
        "Evidence",
        link.created_at,
    )


def test_live_identical_metric_edge_replay_is_idempotent(repo):
    metric = _metric("replay")
    evidence = _evidence("replay")
    link = _link(metric, evidence)

    repo.add_metric(metric)
    repo.add_evidence(evidence)

    repo.add_metric_evidence_link(link)
    repo.add_metric_evidence_link(link)

    with repo.connect() as con:
        count = con.execute(
            """
            SELECT COUNT(*)
            FROM domain_edges
            WHERE source_id = %s
              AND edge_type = %s
              AND target_id = %s
            """,
            (
                metric.id,
                link.relation.value,
                evidence.id,
            ),
        ).fetchone()[0]

    assert count == 1


def test_live_same_metric_edge_different_created_at_fails_closed(repo):
    metric = _metric("collision")
    evidence = _evidence("collision")

    first = _link(metric, evidence)
    second = _link(
        metric,
        evidence,
        created_at=T3 + timedelta(seconds=1),
    )

    repo.add_metric(metric)
    repo.add_evidence(evidence)
    repo.add_metric_evidence_link(first)

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W506: EDGE_CONTENT_COLLISION",
    ):
        repo.add_metric_evidence_link(second)


def test_live_missing_metric_source_fails_closed(repo):
    metric = _metric("missing-source")
    evidence = _evidence("missing-source")

    repo.add_evidence(evidence)

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W507: EDGE_SOURCE_NOT_FOUND",
    ):
        repo.add_metric_evidence_link(_link(metric, evidence))

    with repo.connect() as con:
        count = con.execute(
            "SELECT COUNT(*) FROM domain_edges"
        ).fetchone()[0]

    assert count == 0


def test_live_missing_evidence_target_fails_closed(repo):
    metric = _metric("missing-target")
    evidence = _evidence("missing-target")

    repo.add_metric(metric)

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W508: EDGE_TARGET_NOT_FOUND",
    ):
        repo.add_metric_evidence_link(_link(metric, evidence))

    with repo.connect() as con:
        count = con.execute(
            "SELECT COUNT(*) FROM domain_edges"
        ).fetchone()[0]

    assert count == 0


def test_live_contradicted_by_rejected_before_persistence(repo):
    metric = _metric("illegal-relation")
    evidence = _evidence("illegal-relation")

    repo.add_metric(metric)
    repo.add_evidence(evidence)

    with pytest.raises(
        ValueError,
        match="MetricEvidenceLink.relation must be SUPPORTED_BY",
    ):
        repo.add_metric_evidence_link(
            _link(
                metric,
                evidence,
                relation=EdgeType.CONTRADICTED_BY,
            )
        )

    with repo.connect() as con:
        count = con.execute(
            """
            SELECT COUNT(*)
            FROM domain_edges
            WHERE source_id = %s
            """,
            (metric.id,),
        ).fetchone()[0]

    assert count == 0


def test_live_direct_sql_allows_metric_supported_by_evidence(repo):
    metric = _metric("sql-allowed")
    evidence = _evidence("sql-allowed")

    repo.add_metric(metric)
    repo.add_evidence(evidence)

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
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    metric.id,
                    "Metric",
                    "SUPPORTED_BY",
                    evidence.id,
                    "Evidence",
                    T3,
                ),
            )

    with repo.connect() as con:
        count = con.execute(
            """
            SELECT COUNT(*)
            FROM domain_edges
            WHERE source_id = %s
              AND edge_type = 'SUPPORTED_BY'
              AND target_id = %s
            """,
            (metric.id, evidence.id),
        ).fetchone()[0]

    assert count == 1


def test_live_direct_sql_rejects_metric_contradicted_by_evidence(repo):
    metric = _metric("sql-forbidden")
    evidence = _evidence("sql-forbidden")

    repo.add_metric(metric)
    repo.add_evidence(evidence)

    with pytest.raises(CheckViolation):
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
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        metric.id,
                        "Metric",
                        "CONTRADICTED_BY",
                        evidence.id,
                        "Evidence",
                        T3,
                    ),
                )

    with repo.connect() as con:
        count = con.execute(
            """
            SELECT COUNT(*)
            FROM domain_edges
            WHERE source_id = %s
            """,
            (metric.id,),
        ).fetchone()[0]

    assert count == 0


@pytest.mark.parametrize(
    "source_type,target_type",
    [
        ("Claim", "Evidence"),
        ("Metric", "Claim"),
        ("Evidence", "Metric"),
    ],
)
def test_live_direct_sql_rejects_spoofed_endpoint_types(
    repo,
    source_type,
    target_type,
):
    metric = _metric(
        f"spoof-{source_type.lower()}-{target_type.lower()}"
    )
    evidence = _evidence(
        f"spoof-{source_type.lower()}-{target_type.lower()}"
    )

    repo.add_metric(metric)
    repo.add_evidence(evidence)

    with pytest.raises((CheckViolation, ForeignKeyViolation)):
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
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        metric.id,
                        source_type,
                        "SUPPORTED_BY",
                        evidence.id,
                        target_type,
                        T3,
                    ),
                )
