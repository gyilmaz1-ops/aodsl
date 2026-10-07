from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from investment_domain import canonical_id
from investment_domain.edges import EdgeType
from investment_domain.ingestion import ExternalObservation
from investment_domain.nodes import Metric, Valuation
from investment_domain.postgres_migrations import PostgreSQLMigrationManager
from investment_domain.postgres_repository import PostgreSQLEvidenceRepository
from investment_domain.sec_edgar import SecFilingRequest
from investment_domain.sec_edgar_discovery import SecFilingSelector
from investment_domain.sec_financial_fact_ingestion import (
    SecFinancialFactIngestionService,
)


UTC = timezone.utc
DSN = os.getenv("IDM_TEST_POSTGRES_DSN")


pytestmark = pytest.mark.skipif(
    not DSN,
    reason="IDM_TEST_POSTGRES_DSN is not configured",
)


MINIMAL_IXBRL = """\
<html
    xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
    xmlns:xbrli="http://www.xbrl.org/2003/instance"
    xmlns:iso4217="http://www.xbrl.org/2003/iso4217"
    xmlns:us-gaap="http://fasb.org/us-gaap/2026">
  <body>
    <xbrli:context id="FY2026">
      <xbrli:entity>
        <xbrli:identifier scheme="http://www.sec.gov/CIK">
          0000320193
        </xbrli:identifier>
      </xbrli:entity>
      <xbrli:period>
        <xbrli:startDate>2026-01-01</xbrli:startDate>
        <xbrli:endDate>2026-09-26</xbrli:endDate>
      </xbrli:period>
    </xbrli:context>

    <xbrli:unit id="USD">
      <xbrli:measure>iso4217:USD</xbrli:measure>
    </xbrli:unit>

    <ix:nonFraction
        name="us-gaap:Revenues"
        contextRef="FY2026"
        unitRef="USD"
        decimals="-6">300000000000</ix:nonFraction>
  </body>
</html>
"""


def utc(year, month, day, hour=0, minute=0):
    return datetime(
        year,
        month,
        day,
        hour,
        minute,
        tzinfo=UTC,
    )


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


class Resolver:
    def __init__(self, request):
        self.request = request
        self.calls = 0

    def resolve(self, selector):
        self.calls += 1
        return self.request


class Provider:
    def __init__(self, observation):
        self.observation = observation
        self.calls = 0

    def fetch_filing(self, request):
        self.calls += 1
        return (self.observation,)


def request():
    return SecFilingRequest(
        accession_number="0000320193-26-000001",
        primary_document="aapl-20260926.htm",
        cik="0000320193",
        form="10-Q",
        filing_date="2026-09-26",
        effective_at=utc(2026, 9, 26),
        acceptance_datetime=utc(2026, 9, 26, 20, 15),
    )


def observation(req):
    return ExternalObservation(
        source_id="sec-edgar:10-q:0000320193-26-000001",
        source_version="sec-edgar-v1",
        raw_content=MINIMAL_IXBRL,
        effective_at=req.effective_at,
        observed_at=req.acceptance_datetime,
        published_at=req.acceptance_datetime,
        ingested_at=utc(2026, 9, 26, 20, 16),
        source_uri=(
            "https://www.sec.gov/Archives/edgar/data/"
            "320193/000032019326000001/aapl-20260926.htm"
        ),
    )


def selector():
    return SecFilingSelector(
        cik="0000320193",
        form="10-Q",
        effective_at=utc(2026, 9, 26),
    )


def valuation():
    payload = {
        "security_id": "security:nasdaq:aapl",
        "method": "DCF",
        "value": Decimal("250"),
        "currency": "USD",
        "as_of": utc(2026, 9, 27, 12),
        "model_version": "cp-85.4e",
        "scenario": "BASE",
    }
    return Valuation(
        id=canonical_id("valuation", payload),
        **payload,
    )


def pin_dependency_before_cutoff(repo, valuation_id):
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
                    utc(2026, 9, 27, 11),
                    valuation_id,
                ),
            )


def test_sec_filing_flows_through_postgres_into_valuation_input(repo):
    req = request()
    resolver = Resolver(req)
    provider = Provider(observation(req))

    service = SecFinancialFactIngestionService(
        resolver=resolver,
        provider=provider,
        repository=repo,
    )

    result = service.execute(
        selector(),
        subject_id="company:apple",
        created_at=utc(2026, 9, 26, 20, 17),
    )

    assert resolver.calls == 1
    assert provider.calls == 1
    assert len(result.metrics) == 1

    metric = result.metrics[0]
    evidence = result.evidence

    assert metric.name == "financial.revenue"
    assert metric.value == Decimal("300000000000")
    assert metric.currency == "USD"
    assert metric.subject_id == "company:apple"

    node_valuation = valuation()
    repo.add_valuation(node_valuation)
    repo.add_valuation_dependencies(
        node_valuation.id,
        (metric.id,),
    )
    pin_dependency_before_cutoff(
        repo,
        node_valuation.id,
    )

    inputs = repo.valuation_inputs_at(
        node_valuation.id,
        utc(2026, 9, 27, 12),
    )

    assert inputs == (metric,)
    assert isinstance(inputs[0], Metric)

    with repo.connect() as con:
        evidence_count = con.execute(
            """
            SELECT COUNT(*)
            FROM evidence_facts
            WHERE node_id = %s
            """,
            (evidence.id,),
        ).fetchone()[0]

        metric_count = con.execute(
            """
            SELECT COUNT(*)
            FROM metric_facts
            WHERE node_id = %s
            """,
            (metric.id,),
        ).fetchone()[0]

        provenance_count = con.execute(
            """
            SELECT COUNT(*)
            FROM domain_edges
            WHERE source_id = %s
              AND source_type = 'Metric'
              AND edge_type = %s
              AND target_id = %s
              AND target_type = 'Evidence'
            """,
            (
                metric.id,
                EdgeType.SUPPORTED_BY.value,
                evidence.id,
            ),
        ).fetchone()[0]

    assert evidence_count == 1
    assert metric_count == 1
    assert provenance_count == 1


def test_sec_filing_live_replay_is_idempotent(repo):
    req = request()
    resolver = Resolver(req)
    provider = Provider(observation(req))

    service = SecFinancialFactIngestionService(
        resolver=resolver,
        provider=provider,
        repository=repo,
    )

    first = service.execute(
        selector(),
        subject_id="company:apple",
        created_at=utc(2026, 9, 26, 20, 17),
    )

    second = service.execute(
        selector(),
        subject_id="company:apple",
        created_at=utc(2026, 9, 26, 20, 17),
    )

    assert resolver.calls == 2
    assert provider.calls == 2

    assert second == first
    assert second.evidence.id == first.evidence.id
    assert second.metrics == first.metrics

    metric = first.metrics[0]
    evidence = first.evidence

    with repo.connect() as con:
        evidence_count = con.execute(
            """
            SELECT COUNT(*)
            FROM evidence_facts
            WHERE node_id = %s
            """,
            (evidence.id,),
        ).fetchone()[0]

        metric_count = con.execute(
            """
            SELECT COUNT(*)
            FROM metric_facts
            WHERE node_id = %s
            """,
            (metric.id,),
        ).fetchone()[0]

        provenance_count = con.execute(
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

    assert evidence_count == 1
    assert metric_count == 1
    assert provenance_count == 1
