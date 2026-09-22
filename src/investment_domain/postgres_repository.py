from __future__ import annotations

import json

from .canonical import canonical_json, canonical_sha256
from .claims import ClaimEvidenceLink, validate_claim_evidence_link
from .edges import Edge, EdgeType
from .nodes import Claim, Evidence
from .types import NodeType
from .validation import validate_edge, validate_node


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

    def add_evidence(self, evidence: Evidence) -> None:
        validate_node(evidence)

        payload, payload_hash = self._payload(evidence)

        with self.connect() as con:
            with con.transaction():
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

    def evidence_for_claim_at(self, claim_id, research_cutoff):
        raise NotImplementedError(
            "IDM-004D point-in-time read path not implemented"
        )
