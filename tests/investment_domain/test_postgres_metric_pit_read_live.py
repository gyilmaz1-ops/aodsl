from __future__ import annotations

import os

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from investment_domain import canonical_id
from investment_domain.edges import EdgeType
from investment_domain.metrics import MetricEvidenceLink
from investment_domain.nodes import Evidence, Metric
from investment_domain.postgres_migrations import (
    PostgreSQLMigrationManager,
)
from investment_domain.postgres_repository import (
    PostgreSQLEvidenceRepository,
    RepositoryReadError,
)


UTC = timezone.utc
DSN = os.environ.get("IDM_TEST_POSTGRES_DSN")

pytestmark = pytest.mark.skipif(
    not DSN,
    reason="IDM_TEST_POSTGRES_DSN is not configured",
)


def utc(year, month, day, hour=0, minute=0):
    return datetime(
        year,
        month,
        day,
        hour,
        minute,
        tzinfo=UTC,
    )


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


def revenue_metric(seed="pit", **overrides):
    values = {
        "subject_id": f"company:{seed}",
        "name": "financial.revenue",
        "value": Decimal("100.00"),
        "unit": "currency",
        "currency": "USD",
        "period_start": utc(2026, 4, 1),
        "period_end": utc(2026, 6, 30),
        "effective_at": utc(2026, 6, 30),
        "observed_at": utc(2026, 9, 1, 8),
        "published_at": utc(2026, 9, 1, 9),
        "ingested_at": utc(2026, 9, 1, 10),
        "source_id": f"source:{seed}",
        "source_version": "1",
        "supersedes_id": None,
    }
    values.update(overrides)

    supplied_id = values.pop("id", None)
    values["id"] = supplied_id or _metric_id(values)

    return Metric(**values)


def make_evidence(seed="pit"):
    payload = {
        "source_id": f"source:evidence:{seed}",
        "source_version": "1",
        "content_hash": (
            f"{sum(seed.encode()):064x}"[-64:]
        ),
        "effective_at": utc(2026, 6, 30),
        "observed_at": utc(2026, 9, 1, 8),
        "published_at": utc(2026, 9, 1, 9),
    }

    return Evidence(
        id=canonical_id("evidence", payload),
        **payload,
        ingested_at=utc(2026, 9, 1, 10),
        supersedes_id=None,
        source_uri=f"https://example.test/{seed}",
    )


@pytest.fixture
def repo():
    PostgreSQLMigrationManager(DSN).migrate()
    return PostgreSQLEvidenceRepository(DSN)


@pytest.fixture(autouse=True)
def clean_database(repo):
    with repo.connect() as con:
        with con.transaction():
            con.execute("DELETE FROM domain_edges")
            con.execute("DELETE FROM metric_facts")
            con.execute("DELETE FROM evidence_facts")
            con.execute("DELETE FROM claim_facts")
            con.execute("DELETE FROM domain_nodes")

    yield

    with repo.connect() as con:
        with con.transaction():
            con.execute("DELETE FROM domain_edges")
            con.execute("DELETE FROM metric_facts")
            con.execute("DELETE FROM evidence_facts")
            con.execute("DELETE FROM claim_facts")
            con.execute("DELETE FROM domain_nodes")


def setup_pair(seed="pit"):
    repo = PostgreSQLEvidenceRepository(DSN)
    metric = revenue_metric(seed)
    evidence = make_evidence(seed)

    repo.add_metric(metric)
    repo.add_evidence(evidence)
    repo.add_metric_evidence_link(
        MetricEvidenceLink(
            metric_id=metric.id,
            evidence_id=evidence.id,
            relation=EdgeType.SUPPORTED_BY,
            created_at=utc(2026, 9, 1, 10, 5),
        )
    )

    return repo, metric, evidence


def test_read_visible_metric_evidence():
    repo, metric, evidence = setup_pair()

    result = repo.evidence_for_metric_at(
        metric.id,
        utc(2026, 9, 1, 11),
    )

    assert result == (evidence,)


def test_missing_metric_fails_closed(repo):
    with pytest.raises(
        RepositoryReadError,
        match="IDM-R506: METRIC_NOT_FOUND",
    ):
        repo.evidence_for_metric_at(
            "metric:missing",
            utc(2026, 9, 1, 12),
        )


