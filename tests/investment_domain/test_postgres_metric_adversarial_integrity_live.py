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
    RepositoryWriteError,
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


def revenue_metric(seed="adversarial", **overrides):
    values = {
        "subject_id": f"company:{seed}",
        "name": "financial.revenue",
        "value": Decimal("100.00"),
        "unit": "currency",
        "currency": "USD",
        "period_start": utc(2026, 1, 1),
        "period_end": utc(2026, 6, 30),
        "effective_at": utc(2026, 6, 30),
        "observed_at": utc(2026, 7, 5),
        "published_at": utc(2026, 7, 5),
        "ingested_at": utc(2026, 7, 10),
        "source_id": f"source:{seed}",
        "source_version": "1",
        "supersedes_id": None,
    }
    values.update(overrides)
    supplied_id = values.pop("id", None)
    values["id"] = supplied_id or _metric_id(values)
    return Metric(**values)


def restatement(predecessor, **overrides):
    values = {
        "subject_id": predecessor.subject_id,
        "name": predecessor.name,
        "value": Decimal("105.00"),
        "unit": predecessor.unit,
        "currency": predecessor.currency,
        "period_start": predecessor.period_start,
        "period_end": predecessor.period_end,
        "effective_at": predecessor.effective_at,
        "observed_at": utc(2026, 8, 15),
        "published_at": utc(2026, 8, 15),
        "ingested_at": utc(2026, 8, 20),
        "source_id": predecessor.source_id,
        "source_version": "2",
        "supersedes_id": predecessor.id,
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


def setup_lineage(repo, seed):
    root = revenue_metric(seed)
    successor = restatement(root)
    repo.add_metric(root)
    repo.add_metric(successor)
    return root, successor


# AI001
def test_active_metric_rejects_canonical_payload_tamper(repo):
    root = revenue_metric("payload-tamper")
    repo.add_metric(root)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE domain_nodes
                SET canonical_payload =
                    jsonb_set(
                        canonical_payload,
                        '{value}',
                        '"999.00"'::jsonb
                    )
                WHERE id = %s
                """,
                (root.id,),
            )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R510: STORED_METRIC_INTEGRITY_FAILURE",
    ):
        repo.active_metric_at(
            root.id,
            utc(2026, 9, 1),
        )


# AI002
def test_active_metric_rejects_malformed_canonical_payload(repo):
    root = revenue_metric("malformed-payload")
    repo.add_metric(root)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE domain_nodes
                SET canonical_payload =
                    canonical_payload - 'published_at'
                WHERE id = %s
                """,
                (root.id,),
            )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R509: INVALID_STORED_METRIC_PAYLOAD",
    ):
        repo.active_metric_at(
            root.id,
            utc(2026, 9, 1),
        )


# AI003
def test_active_metric_rejects_payload_hash_tamper(repo):
    root = revenue_metric("hash-tamper")
    repo.add_metric(root)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE domain_nodes
                SET payload_hash = %s
                WHERE id = %s
                """,
                ("0" * 64, root.id),
            )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R510: STORED_METRIC_INTEGRITY_FAILURE",
    ):
        repo.active_metric_at(
            root.id,
            utc(2026, 9, 1),
        )


# AI004
def test_active_metric_rejects_projection_tamper(repo):
    root = revenue_metric("projection-tamper")
    repo.add_metric(root)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE metric_facts
                SET value = %s
                WHERE node_id = %s
                """,
                (Decimal("999.00"), root.id),
            )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R510: STORED_METRIC_INTEGRITY_FAILURE",
    ):
        repo.active_metric_at(
            root.id,
            utc(2026, 9, 1),
        )


