from __future__ import annotations

import inspect
import os
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from investment_domain import canonical_id
from investment_domain.edges import EdgeType
from investment_domain.metrics import MetricEvidenceLink
from investment_domain.nodes import Evidence, Metric
from investment_domain.postgres_migrations import PostgreSQLMigrationManager
from investment_domain.postgres_repository import (
    PostgreSQLEvidenceRepository,
    RepositoryWriteError,
)
from investment_domain.repository import EvidenceRepository


DSN = os.getenv("IDM_POSTGRES_DSN")

pytestmark = pytest.mark.skipif(
    not DSN,
    reason="IDM_POSTGRES_DSN not configured",
)

T0 = datetime(2026, 9, 1, 8, tzinfo=timezone.utc)
T1 = datetime(2026, 9, 1, 9, tzinfo=timezone.utc)
T2 = datetime(2026, 9, 1, 10, tzinfo=timezone.utc)
T3 = datetime(2026, 9, 1, 11, tzinfo=timezone.utc)


def _metric(seed: str) -> Metric:
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


def _evidence(seed: str) -> Evidence:
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
) -> MetricEvidenceLink:
    return MetricEvidenceLink(
        metric_id=metric.id,
        evidence_id=evidence.id,
        relation=EdgeType.SUPPORTED_BY,
        created_at=T3,
    )


@pytest.fixture
def repo():
    PostgreSQLMigrationManager(DSN).migrate()
    repository = PostgreSQLEvidenceRepository(DSN)

    with repository.connect() as con:
        with con.transaction():
            con.execute("DELETE FROM domain_edges")
            con.execute("DELETE FROM catalyst_impact_facts")
            con.execute("DELETE FROM valuation_facts")
            con.execute("DELETE FROM forecast_facts")
            con.execute("DELETE FROM estimate_facts")
            con.execute("DELETE FROM calculation_facts")
            con.execute("DELETE FROM metric_facts")
            con.execute("DELETE FROM evidence_facts")
            con.execute("DELETE FROM claim_facts")
            con.execute("DELETE FROM domain_nodes")

    yield repository

    with repository.connect() as con:
        with con.transaction():
            con.execute("DELETE FROM domain_edges")
            con.execute("DELETE FROM catalyst_impact_facts")
            con.execute("DELETE FROM valuation_facts")
            con.execute("DELETE FROM forecast_facts")
            con.execute("DELETE FROM estimate_facts")
            con.execute("DELETE FROM calculation_facts")
            con.execute("DELETE FROM metric_facts")
            con.execute("DELETE FROM evidence_facts")
            con.execute("DELETE FROM claim_facts")
            con.execute("DELETE FROM domain_nodes")


def test_protocol_exposes_atomic_metric_evidence_write():
    assert hasattr(
        EvidenceRepository,
        "add_metric_with_evidence_link",
    )

    sig = inspect.signature(
        EvidenceRepository.add_metric_with_evidence_link
    )
    assert list(sig.parameters) == [
        "self",
        "metric",
        "link",
    ]


def test_postgres_repository_exposes_atomic_metric_evidence_write():
    assert hasattr(
        PostgreSQLEvidenceRepository,
        "add_metric_with_evidence_link",
    )

    sig = inspect.signature(
        PostgreSQLEvidenceRepository.add_metric_with_evidence_link
    )
    assert list(sig.parameters) == [
        "self",
        "metric",
        "link",
    ]