def test_stored_wrong_node_type_fails_closed(repo):
    fake_metric_id = "metric:" + ("a" * 64)

    # Persist only the domain-node anchor. Its identifier is
    # syntactically Metric-shaped, while its stored node_type is
    # deliberately Evidence. No Evidence projection is created:
    # R507 must be raised before Metric projection lookup.
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
                VALUES (%s, %s, %s, %s)
                """,
                (
                    fake_metric_id,
                    "Evidence",
                    "{}",
                    "0" * 64,
                ),
            )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R507: METRIC_TYPE_MISMATCH",
    ):
        repo.evidence_for_metric_at(
            fake_metric_id,
            utc(2026, 9, 1, 12),
        )


def test_missing_metric_projection_fails_closed(repo):
    metric = revenue_metric("missing-projection")
    repo.add_metric(metric)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                DELETE FROM metric_facts
                WHERE node_id = %s
                """,
                (metric.id,),
            )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R508: METRIC_PROJECTION_NOT_FOUND",
    ):
        repo.evidence_for_metric_at(
            metric.id,
            utc(2026, 9, 1, 12),
        )


def test_malformed_stored_metric_payload_fails_closed(repo):
    metric = revenue_metric("bad-payload")
    repo.add_metric(metric)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE domain_nodes
                SET canonical_payload =
                    canonical_payload - 'published_at'
                WHERE id = %s
                """,
                (metric.id,),
            )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R509: INVALID_STORED_METRIC_PAYLOAD",
    ):
        repo.evidence_for_metric_at(
            metric.id,
            utc(2026, 9, 1, 12),
        )


def test_metric_projection_integrity_failure_is_r510(repo):
    metric = revenue_metric("projection-corruption")
    repo.add_metric(metric)

    # value is intentionally non-identity-bearing, but it is part of
    # the complete stored Metric representation. Read reconstruction
    # must therefore still detect this projection corruption.
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
        RepositoryReadError,
        match="IDM-R510: STORED_METRIC_INTEGRITY_FAILURE",
    ):
        repo.evidence_for_metric_at(
            metric.id,
            utc(2026, 9, 1, 12),
        )


def test_metric_payload_hash_integrity_failure_is_r510(repo):
    metric = revenue_metric("hash-corruption")
    repo.add_metric(metric)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE domain_nodes
                SET payload_hash = %s
                WHERE id = %s
                """,
                ("0" * 64, metric.id),
            )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R510: STORED_METRIC_INTEGRITY_FAILURE",
    ):
        repo.evidence_for_metric_at(
            metric.id,
            utc(2026, 9, 1, 12),
        )


def _add_metric_evidence(
    repo,
    metric,
    evidence,
    *,
    created_at=None,
):
    repo.add_metric(metric)
    repo.add_evidence(evidence)
    repo.add_metric_evidence_link(
        MetricEvidenceLink(
            metric_id=metric.id,
            evidence_id=evidence.id,
            relation=EdgeType.SUPPORTED_BY,
            created_at=(
                created_at
                or utc(2026, 9, 1, 10, 5)
            ),
        )
    )


def _revision_evidence(
    seed,
    *,
    source_version,
    ingested_at,
    published_at=None,
    effective_at=None,
    supersedes_id=None,
):
    payload = {
        "source_id": f"source:evidence:{seed}",
        "source_version": source_version,
        "content_hash": (
            f"{sum((seed + source_version).encode()):064x}"[-64:]
        ),
        "effective_at": (
            effective_at
            or utc(2026, 6, 30)
        ),
        "observed_at": utc(2026, 9, 1, 8),
        "published_at": (
            published_at
            or utc(2026, 9, 1, 9)
        ),
    }

    return Evidence(
        id=canonical_id("evidence", payload),
        **payload,
        ingested_at=ingested_at,
        supersedes_id=supersedes_id,
        source_uri=(
            f"https://example.test/{seed}/"
            f"{source_version}"
        ),
    )


def test_metric_not_yet_published_returns_empty(repo):
    metric = revenue_metric(
        "metric-future-published",
        published_at=utc(2026, 9, 1, 13),
        ingested_at=utc(2026, 9, 1, 14),
    )
    evidence = make_evidence("metric-future-published")

    _add_metric_evidence(repo, metric, evidence)

    assert repo.evidence_for_metric_at(
        metric.id,
        utc(2026, 9, 1, 12),
    ) == ()


def test_metric_not_yet_ingested_returns_empty(repo):
    metric = revenue_metric(
        "metric-future-ingested",
        published_at=utc(2026, 9, 1, 9),
        ingested_at=utc(2026, 9, 1, 13),
    )
    evidence = make_evidence("metric-future-ingested")

    _add_metric_evidence(repo, metric, evidence)

    assert repo.evidence_for_metric_at(
        metric.id,
        utc(2026, 9, 1, 12),
    ) == ()


def test_metric_effective_at_is_not_visibility_gate(repo):
    metric = revenue_metric(
        "metric-future-effective",
        effective_at=utc(2026, 7, 15),
    )
    evidence = make_evidence("metric-future-effective")

    _add_metric_evidence(repo, metric, evidence)

    assert repo.evidence_for_metric_at(
        metric.id,
        utc(2026, 9, 1, 12),
    ) == (evidence,)