# AI005
def test_active_metric_rejects_corrupt_non_anchor_lineage_member(repo):
    root, successor = setup_lineage(
        repo,
        "lineage-member-corrupt",
    )

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE metric_facts
                SET value = %s
                WHERE node_id = %s
                """,
                (Decimal("999.00"), successor.id),
            )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R510: STORED_METRIC_INTEGRITY_FAILURE",
    ):
        repo.active_metric_at(
            root.id,
            utc(2026, 7, 20),
        )


# AI015
@pytest.mark.parametrize("anchor_index", [0, 1, 2])
def test_active_metric_is_anchor_independent_across_three_revision_lineage(
    repo,
    anchor_index,
):
    root = revenue_metric("anchor-independent")
    second = restatement(root)
    third = restatement(
        second,
        value=Decimal("110.00"),
        observed_at=utc(2026, 9, 15),
        published_at=utc(2026, 9, 15),
        ingested_at=utc(2026, 9, 20),
        source_version="3",
    )

    repo.add_metric(root)
    repo.add_metric(second)
    repo.add_metric(third)

    lineage = (root, second, third)

    assert repo.active_metric_at(
        lineage[anchor_index].id,
        utc(2026, 9, 25),
    ) == third


# AI016
def test_future_restatement_cannot_change_historical_resolution(repo):
    root = revenue_metric("future-restatement")
    successor = restatement(root)

    repo.add_metric(root)

    historical_before = repo.active_metric_at(
        root.id,
        utc(2026, 8, 1),
    )

    repo.add_metric(successor)

    historical_after = repo.active_metric_at(
        root.id,
        utc(2026, 8, 1),
    )

    assert historical_before == root
    assert historical_after == root


# AI017
def test_published_at_equal_to_cutoff_is_visible(repo):
    metric = revenue_metric(
        "published-boundary",
        observed_at=utc(2026, 7, 5, 9),
        published_at=utc(2026, 7, 10, 10),
        ingested_at=utc(2026, 7, 10, 10),
    )
    repo.add_metric(metric)

    cutoff = utc(2026, 7, 10, 10)

    assert repo.active_metric_at(
        metric.id,
        cutoff,
    ) == metric


# AI018
def test_ingested_at_equal_to_cutoff_is_visible(repo):
    metric = revenue_metric(
        "ingested-boundary",
        observed_at=utc(2026, 7, 5, 9),
        published_at=utc(2026, 7, 10, 9),
        ingested_at=utc(2026, 7, 10, 10),
    )
    repo.add_metric(metric)

    cutoff = utc(2026, 7, 10, 10)

    assert repo.active_metric_at(
        metric.id,
        cutoff,
    ) == metric


# AI019
def test_effective_at_does_not_determine_active_revision(repo):
    root = revenue_metric(
        "effective-not-gate",
        effective_at=utc(2026, 6, 30),
        observed_at=utc(2026, 7, 5),
        published_at=utc(2026, 7, 5),
        ingested_at=utc(2026, 7, 10),
    )
    successor = restatement(
        root,
        value=Decimal("105.00"),
        effective_at=utc(2026, 3, 31),
        observed_at=utc(2026, 8, 15),
        published_at=utc(2026, 8, 15),
        ingested_at=utc(2026, 8, 20),
        source_version="2",
    )

    repo.add_metric(root)
    repo.add_metric(successor)

    assert successor.effective_at < root.effective_at

    assert repo.active_metric_at(
        root.id,
        utc(2026, 8, 21),
    ) == successor


# AI020
def test_no_available_revision_returns_none_from_any_lineage_anchor(repo):
    root = revenue_metric(
        "none-before-visible",
        observed_at=utc(2026, 7, 5),
        published_at=utc(2026, 7, 10),
        ingested_at=utc(2026, 7, 10),
    )
    successor = restatement(root)

    repo.add_metric(root)
    repo.add_metric(successor)

    cutoff = utc(2026, 7, 9)

    assert repo.active_metric_at(root.id, cutoff) is None
    assert repo.active_metric_at(successor.id, cutoff) is None


# AI026
def test_active_metric_resolution_is_independent_of_node_id_order(repo):
    root = revenue_metric("row-order")
    second = restatement(root)
    third = restatement(
        second,
        value=Decimal("110.00"),
        observed_at=utc(2026, 9, 15),
        published_at=utc(2026, 9, 15),
        ingested_at=utc(2026, 9, 20),
        source_version="3",
    )

    repo.add_metric(root)
    repo.add_metric(second)
    repo.add_metric(third)

    # Repository SQL orders rows by node_id rather than revision chronology.
    # Correct resolution therefore must come from explicit supersedes_id
    # topology and temporal semantics, not returned row position.
    expected = third
    cutoff = utc(2026, 9, 25)

    assert repo.active_metric_at(root.id, cutoff) == expected
    assert repo.active_metric_at(second.id, cutoff) == expected
    assert repo.active_metric_at(third.id, cutoff) == expected


def make_evidence(seed, *, hour=9):
    payload = {
        "source_id": f"source:evidence:{seed}",
        "source_version": "1",
        "content_hash": f"{sum(seed.encode()):064x}"[-64:],
        "effective_at": utc(2026, 6, 30),
        "observed_at": utc(2026, 9, 1, hour),
        "published_at": utc(2026, 9, 1, hour),
    }

    return Evidence(
        id=canonical_id("evidence", payload),
        **payload,
        ingested_at=utc(2026, 9, 1, hour + 1),
        supersedes_id=None,
        source_uri=f"https://example.test/{seed}",
    )


def add_metric_evidence(repo, metric, evidence, *, minute):
    repo.add_evidence(evidence)
    repo.add_metric_evidence_link(
        MetricEvidenceLink(
            metric_id=metric.id,
            evidence_id=evidence.id,
            relation=EdgeType.SUPPORTED_BY,
            created_at=utc(2026, 9, 1, 11, minute),
        )
    )


# AI021 + AI022
def test_metric_revisions_do_not_inherit_each_others_provenance(repo):
    root = revenue_metric("provenance-isolation")
    successor = restatement(root)

    repo.add_metric(root)
    repo.add_metric(successor)

    root_evidence = make_evidence("root-provenance", hour=8)
    successor_evidence = make_evidence(
        "successor-provenance",
        hour=9,
    )

    add_metric_evidence(
        repo,
        root,
        root_evidence,
        minute=1,
    )
    add_metric_evidence(
        repo,
        successor,
        successor_evidence,
        minute=2,
    )

    cutoff = utc(2026, 9, 1, 12)

    assert repo.evidence_for_metric_at(
        root.id,
        cutoff,
    ) == (root_evidence,)

    assert repo.evidence_for_metric_at(
        successor.id,
        cutoff,
    ) == (successor_evidence,)


# AI023
def test_future_evidence_revision_does_not_leak_into_historical_metric_provenance(
    repo,
):
    metric = revenue_metric("future-evidence-revision")
    repo.add_metric(metric)

    root_evidence = make_evidence(
        "future-evidence",
        hour=8,
    )

    successor_payload = {
        "source_id": root_evidence.source_id,
        "source_version": "2",
        "content_hash": ("f" * 64),
        "effective_at": root_evidence.effective_at,
        "observed_at": utc(2026, 9, 2, 8),
        "published_at": utc(2026, 9, 2, 9),
    }

    successor_evidence = Evidence(
        id=canonical_id(
            "evidence",
            successor_payload,
        ),
        **successor_payload,
        ingested_at=utc(2026, 9, 2, 10),
        supersedes_id=root_evidence.id,
        source_uri="https://example.test/future-evidence-v2",
    )

    repo.add_evidence(root_evidence)
    repo.add_evidence(successor_evidence)

    repo.add_metric_evidence_link(
        MetricEvidenceLink(
            metric_id=metric.id,
            evidence_id=root_evidence.id,
            relation=EdgeType.SUPPORTED_BY,
            created_at=utc(2026, 9, 1, 11),
        )
    )

    assert repo.evidence_for_metric_at(
        metric.id,
        utc(2026, 9, 1, 12),
    ) == (root_evidence,)


# AI024
def test_metric_provenance_read_does_not_follow_metric_revision_lineage(repo):
    root = revenue_metric("concrete-provenance")
    successor = restatement(root)

    repo.add_metric(root)
    repo.add_metric(successor)

    evidence = make_evidence(
        "successor-only-provenance",
        hour=9,
    )

    add_metric_evidence(
        repo,
        successor,
        evidence,
        minute=1,
    )

    cutoff = utc(2026, 9, 1, 12)

    assert repo.evidence_for_metric_at(
        root.id,
        cutoff,
    ) == ()

    assert repo.evidence_for_metric_at(
        successor.id,
        cutoff,
    ) == (evidence,)


# AI027 + AI028 + AI029
def test_failed_revision_append_is_atomic_and_preserves_predecessor_state(
    repo,
):
    root = revenue_metric("atomic-failed-append")
    repo.add_metric(root)

    evidence = make_evidence(
        "atomic-predecessor-evidence",
        hour=8,
    )

    add_metric_evidence(
        repo,
        root,
        evidence,
        minute=1,
    )

    before_cutoff = utc(2026, 9, 1, 12)

    before_metric = repo.active_metric_at(
        root.id,
        before_cutoff,
    )
    before_evidence = repo.evidence_for_metric_at(
        root.id,
        before_cutoff,
    )

    invalid_successor = restatement(
        root,
        subject_id="company:different-subject",
        source_version="2",
    )

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W519: METRIC_REVISION_KEY_MISMATCH",
    ) as exc_info:
        repo.add_metric(invalid_successor)

    assert "IDM-W519: METRIC_REVISION_KEY_MISMATCH" in str(
        exc_info.value
    )

    with repo.connect() as con:
        domain_node_count = con.execute(
            """
            SELECT COUNT(*)
            FROM domain_nodes
            WHERE id = %s
            """,
            (invalid_successor.id,),
        ).fetchone()[0]

        metric_fact_count = con.execute(
            """
            SELECT COUNT(*)
            FROM metric_facts
            WHERE node_id = %s
            """,
            (invalid_successor.id,),
        ).fetchone()[0]

    assert domain_node_count == 0
    assert metric_fact_count == 0

    assert repo.active_metric_at(
        root.id,
        before_cutoff,
    ) == before_metric

    assert repo.evidence_for_metric_at(
        root.id,
        before_cutoff,
    ) == before_evidence


# AI007 / W512
def test_revision_append_rejects_missing_predecessor(repo):
    missing_id = "metric:" + ("a" * 64)

    successor = revenue_metric(
        "missing-predecessor",
        supersedes_id=missing_id,
    )

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W512: REVISION_PREDECESSOR_NOT_FOUND",
    ):
        repo.add_metric(successor)


# AI009 / W514
def test_revision_append_rejects_non_monotonic_ingestion(repo):
    root = revenue_metric("non-monotonic")
    repo.add_metric(root)

    successor = restatement(
        root,
        observed_at=root.observed_at,
        published_at=root.published_at,
        ingested_at=root.ingested_at,
        source_version="2",
    )

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W514: NON_MONOTONIC_REVISION_INGESTION",
    ):
        repo.add_metric(successor)


# AI010 / W515
def test_revision_append_rejects_branch(repo):
    root = revenue_metric("branch")
    first = restatement(root)

    second = restatement(
        root,
        value=Decimal("110.00"),
        observed_at=utc(2026, 9, 10),
        published_at=utc(2026, 9, 10),
        ingested_at=utc(2026, 9, 15),
        source_version="3",
    )

    repo.add_metric(root)
    repo.add_metric(first)

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W515: REVISION_BRANCH_FORBIDDEN",
    ):
        repo.add_metric(second)


# AI008 / W519
def test_revision_append_rejects_revision_key_change(repo):
    root = revenue_metric("revision-key")
    repo.add_metric(root)

    successor = restatement(
        root,
        currency="EUR",
        source_version="2",
    )

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W519: METRIC_REVISION_KEY_MISMATCH",
    ):
        repo.add_metric(successor)


# AI006 / W520
def test_revision_append_rejects_corrupt_predecessor(repo):
    root = revenue_metric("corrupt-predecessor")
    repo.add_metric(root)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE metric_facts
                SET value = %s
                WHERE node_id = %s
                """,
                (Decimal("999.00"), root.id),
            )

    successor = restatement(root)

    with pytest.raises(
        RepositoryWriteError,
        match=(
            "IDM-W520: "
            "METRIC_REVISION_PREDECESSOR_INTEGRITY_FAILURE"
        ),
    ):
        repo.add_metric(successor)


