from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from . import canonical_id
from .edges import EdgeType
from .metrics import MetricEvidenceLink
from .nodes import Evidence, Metric
from .sec_financial_facts import (
    ExtractedFinancialFact,
    FinancialFactValidationError,
    validate_extracted_financial_fact,
)


class SecFinancialFactProjectionError(ValueError):
    pass


@dataclass(frozen=True)
class ProjectedFinancialMetric:
    metric: Metric
    link: MetricEvidenceLink


_CONCEPT_MAP = {
    "us-gaap:Revenues": "financial.revenue",
}


def project_sec_financial_fact(
    *,
    fact: ExtractedFinancialFact,
    evidence: Evidence,
    subject_id: str,
    created_at: datetime,
) -> ProjectedFinancialMetric:
    try:
        validate_extracted_financial_fact(fact)
    except FinancialFactValidationError as exc:
        raise SecFinancialFactProjectionError(
            "invalid extracted financial fact"
        ) from exc

    metric_name = _CONCEPT_MAP.get(fact.concept)
    if metric_name is None:
        raise SecFinancialFactProjectionError(
            f"unsupported SEC financial concept: {fact.concept}"
        )

    if fact.dimensions:
        raise SecFinancialFactProjectionError(
            "dimensioned SEC financial facts are not supported"
        )

    if fact.unit_ref != "USD":
        raise SecFinancialFactProjectionError(
            "unsupported SEC financial fact unit"
        )

    if not subject_id.startswith("company:") or subject_id == "company:":
        raise SecFinancialFactProjectionError(
            "SEC financial metric subject must reference Company"
        )

    if created_at.tzinfo is None or created_at.utcoffset() is None:
        raise SecFinancialFactProjectionError(
            "created_at must be timezone-aware"
        )

    payload = {
        "subject_id": subject_id,
        "name": metric_name,
        "period_start": fact.period_start,
        "period_end": fact.period_end,
        "effective_at": fact.period_end,
        "observed_at": evidence.observed_at,
        "published_at": evidence.published_at,
        "source_id": evidence.source_id,
        "source_version": evidence.source_version,
    }

    metric = Metric(
        id=canonical_id("metric", payload),
        **payload,
        value=fact.value,
        unit="currency",
        currency="USD",
        ingested_at=evidence.ingested_at,
    )

    link = MetricEvidenceLink(
        metric_id=metric.id,
        evidence_id=evidence.id,
        relation=EdgeType.SUPPORTED_BY,
        created_at=created_at,
    )

    return ProjectedFinancialMetric(
        metric=metric,
        link=link,
    )
