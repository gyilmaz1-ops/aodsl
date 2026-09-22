from __future__ import annotations

import json

from .canonical import canonical_json, canonical_sha256
from .claims import (
    ClaimEvidenceLink,
    eligible_claim_evidence,
    validate_claim_evidence_link,
)
from .edges import Edge, EdgeType
from .nodes import Claim, Evidence
from .types import NodeType
from .validation import validate_edge, validate_node


class RepositoryReadError(RuntimeError):
    """Persistent repository state cannot be read safely."""


class RepositoryWriteError(RuntimeError):
    """Fail-closed Investment Domain persistence error."""


class PostgreSQLEvidenceRepository:
    """PostgreSQL-backed immutable Investment Domain repository."""

    def __init__(self, dsn: str) -> None:
        if not isinstance(dsn, str) or not dsn.strip():
            raise ValueError("dsn must not be empty")
        self.dsn = dsn

    @staticmethod
    def _psycopg():
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError(
                "psycopg is required for PostgreSQLEvidenceRepository"
            ) from exc
        return psycopg

    def connect(self):
        return self._psycopg().connect(self.dsn)

    @staticmethod
    def _payload(node) -> tuple[str, str]:
        payload = canonical_json(node)
        payload_hash = canonical_sha256(node)
        return payload, payload_hash

    @staticmethod
    def _assert_existing_node_matches(
        con,
        *,
        node_id: str,
        node_type: str,
        payload: str,
        payload_hash: str,
    ) -> None:
        row = con.execute(
            """
            SELECT
                node_type,
                canonical_payload,
                payload_hash
            FROM domain_nodes
            WHERE id = %s
            """,
            (node_id,),
        ).fetchone()

        if row is None:
            raise RepositoryWriteError(
                "IDM-W500: DOMAIN_NODE_WRITE_LOST"
            )

        stored_type = str(row[0])
        stored_payload = row[1]
        stored_hash = str(row[2])

        expected_payload_value = json.loads(payload)

        if (
            stored_type != node_type
            or stored_hash != payload_hash
            or stored_payload != expected_payload_value
        ):
            raise RepositoryWriteError(
                "IDM-W501: IDENTITY_CONTENT_COLLISION"
            )

    @staticmethod
    def _insert_domain_node(
        con,
        *,
        node_id: str,
        node_type: str,
        payload: str,
        payload_hash: str,
    ) -> None:
        result = con.execute(
            """
            INSERT INTO domain_nodes (
                id,
                node_type,
                canonical_payload,
                payload_hash
            )
            VALUES (%s, %s, %s::jsonb, %s)
            ON CONFLICT DO NOTHING
            """,
            (
                node_id,
                node_type,
                payload,
                payload_hash,
            ),
        )

        if result.rowcount == 1:
            return

        PostgreSQLEvidenceRepository._assert_existing_node_matches(
            con,
            node_id=node_id,
            node_type=node_type,
            payload=payload,
            payload_hash=payload_hash,
        )

    @staticmethod
    def _assert_claim_projection_matches(
        con,
        claim: Claim,
    ) -> None:
        row = con.execute(
            """
            SELECT
                node_type,
                subject_id,
                predicate,
                as_of,
                polarity,
                scope
            FROM claim_facts
            WHERE node_id = %s
            """,
            (claim.id,),
        ).fetchone()

        if row is None:
            raise RepositoryWriteError(
                "IDM-W502: CLAIM_PROJECTION_WRITE_LOST"
            )

        expected = (
            claim.node_type.value,
            claim.subject_id,
            claim.predicate,
            claim.as_of,
            claim.polarity,
            claim.scope,
        )

        if tuple(row) != expected:
            raise RepositoryWriteError(
                "IDM-W504: CLAIM_PROJECTION_MISMATCH"
            )

    @staticmethod
    def _assert_evidence_projection_matches(
        con,
        evidence: Evidence,
    ) -> None:
        row = con.execute(
            """
            SELECT
                node_type,
                source_id,
                source_version,
                content_hash,
                effective_at,
                observed_at,
                published_at,
                ingested_at,
                supersedes_id,
                source_uri
            FROM evidence_facts
            WHERE node_id = %s
            """,
            (evidence.id,),
        ).fetchone()

        if row is None:
            raise RepositoryWriteError(
                "IDM-W503: EVIDENCE_PROJECTION_WRITE_LOST"
            )

        expected = (
            evidence.node_type.value,
            evidence.source_id,
            evidence.source_version,
            evidence.content_hash,
            evidence.effective_at,
            evidence.observed_at,
            evidence.published_at,
            evidence.ingested_at,
            evidence.supersedes_id,
            evidence.source_uri,
        )

        if tuple(row) != expected:
            raise RepositoryWriteError(
                "IDM-W505: EVIDENCE_PROJECTION_MISMATCH"
            )

    @staticmethod
    def _parse_payload_datetime(value):
        from datetime import datetime

        if isinstance(value, datetime):
            return value
        if not isinstance(value, str):
            raise RepositoryReadError(
                "IDM-R504: INVALID_STORED_CLAIM_PAYLOAD"
            )
        try:
            return datetime.fromisoformat(
                value.replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise RepositoryReadError(
                "IDM-R504: INVALID_STORED_CLAIM_PAYLOAD"
            ) from exc

    def add_claim(self, claim: Claim) -> None:
        validate_node(claim)

        payload, payload_hash = self._payload(claim)

        with self.connect() as con:
            with con.transaction():
                self._insert_domain_node(
                    con,
                    node_id=claim.id,
                    node_type=claim.node_type.value,
                    payload=payload,
                    payload_hash=payload_hash,
                )

                result = con.execute(
                    """
                    INSERT INTO claim_facts (
                        node_id,
                        node_type,
                        subject_id,
                        predicate,
                        as_of,
                        polarity,
                        scope
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (node_id) DO NOTHING
                    """,
                    (
                        claim.id,
                        claim.node_type.value,
                        claim.subject_id,
                        claim.predicate,
                        claim.as_of,
                        claim.polarity,
                        claim.scope,
                    ),
                )

                if result.rowcount == 0:
                    self._assert_claim_projection_matches(
                        con,
                        claim,
                    )

    @staticmethod
    def _validate_revision_append(
        con,
        evidence: Evidence,
    ) -> None:
        predecessor = con.execute(
            """
            SELECT ingested_at
            FROM evidence_facts
            WHERE node_id = %s
            FOR UPDATE
            """,
            (evidence.supersedes_id,),
        ).fetchone()

        if predecessor is None:
            raise RepositoryWriteError(
                "IDM-W512: REVISION_PREDECESSOR_NOT_FOUND"
            )

        if evidence.ingested_at <= predecessor[0]:
            raise RepositoryWriteError(
                "IDM-W514: NON_MONOTONIC_REVISION_INGESTION"
            )

        successor = con.execute(
            """
            SELECT node_id
            FROM evidence_facts
            WHERE supersedes_id = %s
            """,
            (evidence.supersedes_id,),
        ).fetchone()

        if (
            successor is not None
            and str(successor[0]) != evidence.id
        ):
            raise RepositoryWriteError(
                "IDM-W515: REVISION_BRANCH_FORBIDDEN"
            )

    def add_evidence(self, evidence: Evidence) -> None:
        validate_node(evidence)

        payload, payload_hash = self._payload(evidence)

        with self.connect() as con:
            with con.transaction():
                if evidence.supersedes_id is not None:
                    self._validate_revision_append(
                        con,
                        evidence,
                    )

                self._insert_domain_node(
                    con,
                    node_id=evidence.id,
                    node_type=evidence.node_type.value,
                    payload=payload,
                    payload_hash=payload_hash,
                )

                result = con.execute(
                    """
                    INSERT INTO evidence_facts (
                        node_id,
                        node_type,
                        source_id,
                        source_version,
                        content_hash,
                        effective_at,
                        observed_at,
                        published_at,
                        ingested_at,
                        supersedes_id,
                        source_uri
                    )
                    VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (node_id) DO NOTHING
                    """,
                    (
                        evidence.id,
                        evidence.node_type.value,
                        evidence.source_id,
                        evidence.source_version,
                        evidence.content_hash,
                        evidence.effective_at,
                        evidence.observed_at,
                        evidence.published_at,
                        evidence.ingested_at,
                        evidence.supersedes_id,
                        evidence.source_uri,
                    ),
                )

                if result.rowcount == 0:
                    self._assert_evidence_projection_matches(
                        con,
                        evidence,
                    )

    @staticmethod
    def _assert_edge_endpoint(
        con,
        *,
        node_id: str,
        expected_type: NodeType,
        missing_code: str,
        mismatch_code: str,
    ) -> None:
        row = con.execute(
            """
            SELECT node_type
            FROM domain_nodes
            WHERE id = %s
            """,
            (node_id,),
        ).fetchone()

        if row is None:
            raise RepositoryWriteError(missing_code)

        if str(row[0]) != expected_type.value:
            raise RepositoryWriteError(mismatch_code)

    @staticmethod
    def _assert_existing_edge_matches(
        con,
        *,
        link: ClaimEvidenceLink,
    ) -> None:
        row = con.execute(
            """
            SELECT created_at
            FROM domain_edges
            WHERE source_id = %s
              AND edge_type = %s
              AND target_id = %s
            """,
            (
                link.claim_id,
                link.relation.value,
                link.evidence_id,
            ),
        ).fetchone()

        if row is None:
            raise RepositoryWriteError(
                "IDM-W511: CLAIM_EVIDENCE_EDGE_WRITE_LOST"
            )

        if row[0] != link.created_at:
            raise RepositoryWriteError(
                "IDM-W506: EDGE_CONTENT_COLLISION"
            )

    def add_claim_evidence_link(
        self,
        link: ClaimEvidenceLink,
    ) -> None:
        validate_claim_evidence_link(link)

        edge = Edge(
            source_id=link.claim_id,
            source_type=NodeType.CLAIM,
            edge_type=link.relation,
            target_id=link.evidence_id,
            target_type=NodeType.EVIDENCE,
        )
        validate_edge(edge)

        with self.connect() as con:
            with con.transaction():
                self._assert_edge_endpoint(
                    con,
                    node_id=link.claim_id,
                    expected_type=NodeType.CLAIM,
                    missing_code=(
                        "IDM-W507: EDGE_SOURCE_NOT_FOUND"
                    ),
                    mismatch_code=(
                        "IDM-W509: EDGE_SOURCE_TYPE_MISMATCH"
                    ),
                )

                self._assert_edge_endpoint(
                    con,
                    node_id=link.evidence_id,
                    expected_type=NodeType.EVIDENCE,
                    missing_code=(
                        "IDM-W508: EDGE_TARGET_NOT_FOUND"
                    ),
                    mismatch_code=(
                        "IDM-W510: EDGE_TARGET_TYPE_MISMATCH"
                    ),
                )

                result = con.execute(
                    """
                    INSERT INTO domain_edges (
                        source_id,
                        edge_type,
                        target_id,
                        created_at
                    )
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (
                        link.claim_id,
                        link.relation.value,
                        link.evidence_id,
                        link.created_at,
                    ),
                )

                if result.rowcount == 0:
                    self._assert_existing_edge_matches(
                        con,
                        link=link,
                    )

    @staticmethod
    def _assert_stored_node_integrity(
        *,
        node,
        stored_payload,
        stored_hash,
    ) -> None:
        try:
            actual_hash = canonical_sha256(stored_payload)
            reconstructed_payload = json.loads(canonical_json(node))
        except (TypeError, ValueError) as exc:
            raise RepositoryReadError(
                "IDM-R505: STORED_NODE_INTEGRITY_FAILURE"
            ) from exc

        if (
            actual_hash != str(stored_hash)
            or reconstructed_payload != stored_payload
        ):
            raise RepositoryReadError(
                "IDM-R505: STORED_NODE_INTEGRITY_FAILURE"
            )

    def evidence_for_claim_at(
        self,
        claim_id,
        research_cutoff,
    ):
        if (
            research_cutoff.tzinfo is None
            or research_cutoff.utcoffset() is None
        ):
            raise ValueError(
                "research_cutoff must be timezone-aware"
            )

        if (
            not isinstance(claim_id, str)
            or not claim_id.startswith("claim:")
        ):
            raise ValueError(
                "claim_id must reference Claim"
            )

        with self.connect() as con:
            claim_row = con.execute(
                """
                SELECT
                    n.node_type,
                    n.canonical_payload,
                    n.payload_hash
                FROM domain_nodes AS n
                WHERE n.id = %s
                """,
                (claim_id,),
            ).fetchone()

            if claim_row is None:
                raise RepositoryReadError(
                    "IDM-R501: CLAIM_NOT_FOUND"
                )

            if str(claim_row[0]) != NodeType.CLAIM.value:
                raise RepositoryReadError(
                    "IDM-R502: CLAIM_TYPE_MISMATCH"
                )

            claim_payload = claim_row[1]
            claim = Claim(
                id=claim_id,
                subject_id=claim_payload["subject_id"],
                predicate=claim_payload["predicate"],
                as_of=self._parse_payload_datetime(
                    claim_payload["as_of"]
                ),
                created_by=claim_payload["created_by"],
                object_value=claim_payload.get("object_value"),
                object_ref=claim_payload.get("object_ref"),
                polarity=claim_payload["polarity"],
                scope=claim_payload["scope"],
            )
            validate_node(claim)
            self._assert_stored_node_integrity(
                node=claim,
                stored_payload=claim_payload,
                stored_hash=claim_row[2],
            )

            link_rows = con.execute(
                """
                SELECT
                    target_id,
                    edge_type,
                    created_at
                FROM domain_edges
                WHERE source_id = %s
                  AND edge_type IN (
                      'SUPPORTED_BY',
                      'CONTRADICTED_BY'
                  )
                ORDER BY target_id, edge_type
                """,
                (claim_id,),
            ).fetchall()

            if not link_rows:
                return ()

            linked_ids = {
                str(row[0])
                for row in link_rows
            }

            evidence_rows = con.execute(
                """
                WITH RECURSIVE
                ancestors(node_id, supersedes_id) AS (
                    SELECT
                        e.node_id,
                        e.supersedes_id
                    FROM evidence_facts AS e
                    WHERE e.node_id = ANY(%s)

                    UNION

                    SELECT
                        parent.node_id,
                        parent.supersedes_id
                    FROM evidence_facts AS parent
                    JOIN ancestors AS child
                      ON parent.node_id = child.supersedes_id
                ),
                roots(node_id) AS (
                    SELECT DISTINCT
                        a.node_id
                    FROM ancestors AS a
                    WHERE a.supersedes_id IS NULL
                ),
                descendants(node_id) AS (
                    SELECT
                        r.node_id
                    FROM roots AS r

                    UNION

                    SELECT
                        child.node_id
                    FROM evidence_facts AS child
                    JOIN descendants AS parent
                      ON child.supersedes_id = parent.node_id
                )
                SELECT
                    e.node_id,
                    e.source_id,
                    e.source_version,
                    e.content_hash,
                    e.effective_at,
                    e.observed_at,
                    e.published_at,
                    e.ingested_at,
                    e.supersedes_id,
                    e.source_uri,
                    n.node_type,
                    n.canonical_payload,
                    n.payload_hash
                FROM evidence_facts AS e
                JOIN descendants AS d
                  ON d.node_id = e.node_id
                JOIN domain_nodes AS n
                  ON n.id = e.node_id
                ORDER BY e.node_id
                """,
                (list(linked_ids),),
            ).fetchall()

        evidence = tuple(
            Evidence(
                id=str(row[0]),
                source_id=str(row[1]),
                source_version=str(row[2]),
                content_hash=str(row[3]),
                effective_at=row[4],
                observed_at=row[5],
                published_at=row[6],
                ingested_at=row[7],
                supersedes_id=(
                    str(row[8])
                    if row[8] is not None
                    else None
                ),
                source_uri=(
                    str(row[9])
                    if row[9] is not None
                    else None
                ),
            )
            for row in evidence_rows
        )

        for node, row in zip(evidence, evidence_rows):
            validate_node(node)

            if str(row[10]) != NodeType.EVIDENCE.value:
                raise RepositoryReadError(
                    "IDM-R505: STORED_NODE_INTEGRITY_FAILURE"
                )

            self._assert_stored_node_integrity(
                node=node,
                stored_payload=row[11],
                stored_hash=row[12],
            )

        evidence_ids = {node.id for node in evidence}
        if not linked_ids.issubset(evidence_ids):
            raise RepositoryReadError(
                "IDM-R503: LINKED_EVIDENCE_NOT_FOUND"
            )

        links = tuple(
            ClaimEvidenceLink(
                claim_id=claim_id,
                evidence_id=str(row[0]),
                relation=EdgeType(str(row[1])),
                created_at=row[2],
            )
            for row in link_rows
        )

        for link in links:
            validate_claim_evidence_link(link)

        return eligible_claim_evidence(
            claim,
            evidence,
            links,
            research_cutoff,
        )