# AI012 — direct SQL FK defense
def test_direct_sql_rejects_missing_metric_predecessor(repo):
    root = revenue_metric("sql-missing-predecessor")
    repo.add_metric(root)

    missing_id = "metric:" + ("b" * 64)

    with repo.connect() as con:
        with pytest.raises(Exception) as exc_info:
            with con.transaction():
                con.execute(
                    """
                    UPDATE metric_facts
                    SET supersedes_id = %s
                    WHERE node_id = %s
                    """,
                    (missing_id, root.id),
                )

    exc = exc_info.value
    assert getattr(exc, "sqlstate", None) == "23503"
    assert (
        getattr(
            getattr(exc, "diag", None),
            "constraint_name",
            None,
        )
        == "metric_supersedes_fk"
    )


# AI013 — direct SQL self-supersession defense
def test_direct_sql_rejects_metric_self_supersession(repo):
    root = revenue_metric("sql-self")
    repo.add_metric(root)

    with repo.connect() as con:
        with pytest.raises(Exception) as exc_info:
            with con.transaction():
                con.execute(
                    """
                    UPDATE metric_facts
                    SET supersedes_id = node_id
                    WHERE node_id = %s
                    """,
                    (root.id,),
                )

    exc = exc_info.value
    assert getattr(exc, "sqlstate", None) == "23514"
    assert (
        getattr(
            getattr(exc, "diag", None),
            "constraint_name",
            None,
        )
        == "metric_no_self_supersession"
    )


