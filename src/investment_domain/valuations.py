from dataclasses import dataclass
from decimal import (
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    ROUND_HALF_EVEN,
    localcontext,
)

from .nodes import (
    Calculation,
    CatalystImpact,
    Estimate,
    Forecast,
    Metric,
    Security,
    Valuation,
)


class ValuationInputResolutionError(ValueError):
    """Fail-closed valuation execution input error."""


@dataclass(frozen=True)
class DCFV1Inputs:
    free_cash_flows: tuple[Estimate, ...]
    wacc: Estimate | Metric
    terminal_growth_rate: Estimate | Metric
    net_debt: Estimate | Metric
    diluted_shares_outstanding: Estimate | Metric


_SCALAR_ROLES = {
    "valuation.wacc": "wacc",
    "valuation.terminal_growth_rate": "terminal_growth_rate",
    "financial.net_debt": "net_debt",
    "market.diluted_shares_outstanding": "diluted_shares_outstanding",
}


def _metric_name(node: Estimate | Metric) -> str:
    if isinstance(node, Estimate):
        return node.metric_name
    return node.name


def resolve_dcf_v1_inputs(
    inputs: tuple[
        Forecast | Estimate | Metric | Calculation | CatalystImpact,
        ...,
    ],
    *,
    valuation: Valuation | None = None,
    security: Security | None = None,
) -> DCFV1Inputs:
    free_cash_flows: list[Estimate] = []
    scalar: dict[str, Estimate | Metric] = {}

    for node in inputs:
        if not isinstance(node, (Estimate, Metric)):
            continue

        name = _metric_name(node)

        if name == "financial.free_cash_flow":
            if not isinstance(node, Estimate):
                raise ValuationInputResolutionError(
                    "IDM-V001: DCF_FREE_CASH_FLOW_MUST_BE_ESTIMATE"
                )
            free_cash_flows.append(node)
            continue

        role = _SCALAR_ROLES.get(name)
        if role is None:
            continue

        if role in scalar:
            raise ValuationInputResolutionError(
                f"IDM-V002: DCF_AMBIGUOUS_ROLE:{role}"
            )

        scalar[role] = node

    if not free_cash_flows:
        raise ValuationInputResolutionError(
            "IDM-V003: DCF_FREE_CASH_FLOW_MISSING"
        )

    free_cash_flows.sort(key=lambda node: (node.period_end, node.id))

    for role in _SCALAR_ROLES.values():
        if role not in scalar:
            raise ValuationInputResolutionError(
                f"IDM-V004: DCF_REQUIRED_ROLE_MISSING:{role}"
            )

    resolved = DCFV1Inputs(
        free_cash_flows=tuple(free_cash_flows),
        wacc=scalar["wacc"],
        terminal_growth_rate=scalar["terminal_growth_rate"],
        net_debt=scalar["net_debt"],
        diluted_shares_outstanding=scalar[
            "diluted_shares_outstanding"
        ],
    )

    if valuation is None and security is None:
        return resolved

    if valuation is None or security is None:
        raise ValuationInputResolutionError(
            "IDM-V005: DCF_COHERENCE_CONTEXT_INCOMPLETE"
        )

    if security.id != valuation.security_id:
        raise ValuationInputResolutionError(
            "IDM-V006: DCF_SECURITY_MISMATCH"
        )

    company_nodes = (
        *resolved.free_cash_flows,
        resolved.wacc,
        resolved.terminal_growth_rate,
        resolved.net_debt,
    )

    for node in company_nodes:
        if node.subject_id != security.company_id:
            raise ValuationInputResolutionError(
                "IDM-V007: DCF_COMPANY_SUBJECT_MISMATCH"
            )

    if (
        resolved.diluted_shares_outstanding.subject_id
        != security.id
    ):
        raise ValuationInputResolutionError(
            "IDM-V008: DCF_SECURITY_SUBJECT_MISMATCH"
        )

    estimate_nodes = tuple(
        node
        for node in (
            *resolved.free_cash_flows,
            resolved.wacc,
            resolved.terminal_growth_rate,
            resolved.net_debt,
            resolved.diluted_shares_outstanding,
        )
        if isinstance(node, Estimate)
    )

    for node in estimate_nodes:
        if node.scenario != valuation.scenario:
            raise ValuationInputResolutionError(
                "IDM-V009: DCF_SCENARIO_MISMATCH"
            )

    fcf_model_versions = {
        node.model_version
        for node in resolved.free_cash_flows
    }
    if len(fcf_model_versions) != 1:
        raise ValuationInputResolutionError(
            "IDM-V010: DCF_FCF_MODEL_VERSION_MISMATCH"
        )

    currency_nodes = (
        *resolved.free_cash_flows,
        resolved.net_debt,
    )
    for node in currency_nodes:
        if node.currency != valuation.currency:
            raise ValuationInputResolutionError(
                "IDM-V011: DCF_CURRENCY_MISMATCH"
            )

    if resolved.diluted_shares_outstanding.value <= 0:
        raise ValuationInputResolutionError(
            "IDM-V012: DCF_SHARES_NOT_POSITIVE"
        )

    wacc = resolved.wacc.value
    terminal_growth = resolved.terminal_growth_rate.value

    if wacc <= Decimal("-1"):
        raise ValuationInputResolutionError(
            "IDM-V014: DCF_WACC_OUT_OF_DOMAIN"
        )

    if wacc <= terminal_growth:
        raise ValuationInputResolutionError(
            "IDM-V013: DCF_WACC_NOT_ABOVE_TERMINAL_GROWTH"
        )

    periods = tuple(
        node.period_end
        for node in resolved.free_cash_flows
    )

    if len(periods) != len(set(periods)):
        raise ValuationInputResolutionError(
            "IDM-V015: DCF_FCF_PERIOD_DUPLICATE"
        )

    if any(
        period <= valuation.as_of
        for period in periods
    ):
        raise ValuationInputResolutionError(
            "IDM-V016: DCF_FCF_PERIOD_NOT_FUTURE"
        )

    return resolved


