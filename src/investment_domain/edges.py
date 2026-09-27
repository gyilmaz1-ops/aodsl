from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .types import NodeType


class EdgeType(str, Enum):
    ISSUES = "ISSUES"
    SUPPORTED_BY = "SUPPORTED_BY"
    CONTRADICTED_BY = "CONTRADICTED_BY"
    DERIVED_FROM = "DERIVED_FROM"
    CONTAINS = "CONTAINS"
    AFFECTS = "AFFECTS"
    DEPENDS_ON = "DEPENDS_ON"


ALLOWED_EDGES: frozenset[tuple[NodeType, EdgeType, NodeType]] = frozenset({
    (NodeType.COMPANY, EdgeType.ISSUES, NodeType.SECURITY),

    (NodeType.METRIC, EdgeType.SUPPORTED_BY, NodeType.EVIDENCE),
    (NodeType.CLAIM, EdgeType.SUPPORTED_BY, NodeType.EVIDENCE),
    (NodeType.CLAIM, EdgeType.CONTRADICTED_BY, NodeType.EVIDENCE),

    (NodeType.CALCULATION, EdgeType.DERIVED_FROM, NodeType.METRIC),
    (NodeType.CALCULATION, EdgeType.DERIVED_FROM, NodeType.CALCULATION),

    (NodeType.ESTIMATE, EdgeType.DERIVED_FROM, NodeType.METRIC),
    (NodeType.ESTIMATE, EdgeType.DERIVED_FROM, NodeType.CALCULATION),
    (NodeType.ESTIMATE, EdgeType.DERIVED_FROM, NodeType.CLAIM),

    (NodeType.FORECAST, EdgeType.CONTAINS, NodeType.ESTIMATE),

    (NodeType.CATALYST, EdgeType.AFFECTS, NodeType.CLAIM),
    (NodeType.CATALYST, EdgeType.AFFECTS, NodeType.FORECAST),

    (NodeType.RISK, EdgeType.AFFECTS, NodeType.CLAIM),
    (NodeType.RISK, EdgeType.AFFECTS, NodeType.FORECAST),
    (NodeType.RISK, EdgeType.AFFECTS, NodeType.VALUATION),

    (NodeType.VALUATION, EdgeType.DEPENDS_ON, NodeType.FORECAST),
    (NodeType.VALUATION, EdgeType.DEPENDS_ON, NodeType.ESTIMATE),
    (NodeType.VALUATION, EdgeType.DEPENDS_ON, NodeType.METRIC),
    (NodeType.VALUATION, EdgeType.DEPENDS_ON, NodeType.CALCULATION),
    (NodeType.VALUATION, EdgeType.DEPENDS_ON, NodeType.CATALYST_IMPACT),

    (NodeType.RECOMMENDATION, EdgeType.DEPENDS_ON, NodeType.VALUATION),
    (NodeType.RECOMMENDATION, EdgeType.DEPENDS_ON, NodeType.CLAIM),
    (NodeType.RECOMMENDATION, EdgeType.DEPENDS_ON, NodeType.RISK),
    (NodeType.RECOMMENDATION, EdgeType.DEPENDS_ON, NodeType.CATALYST),
})


@dataclass(frozen=True)
class Edge:
    source_id: str
    source_type: NodeType
    edge_type: EdgeType
    target_id: str
    target_type: NodeType
