from __future__ import annotations

from datetime import datetime

from .sec_financial_facts import ExtractedFinancialFact


_REVENUE_CONCEPT = "us-gaap:Revenues"
_REVENUE_UNIT = "USD"


class SecFinancialFactSelectionError(ValueError):
    """Raised when a canonical SEC financial fact cannot be selected."""


def select_revenue_fact(
    facts: tuple[ExtractedFinancialFact, ...],
    *,
    effective_at: datetime,
) -> ExtractedFinancialFact:
    """
    Select exactly one canonical revenue fact for the filing effective date.

    Eligible revenue facts must:
    - use the supported us-gaap:Revenues concept;
    - be denominated in USD;
    - be dimensionless;
    - end on the filing effective date.

    Unsupported facts are ignored. Missing or semantically ambiguous eligible
    revenue facts fail closed.
    """
    if (
        effective_at.tzinfo is None
        or effective_at.utcoffset() is None
    ):
        raise SecFinancialFactSelectionError(
            "effective_at must be timezone-aware"
        )

    candidates = tuple(
        fact
        for fact in facts
        if fact.concept == _REVENUE_CONCEPT
        and fact.unit_ref == _REVENUE_UNIT
        and fact.dimensions == ()
        and fact.period_end == effective_at
    )

    if not candidates:
        raise SecFinancialFactSelectionError(
            "no eligible revenue fact"
        )

    first = candidates[0]

    if any(
        candidate != first
        for candidate in candidates[1:]
    ):
        raise SecFinancialFactSelectionError(
            "ambiguous revenue facts"
        )

    return first