def test_future_metric_evidence_edge_is_not_visible(repo):
    metric = revenue_metric("future-edge")
    evidence = make_evidence("future-edge")

    _add_metric_evidence(
        repo,
        metric,
        evidence,
        created_at=utc(2026, 9, 1, 13),
    )

    assert repo.evidence_for_metric_at(
        metric.id,
        utc(2026, 9, 1, 12),
    ) == ()


def test_evidence_not_yet_published_is_not_visible(repo):
    metric = revenue_metric("evidence-future-published")
    evidence = _revision_evidence(
        "evidence-future-published",
        source_version="1",
        published_at=utc(2026, 9, 1, 13),
        ingested_at=utc(2026, 9, 1, 14),
    )

    _add_metric_evidence(repo, metric, evidence)

    assert repo.evidence_for_metric_at(
        metric.id,
        utc(2026, 9, 1, 12),
    ) == ()


def test_evidence_not_yet_ingested_is_not_visible(repo):
    metric = revenue_metric("evidence-future-ingested")
    evidence = _revision_evidence(
        "evidence-future-ingested",
        source_version="1",
        published_at=utc(2026, 9, 1, 9),
        ingested_at=utc(2026, 9, 1, 13),
    )

    _add_metric_evidence(repo, metric, evidence)

    assert repo.evidence_for_metric_at(
        metric.id,
        utc(2026, 9, 1, 12),
    ) == ()


def test_evidence_effective_at_is_not_visibility_gate(repo):
    metric = revenue_metric("evidence-future-effective")
    evidence = _revision_evidence(
        "evidence-future-effective",
        source_version="1",
        effective_at=utc(2027, 1, 1),
        ingested_at=utc(2026, 9, 1, 10),
    )

    _add_metric_evidence(repo, metric, evidence)

    assert repo.evidence_for_metric_at(
        metric.id,
        utc(2026, 9, 1, 12),
    ) == (evidence,)


def test_active_evidence_revision_replaces_predecessor(repo):
    metric = revenue_metric("revision-active")

    first = _revision_evidence(
        "revision-active",
        source_version="1",
        ingested_at=utc(2026, 9, 1, 10),
    )
    second = _revision_evidence(
        "revision-active",
        source_version="2",
        ingested_at=utc(2026, 9, 1, 11),
        supersedes_id=first.id,
    )

    repo.add_metric(metric)
    repo.add_evidence(first)
    repo.add_evidence(second)

    for evidence in (first, second):
        repo.add_metric_evidence_link(
            MetricEvidenceLink(
                metric_id=metric.id,
                evidence_id=evidence.id,
                relation=EdgeType.SUPPORTED_BY,
                created_at=utc(2026, 9, 1, 11, 5),
            )
        )

    assert repo.evidence_for_metric_at(
        metric.id,
        utc(2026, 9, 1, 12),
    ) == (second,)


def test_provenance_is_not_inherited_by_successor(repo):
    metric = revenue_metric("no-inheritance")

    first = _revision_evidence(
        "no-inheritance",
        source_version="1",
        ingested_at=utc(2026, 9, 1, 10),
    )
    second = _revision_evidence(
        "no-inheritance",
        source_version="2",
        ingested_at=utc(2026, 9, 1, 11),
        supersedes_id=first.id,
    )

    repo.add_metric(metric)
    repo.add_evidence(first)
    repo.add_evidence(second)

    repo.add_metric_evidence_link(
        MetricEvidenceLink(
            metric_id=metric.id,
            evidence_id=first.id,
            relation=EdgeType.SUPPORTED_BY,
            created_at=utc(2026, 9, 1, 10, 5),
        )
    )

    assert repo.evidence_for_metric_at(
        metric.id,
        utc(2026, 9, 1, 12),
    ) == ()


def test_explicit_successor_relink_restores_provenance(repo):
    metric = revenue_metric("explicit-relink")

    first = _revision_evidence(
        "explicit-relink",
        source_version="1",
        ingested_at=utc(2026, 9, 1, 10),
    )
    second = _revision_evidence(
        "explicit-relink",
        source_version="2",
        ingested_at=utc(2026, 9, 1, 11),
        supersedes_id=first.id,
    )

    repo.add_metric(metric)
    repo.add_evidence(first)
    repo.add_evidence(second)

    repo.add_metric_evidence_link(
        MetricEvidenceLink(
            metric_id=metric.id,
            evidence_id=second.id,
            relation=EdgeType.SUPPORTED_BY,
            created_at=utc(2026, 9, 1, 11, 5),
        )
    )

    assert repo.evidence_for_metric_at(
        metric.id,
        utc(2026, 9, 1, 12),
    ) == (second,)


