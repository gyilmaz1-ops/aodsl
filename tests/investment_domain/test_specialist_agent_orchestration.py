import dataclasses

import pytest

from investment_domain.agents import (
    AgentCapability,
    AgentRequest,
    AgentResult,
)


MODULE = "investment_domain.agent_orchestration"


def _load_api():
    module = __import__(MODULE, fromlist=["*"])

    required = (
        "AgentOrchestrationValidationError",
        "RUNTIME_CAPABILITY_TO_AGENT_CAPABILITY",
        "build_agent_request",
        "validate_orchestrated_result",
    )

    missing = [
        name
        for name in required
        if not hasattr(module, name)
    ]

    assert not missing, (
        "missing orchestration API: "
        + ", ".join(missing)
    )

    return module


def test_orchestration_module_exists():
    _load_api()


def test_runtime_capability_mapping_is_explicit_and_exact():
    api = _load_api()

    assert api.RUNTIME_CAPABILITY_TO_AGENT_CAPABILITY == {
        "RESEARCH_SOURCE":
            AgentCapability.NEWS_RESEARCH,
        "CALCULATE_FINANCIAL_METRIC":
            AgentCapability.CALCULATION,
        "VERIFY_CLAIM":
            AgentCapability.VERIFICATION,
        "ADVERSARIAL_ANALYSIS":
            AgentCapability.BEAR_ANALYSIS,
    }


@pytest.mark.parametrize(
    ("runtime_capability", "expected"),
    [
        (
            "RESEARCH_SOURCE",
            AgentCapability.NEWS_RESEARCH,
        ),
        (
            "CALCULATE_FINANCIAL_METRIC",
            AgentCapability.CALCULATION,
        ),
        (
            "VERIFY_CLAIM",
            AgentCapability.VERIFICATION,
        ),
        (
            "ADVERSARIAL_ANALYSIS",
            AgentCapability.BEAR_ANALYSIS,
        ),
    ],
)
def test_build_agent_request_maps_runtime_capability(
    runtime_capability,
    expected,
):
    api = _load_api()

    request = api.build_agent_request(
        event_id="EV-001",
        plan_hash="PLAN-001",
        runtime_capability=runtime_capability,
        subject_id="CLM-001",
        as_of="2026-10-07T20:00:00Z",
        input_object_ids=("CLM-001",),
    )

    assert isinstance(request, AgentRequest)
    assert request.capability is expected
    assert request.subject_id == "CLM-001"
    assert request.as_of == "2026-10-07T20:00:00Z"
    assert request.input_object_ids == ("CLM-001",)


def test_agent_request_identity_is_deterministic():
    api = _load_api()

    kwargs = dict(
        event_id="EV-001",
        plan_hash="PLAN-001",
        runtime_capability="VERIFY_CLAIM",
        subject_id="CLM-001",
        as_of="2026-10-07T20:00:00Z",
        input_object_ids=("CLM-001",),
    )

    first = api.build_agent_request(**kwargs)
    second = api.build_agent_request(**kwargs)

    assert first == second
    assert first.request_id == second.request_id
    assert first.request_id.strip()


def test_agent_request_identity_changes_with_plan():
    api = _load_api()

    common = dict(
        event_id="EV-001",
        runtime_capability="VERIFY_CLAIM",
        subject_id="CLM-001",
        as_of="2026-10-07T20:00:00Z",
        input_object_ids=("CLM-001",),
    )

    first = api.build_agent_request(
        plan_hash="PLAN-001",
        **common,
    )
    second = api.build_agent_request(
        plan_hash="PLAN-002",
        **common,
    )

    assert first.request_id != second.request_id


