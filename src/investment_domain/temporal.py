from __future__ import annotations

from datetime import datetime
from typing import Iterable, TypeVar

from .nodes import Evidence, Metric


SourceBackedNode = Evidence | Metric
T = TypeVar("T", Evidence, Metric)


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _lineage_key(node: SourceBackedNode) -> tuple:
    if isinstance(node, Evidence):
        return (
            "evidence",
            node.source_id,
        )

    if isinstance(node, Metric):
        return (
            "metric",
            node.subject_id,
            node.name,
            node.period_start,
            node.period_end,
            node.source_id,
        )

    raise TypeError(f"unsupported source-backed node: {type(node).__name__}")


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
    """Resolve the active point-in-time revision without future leakage.

    All supplied revisions MUST belong to one logical source lineage.
    Available revisions are ordered deterministically by ingestion time,
    publication time and canonical node identity.
    """
    _require_aware(research_cutoff, "research_cutoff")

    nodes = list(revisions)
    if not nodes:
        return None

    lineage_keys = {_lineage_key(node) for node in nodes}
    if len(lineage_keys) != 1:
        raise ValueError(
            "active_revision_at requires exactly one logical source lineage"
        )

    ids = [node.id for node in nodes]
    if len(ids) != len(set(ids)):
        raise ValueError(
            "active_revision_at received duplicate revision identities"
        )

    by_id = {node.id: node for node in nodes}

    for node in nodes:
        supersedes_id = node.supersedes_id
        if supersedes_id is None:
            continue

        predecessor = by_id.get(supersedes_id)
        if predecessor is None:
            raise ValueError(
                "revision supersedes_id is outside supplied lineage"
            )

        if predecessor.ingested_at >= node.ingested_at:
            raise ValueError(
                "revision must be ingested after the revision it supersedes"
            )

    # Detect cycles independently of temporal ordering.
    for node in nodes:
        seen: set[str] = set()
        current = node

        while current.supersedes_id is not None:
            if current.id in seen:
                raise ValueError("revision supersession cycle detected")

            seen.add(current.id)

            predecessor = by_id.get(current.supersedes_id)
            if predecessor is None:
                break

            current = predecessor

    candidates = [
        node
        for node in nodes
        if available_at(node, research_cutoff)
    ]

    if not candidates:
        return None

    return max(
        candidates,
        key=lambda node: (
            node.ingested_at,
            node.published_at,
            node.id,
        ),
    )
