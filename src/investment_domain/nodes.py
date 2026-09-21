from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from .types import NodeType


@dataclass(frozen=True)
class Company:
    id: str
    canonical_name: str
    node_type: NodeType = field(default=NodeType.COMPANY, init=False)


@dataclass(frozen=True)
class Security:
    id: str
    company_id: str
    venue: str
    ticker: str
    currency: str
    node_type: NodeType = field(default=NodeType.SECURITY, init=False)


@dataclass(frozen=True)
class Evidence:
    id: str
    source_id: str
    source_version: str
    content_hash: str
    effective_at: datetime
    observed_at: datetime
    published_at: datetime
    ingested_at: datetime
    supersedes_id: Optional[str] = None
    source_uri: Optional[str] = None
    node_type: NodeType = field(default=NodeType.EVIDENCE, init=False)


@dataclass(frozen=True)
class Metric:
    id: str
    subject_id: str
    name: str
    value: Decimal
    unit: str
    period_start: Optional[datetime]
    period_end: datetime
    effective_at: datetime
    observed_at: datetime
    published_at: datetime
    ingested_at: datetime
    source_id: str
    source_version: str
    supersedes_id: Optional[str] = None
    currency: Optional[str] = None
    node_type: NodeType = field(default=NodeType.METRIC, init=False)


@dataclass(frozen=True)
class Claim:
    id: str
    subject_id: str
    predicate: str
    as_of: datetime
    created_by: str
    object_value: Optional[str] = None
    object_ref: Optional[str] = None
    polarity: str = "POSITIVE"
    scope: str = "COMPANY"
    node_type: NodeType = field(default=NodeType.CLAIM, init=False)


@dataclass(frozen=True)
class Calculation:
    id: str
    subject_id: str
    formula: str
    input_ids: tuple[str, ...]
    value: Decimal
    unit: str
    model_version: str
    node_type: NodeType = field(default=NodeType.CALCULATION, init=False)


@dataclass(frozen=True)
class Estimate:
    id: str
    subject_id: str
    metric_name: str
    period_end: datetime
    value: Decimal
    unit: str
    scenario: str
    model_version: str
    as_of: datetime
    currency: Optional[str] = None
    node_type: NodeType = field(default=NodeType.ESTIMATE, init=False)


@dataclass(frozen=True)
class Forecast:
    id: str
    subject_id: str
    scenario: str
    as_of: datetime
    model_version: str
    node_type: NodeType = field(default=NodeType.FORECAST, init=False)


@dataclass(frozen=True)
class Catalyst:
    id: str
    subject_id: str
    description: str
    as_of: datetime
    expected_at: Optional[datetime] = None
    node_type: NodeType = field(default=NodeType.CATALYST, init=False)


@dataclass(frozen=True)
class Risk:
    id: str
    subject_id: str
    description: str
    as_of: datetime
    node_type: NodeType = field(default=NodeType.RISK, init=False)


@dataclass(frozen=True)
class Valuation:
    id: str
    security_id: str
    method: str
    value: Decimal
    currency: str
    as_of: datetime
    model_version: str
    scenario: str
    node_type: NodeType = field(default=NodeType.VALUATION, init=False)


@dataclass(frozen=True)
class Recommendation:
    id: str
    security_id: str
    action: str
    as_of: datetime
    created_by: str
    rationale_claim_ids: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    node_type: NodeType = field(default=NodeType.RECOMMENDATION, init=False)