class ValuationEvaluationError(ValueError):
    """Deterministic valuation execution failure."""


def evaluate_dcf_v1(
    inputs: tuple[
        Forecast | Estimate | Metric | Calculation | CatalystImpact,
        ...,
    ],
    *,
    valuation: Valuation,
    security: Security,
    verify_materialized: bool = False,
) -> Decimal:
    if valuation.method != "DCF":
        raise ValuationEvaluationError(
            "IDM-V017: DCF_METHOD_UNSUPPORTED"
        )

    if valuation.model_version != "dcf-v1":
        raise ValuationEvaluationError(
            "IDM-V018: DCF_MODEL_VERSION_UNSUPPORTED"
        )

    resolved = resolve_dcf_v1_inputs(
        inputs,
        valuation=valuation,
        security=security,
    )

    try:
        with localcontext() as ctx:
            ctx.prec = 34
            ctx.rounding = ROUND_HALF_EVEN
            ctx.traps[InvalidOperation] = True
            ctx.traps[DivisionByZero] = True
            ctx.traps[Overflow] = True

            one = Decimal("1")
            wacc = +resolved.wacc.value
            growth = +resolved.terminal_growth_rate.value
            discount_base = +(one + wacc)

            enterprise_value = Decimal("0")

            for period_index, free_cash_flow in enumerate(
                resolved.free_cash_flows,
                start=1,
            ):
                discount_factor = discount_base ** period_index
                enterprise_value += (
                    free_cash_flow.value / discount_factor
                )

            terminal_fcf = resolved.free_cash_flows[-1].value
            terminal_value = (
                terminal_fcf
                * (one + growth)
                / (wacc - growth)
            )

            terminal_discount_factor = (
                discount_base
                ** len(resolved.free_cash_flows)
            )

            enterprise_value += (
                terminal_value / terminal_discount_factor
            )

            equity_value = (
                enterprise_value
                - resolved.net_debt.value
            )

            fair_value_per_share = (
                equity_value
                / resolved.diluted_shares_outstanding.value
            )

            result = +fair_value_per_share

    except (
        ArithmeticError,
        InvalidOperation,
    ) as exc:
        raise ValuationEvaluationError(
            "IDM-V019: DCF_NUMERIC_EVALUATION_FAILED"
        ) from exc

    if verify_materialized and result != valuation.value:
        raise ValuationEvaluationError(
            "IDM-V020: DCF_MATERIALIZED_VALUE_MISMATCH"
        )

    return result
