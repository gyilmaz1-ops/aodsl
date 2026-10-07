import pytest

from investment_domain.specialist_dispatch_envelope import (
    SpecialistDispatchEnvelopeError,
    validate_specialist_dispatch_envelope,
)


def valid_dispatch():
    return {
        "schema_version": "specialist-dispatch-v1",
        "event_id": "EVENT-001",
        "plan_hash": "PLAN-001",
        "capability": "VERIFY_CLAIM",
        "provider": "verification_agent_v1",
        "target": "CLAIM-001",
        "as_of": "2026-10-08",
        "input_object_ids": ("EVIDENCE-001",),
    }


def test_valid_dispatch_envelope():
    result = validate_specialist_dispatch_envelope(
        valid_dispatch()
    )
    assert result["schema_version"] == "specialist-dispatch-v1"


@pytest.mark.parametrize(
    "field",
    [
        "schema_version",
        "event_id",
        "plan_hash",
        "capability",
        "provider",
        "target",
        "as_of",
        "input_object_ids",
    ],
)
def test_missing_required_field_fails_closed(field):
    dispatch = valid_dispatch()
    del dispatch[field]

    with pytest.raises(SpecialistDispatchEnvelopeError):
        validate_specialist_dispatch_envelope(dispatch)


def test_unsupported_schema_version_fails_closed():
    dispatch = valid_dispatch()
    dispatch["schema_version"] = "unknown-v2"

    with pytest.raises(SpecialistDispatchEnvelopeError):
        validate_specialist_dispatch_envelope(dispatch)


def test_duplicate_input_objects_fail_closed():
    dispatch = valid_dispatch()
    dispatch["input_object_ids"] = (
        "EVIDENCE-001",
        "EVIDENCE-001",
    )

    with pytest.raises(SpecialistDispatchEnvelopeError):
        validate_specialist_dispatch_envelope(dispatch)


def test_provider_mismatch_fails_closed():
    dispatch = valid_dispatch()
    dispatch["provider"] = "untrusted_provider"

    with pytest.raises(SpecialistDispatchEnvelopeError):
        validate_specialist_dispatch_envelope(dispatch)


def test_missing_provenance_cannot_be_inferred():
    dispatch = valid_dispatch()
    dispatch.pop("as_of")
    dispatch.pop("input_object_ids")

    with pytest.raises(SpecialistDispatchEnvelopeError):
        validate_specialist_dispatch_envelope(dispatch)


@pytest.mark.parametrize(
    "invalid_as_of",
    [
        "not-a-date",
        "2026-99-99",
        "2026-02-30",
        "2026-1-8",
        "2026-10-08T00:00:00",
    ],
)
def test_invalid_as_of_fails_closed(invalid_as_of):
    dispatch = valid_dispatch()
    dispatch["as_of"] = invalid_as_of

    with pytest.raises(SpecialistDispatchEnvelopeError):
        validate_specialist_dispatch_envelope(dispatch)


def test_unexpected_field_fails_closed():
    dispatch = valid_dispatch()
    dispatch["untrusted_metadata"] = "injected"

    with pytest.raises(SpecialistDispatchEnvelopeError):
        validate_specialist_dispatch_envelope(dispatch)


def test_non_mapping_envelope_fails_closed():
    with pytest.raises(SpecialistDispatchEnvelopeError):
        validate_specialist_dispatch_envelope(None)


def test_input_object_ids_must_be_tuple():
    dispatch = valid_dispatch()
    dispatch["input_object_ids"] = ["EVIDENCE-001"]

    with pytest.raises(SpecialistDispatchEnvelopeError):
        validate_specialist_dispatch_envelope(dispatch)
