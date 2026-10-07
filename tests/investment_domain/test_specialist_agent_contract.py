from __future__ import annotations

import importlib
from dataclasses import FrozenInstanceError, is_dataclass

import pytest


MODULE = "investment_domain.agents"

EXPECTED_CAPABILITIES = {
    "FUNDAMENTAL_ANALYSIS",
    "INDUSTRY_ANALYSIS",
    "NEWS_RESEARCH",
    "VALUATION_ANALYSIS",
    "BEAR_ANALYSIS",
    "CALCULATION",
    "VERIFICATION",
}


def _module():
    return importlib.import_module(MODULE)


def _public_api():
    module = _module()

    required = {
        "AgentCapability",
        "AgentRequest",
        "AgentResult",
        "AgentContractValidationError",
        "SPECIALIST_AGENT_CAPABILITIES",
        "validate_agent_request",
        "validate_agent_result",
    }

    missing = sorted(
        name
        for name in required
        if not hasattr(module, name)
    )

    assert not missing, (
        "missing specialist-agent API: "
        + ", ".join(missing)
    )

    return module


def _enum_names(enum_type):
    return {member.name for member in enum_type}


def _make_request(module):
    return module.AgentRequest(
        request_id="request-001",
        capability=module.AgentCapability.FUNDAMENTAL_ANALYSIS,
        subject_id="security:NVDA",
        as_of="2026-10-07T00:00:00Z",
        input_object_ids=("claim:001", "metric:001"),
    )


def _make_result(module):
    return module.AgentResult(
        result_id="result-001",
        request_id="request-001",
        capability=module.AgentCapability.FUNDAMENTAL_ANALYSIS,
        output_object_ids=("claim:101", "forecast:101"),
        provenance_object_ids=("evidence:001",),
    )


def test_specialist_capability_registry_is_exact_and_deterministic():
    module = _public_api()

    assert _enum_names(module.AgentCapability) == EXPECTED_CAPABILITIES

    registry = module.SPECIALIST_AGENT_CAPABILITIES

    assert isinstance(registry, tuple)
    assert len(registry) == len(EXPECTED_CAPABILITIES)
    assert len(set(registry)) == len(registry)

    assert tuple(
        capability.name
        for capability in registry
    ) == tuple(sorted(EXPECTED_CAPABILITIES))

    assert set(registry) == set(module.AgentCapability)


def test_agent_request_and_result_are_immutable_value_contracts():
    module = _public_api()

    request = _make_request(module)
    result = _make_result(module)

    assert is_dataclass(request)
    assert is_dataclass(result)

    with pytest.raises(FrozenInstanceError):
        request.request_id = "mutated"

    with pytest.raises(FrozenInstanceError):
        result.result_id = "mutated"


def test_valid_agent_request_and_result_are_accepted():
    module = _public_api()

    request = _make_request(module)
    result = _make_result(module)

    assert module.validate_agent_request(request) is request

    assert (
        module.validate_agent_result(
            result,
            request=request,
        )
        is result
    )


def test_unknown_capability_fails_closed():
    module = _public_api()

    with pytest.raises(
        module.AgentContractValidationError
    ):
        module.AgentRequest(
            request_id="request-001",
            capability="UNKNOWN",
            subject_id="security:NVDA",
            as_of="2026-10-07T00:00:00Z",
            input_object_ids=("claim:001",),
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("request_id", ""),
        ("subject_id", ""),
        ("as_of", ""),
        ("input_object_ids", ()),
        (
            "input_object_ids",
            ("claim:001", "claim:001"),
        ),
    ],
)
def test_invalid_agent_request_fails_closed(field, value):
    module = _public_api()

    kwargs = {
        "request_id": "request-001",
        "capability": (
            module.AgentCapability.FUNDAMENTAL_ANALYSIS
        ),
        "subject_id": "security:NVDA",
        "as_of": "2026-10-07T00:00:00Z",
        "input_object_ids": ("claim:001",),
    }

    kwargs[field] = value

    with pytest.raises(
        module.AgentContractValidationError
    ):
        request = module.AgentRequest(**kwargs)
        module.validate_agent_request(request)


@pytest.mark.parametrize(
    "field,value",
    [
        ("result_id", ""),
        ("request_id", ""),
        ("output_object_ids", ()),
        (
            "output_object_ids",
            ("claim:101", "claim:101"),
        ),
        ("provenance_object_ids", ()),
        (
            "provenance_object_ids",
            ("evidence:001", "evidence:001"),
        ),
    ],
)
def test_invalid_agent_result_fails_closed(field, value):
    module = _public_api()

    kwargs = {
        "result_id": "result-001",
        "request_id": "request-001",
        "capability": (
            module.AgentCapability.FUNDAMENTAL_ANALYSIS
        ),
        "output_object_ids": ("claim:101",),
        "provenance_object_ids": ("evidence:001",),
    }

    kwargs[field] = value

    with pytest.raises(
        module.AgentContractValidationError
    ):
        result = module.AgentResult(**kwargs)
        module.validate_agent_result(result)


def test_result_request_identity_mismatch_fails_closed():
    module = _public_api()

    request = _make_request(module)

    result = module.AgentResult(
        result_id="result-001",
        request_id="different-request",
        capability=request.capability,
        output_object_ids=("claim:101",),
        provenance_object_ids=("evidence:001",),
    )

    with pytest.raises(
        module.AgentContractValidationError
    ):
        module.validate_agent_result(
            result,
            request=request,
        )


def test_result_capability_mismatch_fails_closed():
    module = _public_api()

    request = _make_request(module)

    result = module.AgentResult(
        result_id="result-001",
        request_id=request.request_id,
        capability=module.AgentCapability.BEAR_ANALYSIS,
        output_object_ids=("claim:101",),
        provenance_object_ids=("evidence:001",),
    )

    with pytest.raises(
        module.AgentContractValidationError
    ):
        module.validate_agent_result(
            result,
            request=request,
        )


def test_agent_contract_does_not_define_parallel_domain_models():
    module = _public_api()

    forbidden = {
        "Evidence",
        "Claim",
        "Calculation",
        "Metric",
        "Estimate",
        "Forecast",
        "Valuation",
        "Recommendation",
    }

    defined_here = {
        name
        for name, value in vars(module).items()
        if (
            isinstance(value, type)
            and value.__module__ == MODULE
        )
    }

    assert defined_here.isdisjoint(forbidden)
