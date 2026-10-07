from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from investment_domain.sec_financial_fact_ingestion import SecFinancialFactIngestionService

from investment_domain.ingestion import ExternalObservation
from investment_domain.sec_edgar import SecFilingRequest
from investment_domain.sec_edgar_discovery import SecFilingSelector


UTC = timezone.utc


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


class Repository:
    def __init__(self):
        self.evidence = []
        self.metric_links = []

    def add_evidence(self, evidence):
        self.evidence.append(evidence)

    def add_metric_with_evidence_link(self, metric, link):
        self.metric_links.append((metric, link))


def request():
    return SecFilingRequest(
        accession_number="0000320193-26-000001",
        primary_document="aapl-20260926.htm",
        cik="0000320193",
        form="10-Q",
        filing_date="2026-09-26",
        effective_at=datetime(2026, 9, 26, tzinfo=UTC),
        acceptance_datetime=datetime(
            2026, 9, 26, 20, 15, tzinfo=UTC
        ),
    )


def observation(req):
    return ExternalObservation(
        source_id="sec-edgar:10-q:0000320193-26-000001",
        source_version="sec-edgar-v1",
        raw_content=MINIMAL_IXBRL,
        effective_at=req.effective_at,
        observed_at=req.acceptance_datetime,
        published_at=req.acceptance_datetime,
        ingested_at=datetime(
            2026, 9, 26, 20, 16, tzinfo=UTC
        ),
        source_uri=(
            "https://www.sec.gov/Archives/edgar/data/"
            "320193/000032019326000001/aapl-20260926.htm"
        ),
    )


def selector():
    return SecFilingSelector(
        cik="0000320193",
        form="10-Q",
        effective_at=datetime(2026, 9, 26, tzinfo=UTC),
    )


def test_service_fetches_once_and_persists_revenue_with_provenance():
    from investment_domain.sec_financial_fact_ingestion import (
        SecFinancialFactIngestionService,
    )

    req = request()
    resolver = Resolver(req)
    provider = Provider(observation(req))
    repository = Repository()

    service = SecFinancialFactIngestionService(
        resolver=resolver,
        provider=provider,
        repository=repository,
    )

    result = service.execute(
        selector(),
        subject_id="company:apple",
        created_at=datetime(
            2026, 9, 26, 20, 17, tzinfo=UTC
        ),
    )

    assert resolver.calls == 1
    assert provider.calls == 1

    assert len(repository.evidence) == 1
    assert len(repository.metric_links) == 1

    evidence = repository.evidence[0]
    metric, link = repository.metric_links[0]

    assert result.evidence == evidence
    assert result.metrics == (metric,)

    assert metric.subject_id == "company:apple"
    assert metric.name == "financial.revenue"
    assert metric.value == 300000000000
    assert metric.currency == "USD"

    assert link.metric_id == metric.id
    assert link.evidence_id == evidence.id


def test_malformed_ixbrl_fails_before_any_repository_write():
    from investment_domain.sec_financial_fact_ingestion import (
        SecFinancialFactIngestionService,
    )

    req = request()
    broken = observation(req)

    broken = ExternalObservation(
        source_id=broken.source_id,
        source_version=broken.source_version,
        raw_content="<html><broken>",
        effective_at=broken.effective_at,
        observed_at=broken.observed_at,
        published_at=broken.published_at,
        ingested_at=broken.ingested_at,
        source_uri=broken.source_uri,
    )

    resolver = Resolver(req)
    provider = Provider(broken)
    repository = Repository()

    service = SecFinancialFactIngestionService(
        resolver=resolver,
        provider=provider,
        repository=repository,
    )

    with pytest.raises(Exception):
        service.execute(
            selector(),
            subject_id="company:apple",
            created_at=datetime(
                2026, 9, 26, 20, 17, tzinfo=UTC
            ),
        )

    assert resolver.calls == 1
    assert provider.calls == 1
    assert repository.evidence == []
    assert repository.metric_links == []


