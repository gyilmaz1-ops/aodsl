from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Iterable

from .edges import EdgeType
from .nodes import Claim, Evidence
from .temporal import active_revision_at


class ClaimStatus(str, Enum):
    PROPOSED = "PROPOSED"
    EVIDENCED = "EVIDENCED"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


@dataclass(frozen=True)
class ClaimEvidenceLink:
    claim_id: str
    evidence_id: str
    relation: EdgeType
    created_at: datetime


_ALLOWED_TRANSITIONS: frozenset[tuple[ClaimStatus, ClaimStatus]] = frozenset({
    (ClaimStatus.PROPOSED, ClaimStatus.EVIDENCED),
    (ClaimStatus.EVIDENCED, ClaimStatus.VERIFIED),
    (ClaimStatus.EVIDENCED, ClaimStatus.REJECTED),
    (ClaimStatus.VERIFIED, ClaimStatus.SUPERSEDED),
    (ClaimStatus.REJECTED, ClaimStatus.SUPERSEDED),
})


def validate_claim_transition(
    current: ClaimStatus,
    target: ClaimStatus,
) -> None:
    if current == target:
        raise ValueError("claim lifecycle transition must change state")

    if (current, target) not in _ALLOWED_TRANSITIONS:
        raise ValueError(
            f"invalid claim lifecycle transition: "
            f"{current.value} -> {target.value}"
        )


def validate_claim_evidence_link(
    link: ClaimEvidenceLink,
) -> None:
    if not link.claim_id.startswith("claim:"):
        raise ValueError(
            "ClaimEvidenceLink.claim_id must reference Claim"
        )

    if not link.evidence_id.startswith("evidence:"):
        raise ValueError(
            "ClaimEvidenceLink.evidence_id must reference Evidence"
        )
    if link.relation not in {
        EdgeType.SUPPORTED_BY,
        EdgeType.CONTRADICTED_BY,
    }:
        raise ValueError(
            "ClaimEvidenceLink.relation must be SUPPORTED_BY "
            "or CONTRADICTED_BY"
        )

    if link.created_at.tzinfo is None or link.created_at.utcoffset() is None:
        raise ValueError(
            "ClaimEvidenceLink.created_at must be timezone-aware"
        )


def eligible_claim_evidence(
    claim: Claim,
    evidence: Iterable[Evidence],
    links: Iterable[ClaimEvidenceLink],
    research_cutoff: datetime,
) -> tuple[tuple[Evidence, EdgeType], ...]:
    """Resolve explicitly linked, active evidence at a historical cutoff.

    A superseded Evidence revision does not remain eligible once its
    successor becomes available. Claim relations are not inherited by a
    successor: the active revision must itself have an explicit link.

    Missing references, duplicate links, malformed revision chains and
    mixed-claim input fail closed.
    """
    if research_cutoff.tzinfo is None or research_cutoff.utcoffset() is None:
        raise ValueError("research_cutoff must be timezone-aware")

    evidence_nodes = list(evidence)
    evidence_by_id: dict[str, Evidence] = {}

    for node in evidence_nodes:
        if node.id in evidence_by_id:
            raise ValueError("duplicate evidence identity")
        evidence_by_id[node.id] = node

    # Build revision components from explicit supersedes_id relationships.
    # source_id is provenance metadata and MUST NOT define revision lineage.
    adjacency: dict[str, set[str]] = {
        node.id: set() for node in evidence_nodes
    }

    for node in evidence_nodes:
        if node.supersedes_id is None:
            continue

        predecessor = evidence_by_id.get(node.supersedes_id)
        if predecessor is None:
            raise ValueError(
                "revision supersedes_id is outside supplied evidence set"
            )

        adjacency[node.id].add(predecessor.id)
        adjacency[predecessor.id].add(node.id)

    active_ids: set[str] = set()
    visited: set[str] = set()

    for node in evidence_nodes:
        if node.id in visited:
            continue

        component_ids: set[str] = set()
        stack = [node.id]

        while stack:
            current_id = stack.pop()

            if current_id in component_ids:
                continue

            component_ids.add(current_id)
            visited.add(current_id)
            stack.extend(adjacency[current_id] - component_ids)

        component = [
            evidence_by_id[node_id]
            for node_id in component_ids
        ]

        active = active_revision_at(component, research_cutoff)
        if active is not None:
            active_ids.add(active.id)

    seen_links: set[tuple[str, str, EdgeType]] = set()
    result: list[tuple[Evidence, EdgeType]] = []

    for link in links:
        validate_claim_evidence_link(link)

        if link.claim_id != claim.id:
            raise ValueError(
                "ClaimEvidenceLink belongs to a different claim"
            )

        key = (
            link.claim_id,
            link.evidence_id,
            link.relation,
        )
        if key in seen_links:
            raise ValueError("duplicate claim-evidence link")
        seen_links.add(key)

        node = evidence_by_id.get(link.evidence_id)
        if node is None:
            raise ValueError(
                "ClaimEvidenceLink references missing Evidence"
            )

        if link.created_at > research_cutoff:
            continue

        if node.id not in active_ids:
            continue

        result.append((node, link.relation))

    return tuple(
        sorted(
            result,
            key=lambda item: (
                item[0].ingested_at,
                item[0].published_at,
                item[0].id,
                item[1].value,
            ),
        )
    )
