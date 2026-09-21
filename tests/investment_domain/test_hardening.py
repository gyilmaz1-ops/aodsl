from datetime import datetime, timezone
from decimal import Decimal

import pytest

from investment_domain import (
    Edge,
    EdgeType,
    Estimate,
    Evidence,
    Metric,
    NodeType,
    Valuation,
    canonical_id,
    validate_edge,
    validate_node,
)
from investment_domain.validation import DomainValidationError


UTC = timezone.utc


def test_estimate_float_rejected_at_canonical_boundary():
    payload = {
        "subject_id": "company:x",
        "metric_name": "revenue",
        "period_end": datetime(2027, 12, 31, tzinfo=UTC),
        "value": 10.5,
        "unit": "currency",
        "scenario": "BASE",
        "model_version": "1",
        "as_of": datetime(2026, 9, 1, tzinfo=UTC),
        "currency": "USD",
    }
    with pytest.raises(TypeError, match="float is forbidden"):
        canonical_id("estimate", payload)


def test_estimate_float_rejected_at_validation_boundary():
    canonical_payload = {
        "subject_id": "company:x",
        "metric_name": "revenue",
        "period_end": datetime(2027, 12, 31, tzinfo=UTC),
        "value": Decimal("10.5"),
        "unit": "currency",
        "scenario": "BASE",
        "model_version": "1",
        "as_of": datetime(2026, 9, 1, tzinfo=UTC),
        "currency": "USD",
    }
    node_payload = dict(canonical_payload)
    node_payload["value"] = 10.5

    node = Estimate(
        id=canonical_id("estimate", canonical_payload),
        **node_payload,
    )
    with pytest.raises(DomainValidationError, match="IDM-C001"):
        validate_node(node)


def test_valuation_float_rejected_at_canonical_boundary():
    payload = {
        "security_id": "security:nasdaq:x",
        "method": "DCF",
        "value": 100.25,
        "currency": "USD",
        "as_of": datetime(2026, 9, 1, tzinfo=UTC),
        "model_version": "1",
        "scenario": "BASE",
    }
    with pytest.raises(TypeError, match="float is forbidden"):
        canonical_id("valuation", payload)


def test_valuation_float_rejected_at_validation_boundary():
    canonical_payload = {
        "security_id": "security:nasdaq:x",
        "method": "DCF",
        "value": Decimal("100.25"),
        "currency": "USD",
        "as_of": datetime(2026, 9, 1, tzinfo=UTC),
        "model_version": "1",
        "scenario": "BASE",
    }
    node_payload = dict(canonical_payload)
    node_payload["value"] = 100.25

    node = Valuation(
        id=canonical_id("valuation", canonical_payload),
        **node_payload,
    )
    with pytest.raises(DomainValidationError, match="IDM-C001"):
        validate_node(node)


def test_estimate_decimal_accepted():
    payload = {
        "subject_id": "company:x",
        "metric_name": "revenue",
        "period_end": datetime(2027, 12, 31, tzinfo=UTC),
        "value": Decimal("10.5"),
        "unit": "currency",
        "scenario": "BASE",
        "model_version": "1",
        "as_of": datetime(2026, 9, 1, tzinfo=UTC),
        "currency": "USD",
    }
    node = Estimate(
        id=canonical_id("estimate", payload),
        **payload,
    )
    validate_node(node)


def test_edge_source_identity_type_mismatch_rejected():
    edge = Edge(
        source_id="metric:x",
        source_type=NodeType.CLAIM,
        edge_type=EdgeType.SUPPORTED_BY,
        target_id="evidence:y",
        target_type=NodeType.EVIDENCE,
    )
    with pytest.raises(DomainValidationError, match="IDM-C002"):
        validate_edge(edge)


def test_edge_target_identity_type_mismatch_rejected():
    edge = Edge(
        source_id="claim:x",
        source_type=NodeType.CLAIM,
        edge_type=EdgeType.SUPPORTED_BY,
        target_id="metric:y",
        target_type=NodeType.EVIDENCE,
    )
    with pytest.raises(DomainValidationError, match="IDM-C002"):
        validate_edge(edge)


def test_self_edge_rejected():
    edge = Edge(
        source_id="calculation:x",
        source_type=NodeType.CALCULATION,
        edge_type=EdgeType.DERIVED_FROM,
        target_id="calculation:x",
        target_type=NodeType.CALCULATION,
    )
    with pytest.raises(DomainValidationError, match="IDM-C002"):
        validate_edge(edge)


def test_naive_datetime_rejected():
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
        published_at=datetime(2026, 1, 1),
        ingested_at=datetime(2026, 1, 2),
    )
    with pytest.raises(DomainValidationError, match="IDM-C004"):
        validate_node(node)


def test_metric_invalid_period_rejected():
    payload = {
        "subject_id": "company:x",
        "name": "revenue",
        "period_start": datetime(2026, 12, 31, tzinfo=UTC),
        "period_end": datetime(2026, 1, 1, tzinfo=UTC),
        "source_id": "filing:x",
        "source_version": "1",
    }
    node = Metric(
        id=canonical_id("metric", payload),
        subject_id="company:x",
        name="revenue",
        value=Decimal("10"),
        unit="currency",
        period_start=payload["period_start"],
        period_end=payload["period_end"],
        published_at=datetime(2027, 1, 1, tzinfo=UTC),
        ingested_at=datetime(2027, 1, 2, tzinfo=UTC),
        source_id="filing:x",
        source_version="1",
        currency="USD",
    )
    with pytest.raises(DomainValidationError, match="IDM-C004"):
        validate_node(node)