def test_unsupported_extra_fact_does_not_block_revenue_ingestion():
    req = request()
    resolver = Resolver(req)

    raw_content = MINIMAL_IXBRL.replace(
        "</body>",
        """
    <ix:nonFraction
        name="us-gaap:OperatingIncomeLoss"
        contextRef="FY2026"
        unitRef="USD"
        decimals="-6">90000000000</ix:nonFraction>
  </body>
""",
    )

    provider = Provider(
        ExternalObservation(
            source_id="sec-edgar:10-q:0000320193-26-000001",
            source_version="sec-edgar-v1",
            raw_content=raw_content,
            effective_at=req.effective_at,
            observed_at=req.acceptance_datetime,
            published_at=req.acceptance_datetime,
            ingested_at=datetime(2026, 9, 26, 20, 16, tzinfo=timezone.utc),
            source_uri=(
                "https://www.sec.gov/Archives/edgar/data/"
                "320193/000032019326000001/aapl-20260926.htm"
            ),
        )
    )
    repository = Repository()

    service = SecFinancialFactIngestionService(
        resolver=resolver,
        provider=provider,
        repository=repository,
    )

    result = service.execute(
        selector(),
        subject_id="company:apple",
        created_at=datetime(2026, 9, 26, 20, 17, tzinfo=timezone.utc),
    )

    assert resolver.calls == 1
    assert provider.calls == 1
    assert len(result.metrics) == 1
    assert result.metrics[0].name == "financial.revenue"
    assert result.metrics[0].value == Decimal("300000000000")
    assert len(repository.evidence) == 1
    assert len(repository.metric_links) == 1


def test_ambiguous_revenues_fail_before_any_repository_write():
    from investment_domain.sec_financial_fact_selection import (
        SecFinancialFactSelectionError,
    )

    req = request()
    resolver = Resolver(req)

    raw_content = MINIMAL_IXBRL.replace(
        "</xbrli:context>",
        """</xbrli:context>
    <xbrli:context id="FY2026_ALT">
      <xbrli:entity>
        <xbrli:identifier scheme="http://www.sec.gov/CIK">
          0000320193
        </xbrli:identifier>
      </xbrli:entity>
      <xbrli:period>
        <xbrli:startDate>2026-01-01</xbrli:startDate>
        <xbrli:endDate>2026-09-26</xbrli:endDate>
      </xbrli:period>
    </xbrli:context>""",
        1,
    ).replace(
        "</body>",
        """
    <ix:nonFraction
        name="us-gaap:Revenues"
        contextRef="FY2026_ALT"
        unitRef="USD"
        decimals="-6">310000000000</ix:nonFraction>
  </body>
""",
    )

    provider = Provider(
        ExternalObservation(
            source_id="sec-edgar:10-q:0000320193-26-000001",
            source_version="sec-edgar-v1",
            raw_content=raw_content,
            effective_at=req.effective_at,
            observed_at=req.acceptance_datetime,
            published_at=req.acceptance_datetime,
            ingested_at=datetime(2026, 9, 26, 20, 16, tzinfo=timezone.utc),
            source_uri=(
                "https://www.sec.gov/Archives/edgar/data/"
                "320193/000032019326000001/aapl-20260926.htm"
            ),
        )
    )
    repository = Repository()

    service = SecFinancialFactIngestionService(
        resolver=resolver,
        provider=provider,
        repository=repository,
    )

    with pytest.raises(
        SecFinancialFactSelectionError,
        match="ambiguous revenue facts",
    ):
        service.execute(
            selector(),
            subject_id="company:apple",
            created_at=datetime(2026, 9, 26, 20, 17, tzinfo=timezone.utc),
        )

    assert resolver.calls == 1
    assert provider.calls == 1
    assert repository.evidence == []
    assert repository.metric_links == []