def test_unknown_runtime_capability_fails_closed():
    api = _load_api()

    with pytest.raises(
        api.AgentOrchestrationValidationError
    ):
        api.build_agent_request(
            event_id="EV-001",
            plan_hash="PLAN-001",
            runtime_capability="UNKNOWN_CAPABILITY",
            subject_id="CLM-001",
            as_of="2026-10-07T20:00:00Z",
            input_object_ids=("CLM-001",),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("event_id", ""),
        ("plan_hash", ""),
        ("runtime_capability", ""),
        ("subject_id", ""),
        ("as_of", ""),
    ],
)
def test_blank_orchestration_identity_fails_closed(
    field,
    value,
):
    api = _load_api()

    kwargs = dict(
        event_id="EV-001",
        plan_hash="PLAN-001",
        runtime_capability="VERIFY_CLAIM",
        subject_id="CLM-001",
        as_of="2026-10-07T20:00:00Z",
        input_object_ids=("CLM-001",),
    )
    kwargs[field] = value

    with pytest.raises(
        api.AgentOrchestrationValidationError
    ):
        api.build_agent_request(**kwargs)


def test_existing_agent_contract_rejects_empty_inputs():
    api = _load_api()

    with pytest.raises(Exception):
        api.build_agent_request(
            event_id="EV-001",
            plan_hash="PLAN-001",
            runtime_capability="VERIFY_CLAIM",
            subject_id="CLM-001",
            as_of="2026-10-07T20:00:00Z",
            input_object_ids=(),
        )


def test_orchestration_does_not_mutate_agent_request():
    api = _load_api()

    request = api.build_agent_request(
        event_id="EV-001",
        plan_hash="PLAN-001",
        runtime_capability="VERIFY_CLAIM",
        subject_id="CLM-001",
        as_of="2026-10-07T20:00:00Z",
        input_object_ids=("CLM-001",),
    )

    assert dataclasses.is_dataclass(request)

    with pytest.raises(
        dataclasses.FrozenInstanceError
    ):
        request.subject_id = "OTHER"


def _request(api):
    return api.build_agent_request(
        event_id="EV-001",
        plan_hash="PLAN-001",
        runtime_capability="VERIFY_CLAIM",
        subject_id="CLM-001",
        as_of="2026-10-07T20:00:00Z",
        input_object_ids=("CLM-001",),
    )


def test_matching_result_is_accepted():
    api = _load_api()
    request = _request(api)

    result = AgentResult(
        result_id="RES-001",
        request_id=request.request_id,
        capability=request.capability,
        output_object_ids=("CLM-002",),
        provenance_object_ids=("EVD-001",),
    )

    assert (
        api.validate_orchestrated_result(
            request,
            result,
        )
        is result
    )


def test_result_request_mismatch_fails_closed():
    api = _load_api()
    request = _request(api)

    result = AgentResult(
        result_id="RES-001",
        request_id="OTHER-REQUEST",
        capability=request.capability,
        output_object_ids=("CLM-002",),
        provenance_object_ids=("EVD-001",),
    )

    with pytest.raises(Exception):
        api.validate_orchestrated_result(
            request,
            result,
        )


def test_result_capability_mismatch_fails_closed():
    api = _load_api()
    request = _request(api)

    result = AgentResult(
        result_id="RES-001",
        request_id=request.request_id,
        capability=AgentCapability.CALCULATION,
        output_object_ids=("CALC-001",),
        provenance_object_ids=("EVD-001",),
    )

    with pytest.raises(Exception):
        api.validate_orchestrated_result(
            request,
            result,
        )


def test_result_requires_provenance():
    request = AgentRequest(
        request_id="REQ-001",
        capability=AgentCapability.VERIFICATION,
        subject_id="CLM-001",
        as_of="2026-10-07T20:00:00Z",
        input_object_ids=("CLM-001",),
    )

    with pytest.raises(Exception):
        AgentResult(
            result_id="RES-001",
            request_id=request.request_id,
            capability=request.capability,
            output_object_ids=("CLM-002",),
            provenance_object_ids=(),
        )


def test_bridge_does_not_define_parallel_domain_models():
    api = _load_api()

    forbidden = (
        "Evidence",
        "Metric",
        "Claim",
        "Calculation",
        "Estimate",
        "Forecast",
        "Catalyst",
        "Risk",
        "Valuation",
        "Recommendation",
    )

    for name in forbidden:
        assert name not in vars(api)
