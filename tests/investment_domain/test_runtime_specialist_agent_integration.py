import dataclasses

import pytest

from investment_domain.agents import (
    AgentCapability,
    AgentRequest,
)


MODULE = "investment_domain.runtime_agent_integration"


def _load_api():
    module = __import__(MODULE, fromlist=["*"])

    required = (
        "RuntimeAgentIntegrationError",
        "build_agent_request_from_dispatch",
    )

    missing = [
        name
        for name in required
        if not hasattr(module, name)
    ]

    assert not missing, (
        "missing runtime-agent integration API: "
        + ", ".join(missing)
    )

    return module


def _dispatch(**overrides):
    payload = {
        "event_id": "EV-001",
        "plan_hash": "PLAN-001",
        "capability": "VERIFY_CLAIM",
        "provider": "verification_agent_v1",
        "target": "CLM-001",
        "reason": "MISSING_VERIFICATION",
        "as_of": "2026-10-07T20:00:00Z",
        "input_object_ids": ("CLM-001",),
        "idempotency_key": "IDEM-001",
    }
    payload.update(overrides)
    return payload


def test_runtime_agent_integration_module_exists():
    _load_api()


def test_dispatch_builds_existing_agent_request():
    api = _load_api()

    request = api.build_agent_request_from_dispatch(
        _dispatch()
    )

    assert isinstance(request, AgentRequest)
    assert request.capability is AgentCapability.VERIFICATION
    assert request.subject_id == "CLM-001"
    assert request.as_of == "2026-10-07T20:00:00Z"
    assert request.input_object_ids == ("CLM-001",)


def test_dispatch_request_identity_is_deterministic():
    api = _load_api()

    first = api.build_agent_request_from_dispatch(
        _dispatch()
    )
    second = api.build_agent_request_from_dispatch(
        _dispatch()
    )

    assert first == second
    assert first.request_id == second.request_id


@pytest.mark.parametrize(
    ("capability", "provider", "expected"),
    [
        (
            "RESEARCH_SOURCE",
            "news_research_agent_v1",
            AgentCapability.NEWS_RESEARCH,
        ),
        (
            "CALCULATE_FINANCIAL_METRIC",
            "calculation_agent_v1",
            AgentCapability.CALCULATION,
        ),
        (
            "VERIFY_CLAIM",
            "verification_agent_v1",
            AgentCapability.VERIFICATION,
        ),
        (
            "ADVERSARIAL_ANALYSIS",
            "devils_advocate_agent_v1",
            AgentCapability.BEAR_ANALYSIS,
        ),
    ],
)
def test_dispatch_uses_existing_runtime_capability_mapping(
    capability,
    provider,
    expected,
):
    api = _load_api()

    request = api.build_agent_request_from_dispatch(
        _dispatch(
            capability=capability,
            provider=provider,
        )
    )

    assert request.capability is expected


def test_runtime_target_becomes_agent_subject():
    api = _load_api()

    request = api.build_agent_request_from_dispatch(
        _dispatch(target="CLM-777")
    )

    assert request.subject_id == "CLM-777"


@pytest.mark.parametrize(
    "field",
    [
        "event_id",
        "plan_hash",
        "capability",
        "target",
        "as_of",
        "input_object_ids",
    ],
)
def test_missing_required_dispatch_identity_fails_closed(
    field,
):
    api = _load_api()

    dispatch = _dispatch()
    dispatch.pop(field)

    with pytest.raises(
        api.RuntimeAgentIntegrationError
    ):
        api.build_agent_request_from_dispatch(dispatch)


@pytest.mark.parametrize(
    "field",
    [
        "event_id",
        "plan_hash",
        "capability",
        "target",
        "as_of",
    ],
)
def test_blank_required_dispatch_identity_fails_closed(
    field,
):
    api = _load_api()

    with pytest.raises(
        api.RuntimeAgentIntegrationError
    ):
        api.build_agent_request_from_dispatch(
            _dispatch(**{field: ""})
        )


def test_unknown_runtime_capability_fails_closed():
    api = _load_api()

    with pytest.raises(
        api.RuntimeAgentIntegrationError
    ):
        api.build_agent_request_from_dispatch(
            _dispatch(capability="UNKNOWN_CAPABILITY")
        )


def test_empty_input_object_ids_fail_closed():
    api = _load_api()

    with pytest.raises(
        api.RuntimeAgentIntegrationError
    ):
        api.build_agent_request_from_dispatch(
            _dispatch(input_object_ids=())
        )


def test_list_input_object_ids_fail_closed():
    api = _load_api()

    with pytest.raises(
        api.RuntimeAgentIntegrationError
    ):
        api.build_agent_request_from_dispatch(
            _dispatch(input_object_ids=["CLM-001"])
        )


def test_provider_capability_mismatch_fails_closed():
    api = _load_api()

    with pytest.raises(
        api.RuntimeAgentIntegrationError
    ):
        api.build_agent_request_from_dispatch(
            _dispatch(
                capability="VERIFY_CLAIM",
                provider="calculation_agent_v1",
            )
        )


def test_missing_provider_fails_closed():
    api = _load_api()

    dispatch = _dispatch()
    dispatch.pop("provider")

    with pytest.raises(
        api.RuntimeAgentIntegrationError
    ):
        api.build_agent_request_from_dispatch(dispatch)


def test_adapter_does_not_mutate_dispatch():
    api = _load_api()

    dispatch = _dispatch()
    before = dict(dispatch)

    api.build_agent_request_from_dispatch(dispatch)

    assert dispatch == before


def test_result_is_frozen_existing_domain_contract():
    api = _load_api()

    request = api.build_agent_request_from_dispatch(
        _dispatch()
    )

    assert dataclasses.is_dataclass(request)

    with pytest.raises(
        dataclasses.FrozenInstanceError
    ):
        request.subject_id = "OTHER"


def test_adapter_does_not_define_parallel_domain_models():
    api = _load_api()

    forbidden = (
        "Evidence",
        "Metric",
        "Claim",
        "Calculation",
        "Forecast",
        "Valuation",
        "Recommendation",
    )

    assert not [
        name
        for name in forbidden
        if hasattr(api, name)
    ]
