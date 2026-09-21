from datetime import datetime, timezone
from decimal import Decimal

import pytest

from investment_domain import Company, Evidence, Metric, canonical_id, validate_node
from investment_domain.validation import DomainValidationError


UTC = timezone.utc


def test_company_canonical_identity():
    cid = canonical_id("company", {"canonical_name": "Example Corp"})
    node = Company(id=cid, canonical_name="Example Corp")
    validate_node(node)
    assert cid == "company:example-corp"


def test_metric_point_in_time_contract():
    payload = {
        "subject_id": "company:example-corp",
        "name": "revenue",
        "period_start": datetime(2026, 1, 1, tzinfo=UTC),
        "period_end": datetime(2026, 12, 31, tzinfo=UTC),
        "source_id": "filing:10-k",
        "source_version": "2027-02-10",
    }
    mid = canonical_id("metric", payload)
    metric = Metric(
        id=mid,
        subject_id=payload["subject_id"],
        name="revenue",
        value=Decimal("10000000000"),
        unit="currency",
        currency="USD",
        period_start=payload["period_start"],
        period_end=payload["period_end"],
        published_at=datetime(2027, 2, 10, 12, tzinfo=UTC),
        ingested_at=datetime(2027, 2, 10, 12, 5, tzinfo=UTC),
        source_id=payload["source_id"],
        source_version=payload["source_version"],
    )
    validate_node(metric)


def test_future_ingestion_paradox_rejected():
    payload = {
        "source_id": "filing:x",
        "source_version": "1",
        "content_hash": "a" * 64,
    }
    node = Evidence(
        id=canonical_id("evidence", payload),
        source_id="filing:x",
        source_version="1",
        content_hash="a" * 64,
        published_at=datetime(2026, 1, 2, tzinfo=UTC),
        ingested_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    with pytest.raises(DomainValidationError, match="IDM-C004"):
        validate_node(node)
