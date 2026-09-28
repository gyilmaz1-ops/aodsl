from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from .identity import canonical_id
from .nodes import (
    Calculation,
    CatalystImpact,
    Estimate,
    Forecast,
    Metric,
    Security,
    Valuation,
)
from .validation import validate_node
from .valuations import evaluate_dcf_v1


class ValuationExecutionRequestError(ValueError):
    """Fail-closed valuation execution re  est error."""


@dataclass(frozen=True)
class ValuationExecutionRequest:
    security_id: str
    method: str
    currency: str
    as_of: datetime
    model_version: str
    scenario: str
    research_cutoff: datetime
    dependency_ids: tuple[str, ...]


def validate_valuation_execution_request(
    request: ValuationExecutionRequest,
) -> None:
    if not isinstance(request, ValuationExecutionRequest):
        raise TypeError(
            "request must be ValuationExecutionRequest"
        )

    if (
        not isinstance(request.security_id, str)
        or not request.security_id.startswith("security:")
    ):
        raise ValuationExecutionRequestError(
            "IDM-X001: INVALID_SECURITY_ID"
        )

    if request.method != "DCF":
        raise ValuationExecutionRequestError(
            "IDM-X002: EXECUTION_METHOD_UNSUPPORTED"
        )

    if request.model_version != "dcf-v1":
        raise ValuationExecutionRequestError(
            "IDM-X003: EXECUTION_MODEL_VERSION_UNSUPPORTED"
        )

    if request.scenario not in {"BASE", "BULL", "BEAR"}:
        raise ValuationExecutionRequestError(
            "IDM-X004: EXECUTION_SCENARIO_UNSUPPORTED"
        )

    if (
        not isinstance(request.currency, str)
        or len(request.currency) != 3
        or not request.currency.isalpha()
        or request.currency != request.currency.upper()
    ):
        raise ValuationExecutionRequestError(
            "IDM-X005: INVALID_EXECUTION_CURRENCY"
        )

    for name, value in (
        ("as_of", request.as_of),
        ("research_cutoff", request.research_cutoff),
    ):
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise ValuationExecutionRequestError(
                f"IDM-X006: {name.upper()}_MUST_BE_TIMEZONE_AWARE"
            )

    if request.as_of > request.research_cutoff:
        raise ValuationExecutionRequestError(
            "IDM-X007: AS_OF_AFTER_RESEARCH_CUTOFF"
        )

    if (
        not isinstance(request.dependency_ids, tuple)
        or not request.dependency_ids
    ):
        raise ValuationExecutionRequestError(
            "IDM-X008: DEPENDENCY_IDS_MUST_BE_NON_EMPTY_TUPLE"
        )

    if any(
        not isinstance(dependency_id, str)
        or not dependency_id.strip()
        for dependency_id in request.dependency_ids
    ):
        raise ValuationExecutionRequestError(
            "IDM-X009: INVALID_DEPENDENCY_ID"
        )

    if len(request.dependency_ids) != len(
        set(request.dependency_ids)
    ):
        raise ValuationExecutionRequestError(
            "IDM-X010: DUPLICATE_DEPENDENCY_ID"
        )


ValuationExecutionInput = (
    Forecast
    | Estimate
    | Metric
    | Calculation
    | CatalystImpact
)


def materialize_valuation(
    request: ValuationExecutionRequest,
    *,
    security: Security,
    inputs: tuple[ValuationExecutionInput, ...],
) -> Valuation:
    validate_valuation_execution_request(request)

    if security.id != request.security_id:
        raise ValuationExecutionRequestError(
            "IDM-X011: EXECUTION_SECURITY_MISMATCH"
        )

    provisional_payload = {
        "security_id": request.security_id,
        "method": request.method,
        "value": Decimal("0"),
        "currency": request.currency,
        "as_of": request.as_of,
        "model_version": request.model_version,
        "scenario": request.scenario,
    }

    provisional = Valuation(
        id=canonical_id(
            "valuation",
            provisional_payload,
        ),
        **provisional_payload,
    )

    value = evaluate_dcf_v1(
        inputs,
        valuation=provisional,
        security=security,
        verify_materialized=False,
    )

    payload = {
        "security_id": request.security_id,
        "method": request.method,
        "value": value,
        "currency": request.currency,
        "as_of": request.as_of,
        "model_version": request.model_version,
        "scenario": request.scenario,
    }

    valuation = Valuation(
        id=canonical_id(
            "valuation",
            payload,
        ),
        **payload,
    )
    validate_node(valuation)
    return valuation

class ValuationExecutionServiceError(RuntimeError):
    """Fail-closed valuation execution orchestration error."""


@dataclass(frozen=True)
class VerifiedValuationResult:
    valuation: Valuation
    dependency_ids: tuple[str, ...]


class ValuationExecutionService:
    """
    Application boundary for deterministic valuation execution.

    The caller selects execution intent and dependency identities.
    The service resolves authoritative PIT inputs, computes the value,
    persists the resulting Valuation, persists its exact provenance,
    and requires exact reproducibility before returning.
    """

    def __init__(self, repository) -> None:
        self.repository = repository

    def execute(
        self,
        request: ValuationExecutionRequest,
    ) -> VerifiedValuationResult:
        validate_valuation_execution_request(request)

        security = self.repository.security(
            request.security_id
        )
        if security is None:
            raise ValuationExecutionServiceError(
                "IDM-X012: EXECUTION_SECURITY_NOT_FOUND"
            )

        resolved_inputs = tuple(
            self.repository.valuation_inputs_by_ids_at(
                request.dependency_ids,
                request.research_cutoff,
            )
        )

        resolved_ids = tuple(
            node.id for node in resolved_inputs
        )

        if set(resolved_ids) != set(
            request.dependency_ids
        ):
            raise ValuationExecutionServiceError(
                "IDM-X013: "
                "EXECUTION_DEPENDENCY_SET_MISMATCH"
            )

        if len(resolved_ids) != len(
            request.dependency_ids
        ):
            raise ValuationExecutionServiceError(
                "IDM-X013: "
                "EXECUTION_DEPENDENCY_SET_MISMATCH"
            )

        valuation = materialize_valuation(
            request,
            security=security,
            inputs=resolved_inputs,
        )

        self.repository.add_valuation(
            valuation
        )

        self.repository.add_valuation_dependencies(
            valuation.id,
            request.dependency_ids,
        )

        verified = self.repository.verify_valuation(
            valuation.id
        )

        if verified != valuation:
            raise ValuationExecutionServiceError(
                "IDM-X014: "
                "VERIFIED_VALUATION_MISMATCH"
            )

        return VerifiedValuationResult(
            valuation=verified,
            dependency_ids=request.dependency_ids,
        )
