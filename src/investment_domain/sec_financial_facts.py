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

    dimension_axes = tuple(dimension for dimension, _ in fact.dimensions)
    if len(set(dimension_axes)) != len(dimension_axes):
        raise FinancialFactValidationError(
            "dimension axes must be unique"
        )

    if fact.dimensions != tuple(sorted(fact.dimensions)):
        raise FinancialFactValidationError(
            "dimensions must use canonical ordering"
        )


from datetime import timezone
import xml.etree.ElementTree as ET


_IX_NS = "http://www.xbrl.org/2013/inlineXBRL"
_XBRLI_NS = "http://www.xbrl.org/2003/instance"
_XBRLDI_NS = "http://xbrl.org/2006/xbrldi"
_XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"


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
        instant = period.find(
            f"{{{_XBRLI_NS}}}instant"
        )

        dimensions = tuple(
            sorted(
                (
                    member.get("dimension") or "",
                    (member.text or "").strip(),
                )
                for member in context.iter(
                    f"{{{_XBRLDI_NS}}}explicitMember"
                )
            )
        )

        if (
            start is not None
            and end is not None
            and start.text is not None
            and end.text is not None
        ):
            contexts[context_id] = (
                _date_utc(start.text),
                _date_utc(end.text),
                dimensions,
            )
            continue

        if instant is not None and instant.text is not None:
            contexts[context_id] = None

    facts = []
    seen_fact_values: dict[
        tuple[str, str, str],
        Decimal,
    ] = {}

    for element in root.iter(
        f"{{{_IX_NS}}}nonFraction"
    ):
        if element.get(f"{{{_XSI_NS}}}nil") == "true":
            continue

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

        numeric_content = element.text

        if numeric_content is None:
            nested_nonfractions = tuple(
                element.iter(
                    f"{{{_IX_NS}}}nonFraction"
                )
            )

            leaf_texts = tuple(
                nested.text.strip()
                for nested in nested_nonfractions[1:]
                if nested.text is not None
                and nested.text.strip()
                and not any(
                    child.tag
                    == f"{{{_IX_NS}}}nonFraction"
                    for child in nested
                )
            )

            if len(leaf_texts) != 1:
                raise FinancialFactValidationError(
                    "financial fact requires numeric content"
                )

            numeric_content = leaf_texts[0]

        if context_id not in contexts:
            raise FinancialFactValidationError(
                "financial fact references unknown context"
            )

        period = contexts[context_id]

        if period is None:
            continue

        numeric_format = element.get("format")

        if numeric_format == "ixt:fixed-zero":
            value = Decimal("0")
        else:
            numeric_text = numeric_content.strip()

            if numeric_format == "ixt:num-dot-decimal":
                numeric_text = numeric_text.replace(",", "")
            elif numeric_format == "ixt-sec:numwordsen":
                numeric_words = {
                    "one": "1",
                    "two": "2",
                }

                try:
                    numeric_text = numeric_words[
                        numeric_text.lower()
                    ]
                except KeyError as exc:
                    raise FinancialFactValidationError(
                        "financial fact has invalid numeric content"
                    ) from exc

            try:
                value = Decimal(numeric_text)
            except InvalidOperation as exc:
                raise FinancialFactValidationError(
                    "financial fact has invalid numeric content"
                ) from exc

            scale = element.get("scale")

            if scale is not None:
                try:
                    scale_value = int(scale)
                except ValueError as exc:
                    raise FinancialFactValidationError(
                        "financial fact has invalid scale"
                    ) from exc

                value *= Decimal(10) ** scale_value

        fact = ExtractedFinancialFact(
            concept=concept,
            value=value,
            unit_ref=unit_ref,
            period_start=period[0],
            period_end=period[1],
            context_id=context_id,
            dimensions=period[2],
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