# AI011 — direct SQL branch defense
def test_direct_sql_rejects_second_metric_successor(repo):
    root = revenue_metric("sql-branch")
    first = restatement(root)

    repo.add_metric(root)
    repo.add_metric(first)

    candidate = restatement(
        first,
        value=Decimal("110.00"),
        observed_at=utc(2026, 9, 10),
        published_at=utc(2026, 9, 10),
        ingested_at=utc(2026, 9, 15),
        source_version="3",
    )
    repo.add_metric(candidate)

    with repo.connect() as con:
        with pytest.raises(Exception) as exc_info:
            with con.transaction():
                con.execute(
                    """
                    UPDATE metric_facts
                    SET supersedes_id = %s
                    WHERE node_id = %s
                    """,
                    (root.id, candidate.id),
                )

    exc = exc_info.value
    assert getattr(exc, "sqlstate", None) == "23505"
    assert (
        getattr(
            getattr(exc, "diag", None),
            "constraint_name",
            None,
        )
        == "metric_one_successor_per_predecessor"
    )


# AI025 — persisted semantically malformed lineage must fail closed
def test_active_metric_rejects_persisted_revision_key_corruption(repo):
    root = revenue_metric("malformed-lineage")
    successor = restatement(root)

    repo.add_metric(root)
    repo.add_metric(successor)

    # Corrupt both projection and canonical payload coherently enough that
    # per-node storage integrity remains internally consistent only if the
    # canonical domain node is also changed. Instead, mutate the explicit
    # lineage pointer into a semantically invalid topology that remains
    # structurally admissible to PostgreSQL.
    #
    # Detach successor from root. The anchor now has supersedes_id=NULL,
    # making it a second root rather than a revision of root. This does not
    # produce a malformed lineage from successor's own anchor perspective,
    # so use a stronger corruption: point root at successor. PostgreSQL's
    # self/branch/FK constraints permit the resulting two-node cycle.
    #
    # This state is unreachable through repository writes, but demonstrates
    # the read boundary fails closed if persistent state is externally
    # corrupted.
    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE metric_facts
                SET supersedes_id = %s
                WHERE node_id = %s
                """,
                (successor.id, root.id),
            )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R510: STORED_METRIC_INTEGRITY_FAILURE",
    ):
        repo.active_metric_at(
            root.id,
            utc(2026, 9, 1),
        )