def test_metric_evidence_order_is_deterministic(repo):
    metric = revenue_metric("deterministic-order")

    late = _revision_evidence(
        "order-late",
        source_version="1",
        published_at=utc(2026, 9, 1, 9),
        ingested_at=utc(2026, 9, 1, 11),
    )
    early = _revision_evidence(
        "order-early",
        source_version="1",
        published_at=utc(2026, 9, 1, 9, 30),
        ingested_at=utc(2026, 9, 1, 10),
    )

    repo.add_metric(metric)

    for evidence in (late, early):
        repo.add_evidence(evidence)
        repo.add_metric_evidence_link(
            MetricEvidenceLink(
                metric_id=metric.id,
                evidence_id=evidence.id,
                relation=EdgeType.SUPPORTED_BY,
                created_at=utc(2026, 9, 1, 11, 5),
            )
        )

    result = repo.evidence_for_metric_at(
        metric.id,
        utc(2026, 9, 1, 12),
    )

    assert result == (early, late)


def test_metric_pit_read_is_pure(repo):
    metric = revenue_metric("read-purity")
    evidence = make_evidence("read-purity")

    _add_metric_evidence(repo, metric, evidence)

    with repo.connect() as con:
        before = (
            con.execute(
                "SELECT COUNT(*) FROM domain_nodes"
            ).fetchone()[0],
            con.execute(
                "SELECT COUNT(*) FROM metric_facts"
            ).fetchone()[0],
            con.execute(
                "SELECT COUNT(*) FROM evidence_facts"
            ).fetchone()[0],
            con.execute(
                "SELECT COUNT(*) FROM domain_edges"
            ).fetchone()[0],
        )

    first = repo.evidence_for_metric_at(
        metric.id,
        utc(2026, 9, 1, 12),
    )
    second = repo.evidence_for_metric_at(
        metric.id,
        utc(2026, 9, 1, 12),
    )

    with repo.connect() as con:
        after = (
            con.execute(
                "SELECT COUNT(*) FROM domain_nodes"
            ).fetchone()[0],
            con.execute(
                "SELECT COUNT(*) FROM metric_facts"
            ).fetchone()[0],
            con.execute(
                "SELECT COUNT(*) FROM evidence_facts"
            ).fetchone()[0],
            con.execute(
                "SELECT COUNT(*) FROM domain_edges"
            ).fetchone()[0],
        )

    assert first == second == (evidence,)
    assert after == before


def test_metric_supersedes_id_does_not_trigger_revision_traversal(repo):
    predecessor = revenue_metric("metric-predecessor")

    successor = revenue_metric(
        "metric-successor",
        supersedes_id=predecessor.id,
    )
    evidence = make_evidence("metric-successor")

    # IDM-005D reconstructs supersedes_id but does not resolve
    # Metric revision lineage. The predecessor is deliberately absent.
    _add_metric_evidence(repo, successor, evidence)

    assert repo.evidence_for_metric_at(
        successor.id,
        utc(2026, 9, 1, 12),
    ) == (evidence,)


def test_linked_evidence_projection_missing_is_r503(repo):
    metric = revenue_metric("linked-evidence-missing")
    evidence = make_evidence("linked-evidence-missing")

    _add_metric_evidence(repo, metric, evidence)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                DELETE FROM evidence_facts
                WHERE node_id = %s
                """,
                (evidence.id,),
            )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R503: LINKED_EVIDENCE_NOT_FOUND",
    ):
        repo.evidence_for_metric_at(
            metric.id,
            utc(2026, 9, 1, 12),
        )


def test_linked_evidence_integrity_failure_is_r505(repo):
    metric = revenue_metric("evidence-integrity")
    evidence = make_evidence("evidence-integrity")

    _add_metric_evidence(repo, metric, evidence)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE domain_nodes
                SET payload_hash = %s
                WHERE id = %s
                """,
                ("0" * 64, evidence.id),
            )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R505: STORED_NODE_INTEGRITY_FAILURE",
    ):
        repo.evidence_for_metric_at(
            metric.id,
            utc(2026, 9, 1, 12),
        )


def test_linked_evidence_invalid_stored_projection_is_r505(repo):
    metric = revenue_metric("evidence-invalid-projection")
    evidence = make_evidence("evidence-invalid-projection")
    _add_metric_evidence(repo, metric, evidence)

    # evidence_facts permits an empty TEXT source_version at the
    # PostgreSQL layer, but the reconstructed Evidence is invalid
    # under the domain contract. Persistent corruption must fail
    # closed as R505 rather than leaking DomainValidationError.
    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE evidence_facts
                SET source_version = ''
                WHERE node_id = %s
                """,
                (evidence.id,),
            )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R505: STORED_NODE_INTEGRITY_FAILURE",
    ):
        repo.evidence_for_metric_at(
            metric.id,
            utc(2026, 9, 1, 12),
        )
