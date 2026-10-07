from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from investment_domain import canonical_id
from investment_domain.edges import EdgeType
from investment_domain.nodes import Evidence, Metric, Valuation
from investment_domain.postgres_migrations import PostgreSQLMigrationManager
from investment_domain.postgres_repository import PostgreSQLEvidenceRepository
from investment_domain.sec_financial_fact_projection import (
    project_sec_financial_fact,
)
from investment_domain.sec_financial_facts import ExtractedFinancialFact


UTC = timezone.utc
DSN = os.getenv("IDM_TEST_POSTGRES_DSN")

pytestmark = pytest.mark.skipif(
    not DSN,
    reason="IDM_TEST_POSTGRES_DSN is not configured",
)


def utc(year, month, day, hour=0):
    return datetime(year, month, day, hour, tzinfo=UTC)


@pytest.fixture
def repo():
    PostgreSQLMigrationManager(DSN).migrate()
    repository = PostgreSQLEvidenceRepository(DSN)

    def clean():
        with repository.connect() as con:
            with con.transaction():
                con.execute("DELETE FROM domain_edges")
                con.execute("DELETE FROM recommendation_facts")
                con.execute("DELETE FROM risk_facts")
                con.execute("DELETE FROM catalyst_impact_facts")
                con.execute("DELETE FROM valuation_facts")
                con.execute("DELETE FROM forecast_facts")
                con.execute("DELETE FROM estimate_facts")
                con.execute("DELETE FROM calculation_facts")
                con.execute("DELETE FROM metric_facts")
                con.execute("DELETE FROM evidence_facts")
                con.execute("DELETE FROM claim_facts")
                con.execute("DELETE FROM domain_nodes")

    clean()
    yield repository
    clean()


def evidence() -> Evidence:
    payload = {
        "source_id": "sec:edgar:0000123456",
        "source_version": "0000123456-26-000001",
        "content_hash": "a" * 64,
        "effective_at": utc(2026, 1, 31),
        "observed_at": utc(2026, 2, 1, 8),
        "published_at": utc(2026, 2, 1, 9),
    }
    return Evidence(
        id=canonical_id("evidence", payload),
        **payload,
        ingested_at=utc(2026, 2, 1, 10),
        source_uri="https://www.sec.gov/example",
    )


def revenue_fact() -> ExtractedFinancialFact:
    return ExtractedFinancialFact(
        concept="us-gaap:Revenues",
        value=Decimal("1000000"),
        unit_ref="USD",
        period_start=utc(2026, 1, 1),
        period_end=utc(2026, 1, 31),
        context_id="FY2026",
        dimensions=(),
        decimals="-3",
    )


def valuation() -> Valuation:
    payload = {
        "security_id": "security:nasdaq:example",
        "method": "DCF",
        "value": Decimal("200"),
        "currency": "USD",
        "as_of": utc(2026, 2, 2, 12),
        "model_version": "cp-85.3c",
        "scenario": "BASE",
    }
    return Valuation(
        id=canonical_id("valuation", payload),
        **payload,
    )


def pin_dependency_before_cutoff(repo, valuation_id: str) -> None:
    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE domain_edges
                SET created_at = %s
                WHERE source_id = %s
                  AND source_type = 'Valuation'
                  AND edge_type = 'DEPENDS_ON'
                """,
                (
                    utc(2026, 2, 2, 11),
                    valuation_id,
                ),
            )


def test_sec_revenue_projection_persists_provenance_and_resolves_as_valuation_input(
    repo,
):
    ev = evidence()

    projected = project_sec_financial_fact(
        fact=revenue_fact(),
        evidence=ev,
        subject_id="company:example",
        created_at=utc(2026, 2, 1, 11),
    )

    repo.add_evidence(ev)
    repo.add_metric_with_evidence_link(
        projected.metric,
        projected.link,
    )

    node_valuation = valuation()
    repo.add_valuation(node_valuation)
    repo.add_valuation_dependencies(
        node_valuation.id,
        (projected.metric.id,),
    )
    pin_dependency_before_cutoff(
        repo,
        node_valuation.id,
    )

    inputs = repo.valuation_inputs_at(
        node_valuation.id,
        utc(2026, 2, 2, 12),
    )

    assert inputs == (projected.metric,)
    assert isinstance(inputs[0], Metric)
    assert inputs[0].name == "financial.revenue"
    assert inputs[0].value == Decimal("1000000")
    assert inputs[0].subject_id == "company:example"

    with repo.connect() as con:
        metric_row = con.execute(
            """
            SELECT
                subject_id,
                name,
                value,
                unit,
                currency,
                source_id,
                source_version
            FROM metric_facts
            WHERE node_id = %s
            """,
            (projected.metric.id,),
        ).fetchone()

        provenance_row = con.execute(
            """
            SELECT
                source_id,
                source_type,
                edge_type,
                target_id,
                target_type
            FROM domain_edges
            WHERE source_id = %s
              AND edge_type = %s
              AND target_id = %s
            """,
            (
                projected.metric.id,
                EdgeType.SUPPORTED_BY.value,
                ev.id,
            ),
        ).fetchone()

    assert metric_row is not None
    assert metric_row[0] == "company:example"
    assert metric_row[1] == "financial.revenue"
    assert metric_row[2] == Decimal("1000000")
    assert metric_row[3] == "currency"
    assert metric_row[4] == "USD"
    assert metric_row[5] == ev.source_id
    assert metric_row[6] == ev.source_version

    assert provenance_row == (
        projected.metric.id,
        "Metric",
        EdgeType.SUPPORTED_BY.value,
        ev.id,
        "Evidence",
    )


def test_sec_revenue_projection_is_not_visible_before_publication(
    repo,
):
    ev = evidence()

    projected = project_sec_financial_fact(
        fact=revenue_fact(),
        evidence=ev,
        subject_id="company:example",
        created_at=utc(2026, 2, 1, 11),
    )

    repo.add_evidence(ev)
    repo.add_metric_with_evidence_link(
        projected.metric,
        projected.link,
    )

    before_publication = utc(2026, 2, 1, 8)

    with pytest.raises(Exception) as exc_info:
        repo.valuation_inputs_by_ids_at(
            (projected.metric.id,),
            before_publication,
        )

    assert (
        "IDM-R550: VALUATION_INPUT_NOT_VISIBLE_AT_CUTOFF"
        in str(exc_info.value)
    )
