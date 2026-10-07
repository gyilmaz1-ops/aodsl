from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation


@dataclass(frozen=True)
class ExtractedFinancialFact:
    concept: str
    value: Decimal
    unit_ref: str
    period_start: datetime | None
    period_end: datetime
    context_id: str
    dimensions: tuple[tuple[str, str], ...]
    decimals: str | None


class FinancialFactValidationError(ValueError):
    pass


def _is_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def validate_extracted_financial_fact(
    fact: ExtractedFinancialFact,
) -> None:
    for field_name in ("concept", "unit_ref", "context_id"):
        value = getattr(fact, field_name)
        if not isinstance(value, str) or not value.strip():
            raise FinancialFactValidationError(
                f"{field_name} must be a non-empty string"
            )

    if not isinstance(fact.value, Decimal):
        raise FinancialFactValidationError(
            "value must be Decimal"
        )

    if not fact.value.is_finite():
        raise FinancialFactValidationError(
            "value must be finite"
        )

    if not isinstance(fact.period_end, datetime):
        raise FinancialFactValidationError(
            "period_end must be datetime"
        )

    if not _is_aware(fact.period_end):
        raise FinancialFactValidationError(
            "period_end must be timezone-aware"
        )

    if fact.period_start is not None:
        if not isinstance(fact.period_start, datetime):
            raise FinancialFactValidationError(
                "period_start must be datetime or None"
            )

        if not _is_aware(fact.period_start):
            raise FinancialFactValidationError(
                "period_start must be timezone-aware"
            )

        if fact.period_start > fact.period_end:
            raise FinancialFactValidationError(
                "period_start cannot exceed period_end"
            )

    if not isinstance(fact.dimensions, tuple):
        raise FinancialFactValidationError(
            "dimensions must be a tuple"
        )

    for dimension in fact.dimensions:
        if (
            not isinstance(dimension, tuple)
            or len(dimension) != 2
            or not all(
                isinstance(item, str) and item.strip()
                for item in dimension
            )
        ):
            raise FinancialFactValidationError(
                "each dimension must contain two non-empty strings"
            )

    if len(set(fact.dimensions)) != len(fact.dimensions):
        raise FinancialFactValidationError(
            "dimensions must be unique"
        )

    if fact.dimensions != tuple(sorted(fact.dimensions)):
        raise FinancialFactValidationError(
            "dimensions must use canonical ordering"
        )


from datetime import timezone
import xml.etree.ElementTree as ET


_IX_NS = "http://www.xbrl.org/2013/inlineXBRL"
_XBRLI_NS = "http://www.xbrl.org/2003/instance"


def _date_utc(value: str) -> datetime:
    return datetime.strptime(
        value.strip(),
        "%Y-%m-%d",
    ).replace(tzinfo=timezone.utc)


def extract_financial_facts_from_ixbrl(
    raw_content: str,
) -> tuple[ExtractedFinancialFact, ...]:
    root = ET.fromstring(raw_content)

    units = {
        unit_id
        for unit in root.iter(
            f"{{{_XBRLI_NS}}}unit"
        )
        if (unit_id := unit.get("id"))
    }

    contexts = {}

    for context in root.iter(
        f"{{{_XBRLI_NS}}}context"
    ):
        context_id = context.get("id")
        period = context.find(
            f"{{{_XBRLI_NS}}}period"
        )

        if not context_id or period is None:
            continue

        start = period.find(
            f"{{{_XBRLI_NS}}}startDate"
        )
        end = period.find(
            f"{{{_XBRLI_NS}}}endDate"
        )

        if (
            start is None
            or end is None
            or start.text is None
            or end.text is None
        ):
            continue

        contexts[context_id] = (
            _date_utc(start.text),
            _date_utc(end.text),
        )

    facts = []
    seen_fact_values: dict[
        tuple[str, str, str],
        Decimal,
    ] = {}

    for element in root.iter(
        f"{{{_IX_NS}}}nonFraction"
    ):
        concept = element.get("name")
        context_id = element.get("contextRef")
        unit_ref = element.get("unitRef")

        if not concept:
            raise FinancialFactValidationError(
                "financial fact requires name"
            )

        if not context_id:
            raise FinancialFactValidationError(
                "financial fact requires contextRef"
            )

        if not unit_ref:
            raise FinancialFactValidationError(
                "financial fact requires unitRef"
            )

        if unit_ref not in units:
            raise FinancialFactValidationError(
                "financial fact references unknown unit"
            )

        if element.text is None:
            raise FinancialFactValidationError(
                "financial fact requires numeric content"
            )

        period = contexts.get(context_id)

        if period is None:
            raise FinancialFactValidationError(
                "financial fact references unknown context"
            )

        try:
            value = Decimal(element.text.strip())
        except InvalidOperation as exc:
            raise FinancialFactValidationError(
                "financial fact has invalid numeric content"
            ) from exc

        fact = ExtractedFinancialFact(
            concept=concept,
            value=value,
            unit_ref=unit_ref,
            period_start=period[0],
            period_end=period[1],
            context_id=context_id,
            dimensions=(),
            decimals=element.get("decimals"),
        )

        validate_extracted_financial_fact(fact)

        fact_identity = (
            fact.concept,
            fact.context_id,
            fact.unit_ref,
        )
        previous_value = seen_fact_values.get(
            fact_identity
        )

        if (
            previous_value is not None
            and previous_value != fact.value
        ):
            raise FinancialFactValidationError(
                "conflicting duplicate financial fact"
            )

        seen_fact_values[fact_identity] = fact.value
        facts.append(fact)

    return tuple(facts)