def test_atomic_write_persists_metric_projection_and_edge(repo):
    metric = _metric("atomic-success")
    evidence = _evidence("atomic-success")
    link = _link(metric, evidence)

    repo.add_evidence(evidence)
    repo.add_metric_with_evidence_link(metric, link)

    with repo.connect() as con:
        metric_count = con.execute(
            "SELECT COUNT(*) FROM metric_facts "
            "WHERE node_id = %s",
            (metric.id,),
        ).fetchone()[0]

        edge_count = con.execute(
            """
            SELECT COUNT(*)
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
        ).fetchone()[0]

    assert metric_count == 1
    assert edge_count == 1


def test_atomic_write_rolls_back_metric_when_evidence_missing(repo):
    metric = _metric("atomic-rollback")
    evidence = _evidence("atomic-rollback")
    link = _link(metric, evidence)

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W508: EDGE_TARGET_NOT_FOUND",
    ):
        repo.add_metric_with_evidence_link(metric, link)

    with repo.connect() as con:
        node_count = con.execute(
            "SELECT COUNT(*) FROM domain_nodes "
            "WHERE id = %s",
            (metric.id,),
        ).fetchone()[0]

        metric_count = con.execute(
            "SELECT COUNT(*) FROM metric_facts "
            "WHERE node_id = %s",
            (metric.id,),
        ).fetchone()[0]

        edge_count = con.execute(
            "SELECT COUNT(*) FROM domain_edges "
            "WHERE source_id = %s",
            (metric.id,),
        ).fetchone()[0]

    assert node_count == 0
    assert metric_count == 0
    assert edge_count == 0


def test_atomic_write_exact_replay_is_idempotent(repo):
    metric = _metric("atomic-replay")
    evidence = _evidence("atomic-replay")
    link = _link(metric, evidence)

    repo.add_evidence(evidence)

    repo.add_metric_with_evidence_link(metric, link)
    repo.add_metric_with_evidence_link(metric, link)

    with repo.connect() as con:
        metric_count = con.execute(
            "SELECT COUNT(*) FROM metric_facts "
            "WHERE node_id = %s",
            (metric.id,),
        ).fetchone()[0]

        edge_count = con.execute(
            """
            SELECT COUNT(*)
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
        ).fetchone()[0]

    assert metric_count == 1
    assert edge_count == 1


def test_atomic_write_rejects_metric_link_identity_mismatch(repo):
    metric = _metric("atomic-mismatch")
    evidence = _evidence("atomic-mismatch")
    other_metric = _metric("atomic-other")

    repo.add_evidence(evidence)

    link = MetricEvidenceLink(
        metric_id=other_metric.id,
        evidence_id=evidence.id,
        relation=EdgeType.SUPPORTED_BY,
        created_at=T3,
    )

    with pytest.raises(
        ValueError,
        match="MetricEvidenceLink.metric_id must match Metric.id",
    ):
        repo.add_metric_with_evidence_link(metric, link)

    with repo.connect() as con:
        node_count = con.execute(
            "SELECT COUNT(*) FROM domain_nodes WHERE id = %s",
            (metric.id,),
        ).fetchone()[0]

        metric_count = con.execute(
            "SELECT COUNT(*) FROM metric_facts WHERE node_id = %s",
            (metric.id,),
        ).fetchone()[0]

        edge_count = con.execute(
            "SELECT COUNT(*) FROM domain_edges WHERE source_id = %s",
            (metric.id,),
        ).fetchone()[0]

    assert node_count == 0
    assert metric_count == 0
    assert edge_count == 0


def test_atomic_write_edge_collision_rolls_back_new_metric(repo):
    metric = _metric("atomic-edge-collision")
    evidence = _evidence("atomic-edge-collision")
    first = _link(metric, evidence)

    repo.add_evidence(evidence)
    repo.add_metric_with_evidence_link(metric, first)

    conflicting = MetricEvidenceLink(
        metric_id=metric.id,
        evidence_id=evidence.id,
        relation=EdgeType.SUPPORTED_BY,
        created_at=datetime(
            2026, 9, 1, 11, 0, 1,
            tzinfo=timezone.utc,
        ),
    )

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W506: EDGE_CONTENT_COLLISION",
    ):
        repo.add_metric_with_evidence_link(
            metric,
            conflicting,
        )

    with repo.connect() as con:
        metric_count = con.execute(
            "SELECT COUNT(*) FROM metric_facts WHERE node_id = %s",
            (metric.id,),
        ).fetchone()[0]

        edge_created_at = con.execute(
            """
            SELECT created_at
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
        ).fetchone()[0]

    assert metric_count == 1
    assert edge_created_at == first.created_at
