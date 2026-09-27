from investment_domain.metrics import (
    METRIC_DEFINITIONS,
    MetricPeriodKind,
)
from investment_domain.types import NodeType


EXPECTED_DCF_V1_METRICS = {
    "financial.free_cash_flow": (
        NodeType.COMPANY,
        "currency",
        MetricPeriodKind.DURATION,
        True,
    ),
    "valuation.wacc": (
        NodeType.COMPANY,
        "ratio",
        MetricPeriodKind.INSTANT,
        False,
    ),
    "valuation.terminal_growth_rate": (
        NodeType.COMPANY,
        "ratio",
        MetricPeriodKind.INSTANT,
        False,
    ),
    "financial.net_debt": (
        NodeType.COMPANY,
        "currency",
        MetricPeriodKind.INSTANT,
        True,
    ),
    "market.diluted_shares_outstanding": (
        NodeType.SECURITY,
        "shares",
        MetricPeriodKind.INSTANT,
        False,
    ),
}


def test_dcf_v1_metric_vocabulary_is_registered():
    for name in EXPECTED_DCF_V1_METRICS:
        assert name in METRIC_DEFINITIONS


def test_dcf_v1_metric_semantics_are_frozen():
    for name, expected in EXPECTED_DCF_V1_METRICS.items():
        definition = METRIC_DEFINITIONS[name]

        actual = (
            definition.subject_type,
            definition.unit,
            definition.period_kind,
            definition.requires_currency,
        )

        assert actual == expected


def test_dcf_v1_registry_identity_is_canonical():
    for name in EXPECTED_DCF_V1_METRICS:
        assert METRIC_DEFINITIONS[name].name == name
