from __future__ import annotations

from collections.abc import Callable

from aodsl.outbox import MultiWorkerOutboxStore
from investment_domain.agents import AgentRequest, AgentResult
from investment_domain.agent_orchestration import (
    AgentOrchestrationValidationError,
    validate_orchestrated_result,
)
from investment_domain.specialist_outbox_binding import (
    SpecialistOutboxBindingError,
    bind_specialist_outbox_dispatch,
)


class SpecialistOutboxCoordinatorError(RuntimeError):
    """Specialist outbox dispatch failed."""


def dispatch_specialist_outbox_once(
    store: MultiWorkerOutboxStore,
    worker_id: str,
    execute: Callable[[AgentRequest], AgentResult],
    *,
    lease_seconds: float = 30.0,
) -> AgentResult | None:
    lease = store.claim_next_outbox(
        worker_id,
        lease_seconds=lease_seconds,
    )

    if lease is None:
        return None

    try:
        row = store.outbox_row(lease.outbox_id)

        if row is None:
            raise SpecialistOutboxBindingError(
                "durable outbox row missing"
            )

        request = bind_specialist_outbox_dispatch(
            dict(row),
            lease,
            lease.payload,
        )

        result = execute(request)

        validated = validate_orchestrated_result(
            request,
            result,
        )

    except Exception as exc:
        try:
            store.fail_dispatch(
                lease,
                error="SPECIALIST_DISPATCH_FAILURE",
            )
        except Exception:
            # Do not overwrite the original failure here.
            # Lease fencing will be tested separately.
            raise

        raise SpecialistOutboxCoordinatorError(
            "specialist dispatch failed"
        ) from exc

    store.persist_specialist_result_and_ack(
        lease,
        validated,
    )
    return validated
