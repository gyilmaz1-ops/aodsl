from __future__ import annotations

from datetime import datetime
from typing import Iterable, TypeVar

from .nodes import Evidence, Metric


SourceBackedNode = Evidence | Metric
T = TypeVar("T", Evidence, Metric)


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def available_at(
    node: SourceBackedNode,
    research_cutoff: datetime,
) -> bool:
    """Return whether information was usable by the system at cutoff.

    effective_at describes economic applicability, not information
    availability. Visibility requires both publication and ingestion.
    """
    _require_aware(research_cutoff, "research_cutoff")

    return (
        node.published_at <= research_cutoff
        and node.ingested_at <= research_cutoff
    )


def active_revision_at(
    revisions: Iterable[T],
    research_cutoff: datetime,
) -> T | None:
    """Resolve one explicit revision chain at a historical cutoff.

    Revision membership is defined by supersedes_id, not by source_id.
    The supplied nodes must form exactly one complete, non-branching
    chain. Missing predecessors, multiple roots, branches, duplicate
    identities and cross-type supersession fail closed.
    """
    _require_aware(research_cutoff, "research_cutoff")

    nodes = list(revisions)
    if not nodes:
        return None

    node_types = {type(node) for node in nodes}
    if len(node_types) != 1:
        raise ValueError(
            "revision lineage must contain exactly one node type"
        )

    ids = [node.id for node in nodes]
    if len(ids) != len(set(ids)):
        raise ValueError(
            "active_revision_at received duplicate revision identities"
        )

    by_id = {node.id: node for node in nodes}
    children: dict[str, list[T]] = {
        node.id: [] for node in nodes
    }
    roots: list[T] = []

    for node in nodes:
        supersedes_id = node.supersedes_id

        if supersedes_id is None:
            roots.append(node)
            continue

        predecessor = by_id.get(supersedes_id)
        if predecessor is None:
            raise ValueError(
                "revision supersedes_id is outside supplied lineage"
            )

        if type(predecessor) is not type(node):
            raise ValueError(
                "revision cannot supersede a different node type"
            )

        if predecessor.ingested_at >= node.ingested_at:
            raise ValueError(
                "revision must be ingested after the revision it supersedes"
            )

        children[predecessor.id].append(node)

    if len(roots) != 1:
        raise ValueError(
            "revision lineage must contain exactly one root"
        )

    if any(len(successors) > 1 for successors in children.values()):
        raise ValueError(
            "revision lineage must not branch"
        )

    # Traverse from the unique root. Requiring every supplied node to be
    # reachable rejects disconnected components as well as malformed cycles.
    ordered: list[T] = []
    visited: set[str] = set()
    current: T | None = roots[0]

    while current is not None:
        if current.id in visited:
            raise ValueError("revision supersession cycle detected")

        visited.add(current.id)
        ordered.append(current)

        successors = children[current.id]
        current = successors[0] if successors else None

    if len(visited) != len(nodes):
        raise ValueError(
            "revision lineage must form one connected chain"
        )

    candidates = [
        node
        for node in ordered
        if available_at(node, research_cutoff)
    ]

    if not candidates:
        return None

    # Ingestion time is strictly increasing along the validated chain.
    return candidates[-1]
