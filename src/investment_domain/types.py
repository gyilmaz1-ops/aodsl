from __future__ import annotations

from enum import Enum


class NodeType(str, Enum):
    COMPANY = "Company"
    SECURITY = "Security"
    METRIC = "Metric"
    CLAIM = "Claim"
    EVIDENCE = "Evidence"
    CALCULATION = "Calculation"
    ESTIMATE = "Estimate"
    FORECAST = "Forecast"
    CATALYST = "Catalyst"
    RISK = "Risk"
    VALUATION = "Valuation"
    RECOMMENDATION = "Recommendation"
