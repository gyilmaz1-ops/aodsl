from __future__ import annotations

import json

from decimal import Decimal

from .canonical import canonical_json, canonical_sha256
from .calculations import validate_calculation, evaluate_calculation
from .claims import (
    ClaimEvidenceLink,
    eligible_claim_evidence,
    validate_claim_evidence_link,
)
from .edges import Edge, EdgeType
from .identity import is_canonical_content_id
from .metrics import (
    MetricEvidenceLink,
    eligible_metric_evidence,
    metric_revision_key,
    validate_metric_evidence_link,
)
from .nodes import (
    Calculation,
    Catalyst,
    CatalystImpact,
    Claim,
    Estimate,
    Evidence,
    Forecast,
    Metric,
    Recommendation,
    Risk,
    Security,
    Valuation,
)
from .temporal import active_revision_at, available_at
from .types import NodeType
from .validation import validate_edge, validate_node
from .valuations import (
    ValuationEvaluationError,
    ValuationInputResolutionError,
    evaluate_dcf_v1,
)


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

    @staticmethod
    def _assert_metric_projection_matches(
        con,
        metric: Metric,
    ) -> None:
        row = con.execute(
            """
            SELECT
                node_type,
                subject_id,
                name,
                value,
                unit,
                currency,
                period_start,
                period_end,
                effective_at,
                observed_at,
                published_at,
                ingested_at,
                source_id,
                source_version,
                supersedes_id
            FROM metric_facts
            WHERE node_id = %s
            """,
            (metric.id,),
        ).fetchone()

        if row is None:
            raise RepositoryWriteError(
                "IDM-W516: METRIC_PROJECTION_WRITE_LOST"
            )

        expected = (
            metric.node_type.value,
            metric.subject_id,
            metric.name,
            metric.value,
            metric.unit,
            metric.currency,
            metric.period_start,
            metric.period_end,
            metric.effective_at,
            metric.observed_at,
            metric.published_at,
            metric.ingested_at,
            metric.source_id,
            metric.source_version,
            metric.supersedes_id,
        )

        if tuple(row) != expected:
            raise RepositoryWriteError(
                "IDM-W517: METRIC_PROJECTION_MISMATCH"
            )

    @staticmethod
    def _assert_metric_revision_predecessor_integrity(
        *,
        metric: Metric,
        stored_payload,
        stored_hash,
        projection_row,
    ) -> None:
        try:
            actual_hash = canonical_sha256(stored_payload)
            reconstructed_payload = json.loads(
                canonical_json(metric)
            )
        except (TypeError, ValueError) as exc:
            raise RepositoryWriteError(
                "IDM-W520: "
                "METRIC_REVISION_PREDECESSOR_INTEGRITY_FAILURE"
            ) from exc

        expected_projection = (
            metric.node_type.value,
            metric.id,
            metric.subject_id,
            metric.name,
            metric.value,
            metric.unit,
            metric.currency,
            metric.period_start,
            metric.period_end,
            metric.effective_at,
            metric.observed_at,
            metric.published_at,
            metric.ingested_at,
            metric.source_id,
            metric.source_version,
            metric.supersedes_id,
        )

        if (
            actual_hash != str(stored_hash)
            or reconstructed_payload != stored_payload
            or tuple(projection_row) != expected_projection
        ):
            raise RepositoryWriteError(
                "IDM-W520: "
                "METRIC_REVISION_PREDECESSOR_INTEGRITY_FAILURE"
            )

    @staticmethod
    def _validate_metric_revision_append(
        con,
        metric: Metric,
    ) -> None:
        predecessor = con.execute(
            """
            SELECT
                d.canonical_payload,
                d.payload_hash,
                m.node_type,
                m.node_id,
                m.subject_id,
                m.name,
                m.value,
                m.unit,
                m.currency,
                m.period_start,
                m.period_end,
                m.effective_at,
                m.observed_at,
                m.published_at,
                m.ingested_at,
                m.source_id,
                m.source_version,
                m.supersedes_id
            FROM metric_facts AS m
            LEFT JOIN domain_nodes AS d
              ON d.id = m.node_id
             AND d.node_type = m.node_type
            WHERE m.node_id = %s
            FOR UPDATE OF m
            """,
            (metric.supersedes_id,),
        ).fetchone()

        if predecessor is None:
            raise RepositoryWriteError(
                "IDM-W512: REVISION_PREDECESSOR_NOT_FOUND"
            )

        if predecessor[0] is None or predecessor[1] is None:
            raise RepositoryWriteError(
                "IDM-W520: "
                "METRIC_REVISION_PREDECESSOR_INTEGRITY_FAILURE"
            )

        try:
            predecessor_metric = Metric(
                id=str(predecessor[3]),
                subject_id=str(predecessor[4]),
                name=str(predecessor[5]),
                value=predecessor[6],
                unit=str(predecessor[7]),
                currency=(
                    str(predecessor[8])
                    if predecessor[8] is not None
                    else None
                ),
                period_start=predecessor[9],
                period_end=predecessor[10],
                effective_at=predecessor[11],
                observed_at=predecessor[12],
                published_at=predecessor[13],
                ingested_at=predecessor[14],
                source_id=str(predecessor[15]),
                source_version=str(predecessor[16]),
                supersedes_id=(
                    str(predecessor[17])
                    if predecessor[17] is not None
                    else None
                ),
            )
        except (TypeError, ValueError) as exc:
            raise RepositoryWriteError(
                "IDM-W520: "
                "METRIC_REVISION_PREDECESSOR_INTEGRITY_FAILURE"
            ) from exc

        PostgreSQLEvidenceRepository._assert_metric_revision_predecessor_integrity(
            metric=predecessor_metric,
            stored_payload=predecessor[0],
            stored_hash=predecessor[1],
            projection_row=predecessor[2:],
        )

        if metric_revision_key(metric) != metric_revision_key(
            predecessor_metric
        ):
            raise RepositoryWriteError(
                "IDM-W519: METRIC_REVISION_KEY_MISMATCH"
            )

        if metric.ingested_at <= predecessor_metric.ingested_at:
            raise RepositoryWriteError(
                "IDM-W514: NON_MONOTONIC_REVISION_INGESTION"
            )

        successor = con.execute(
            """
            SELECT node_id
            FROM metric_facts
            WHERE supersedes_id = %s
            """,
            (metric.supersedes_id,),
        ).fetchone()

        if (
            successor is not None
            and str(successor[0]) != metric.id
        ):
            raise RepositoryWriteError(
                "IDM-W515: REVISION_BRANCH_FORBIDDEN"
            )

    @staticmethod
    def _assert_estimate_projection_matches(
        con,
        estimate: Estimate,
    ) -> None:
        row = con.execute(
            """
            SELECT
                node_type,
                subject_id,
                metric_name,
                period_end,
                value,
                unit,
                scenario,
                model_version,
                as_of,
                currency
            FROM estimate_facts
            WHERE node_id = %s
            """,
            (estimate.id,),
        ).fetchone()

        if row is None:
            raise RepositoryWriteError(
                "IDM-W528: ESTIMATE_PROJECTION_WRITE_LOST"
            )

        expected = (
            estimate.node_type.value,
            estimate.subject_id,
            estimate.metric_name,
            estimate.period_end,
            estimate.value,
            estimate.unit,
            estimate.scenario,
            estimate.model_version,
            estimate.as_of,
            estimate.currency,
        )

        actual = (
            str(row[0]),
            str(row[1]),
            str(row[2]),
            row[3],
            row[4],
            str(row[5]),
            str(row[6]),
            str(row[7]),
            row[8],
            str(row[9]) if row[9] is not None else None,
        )

        if actual != expected:
            raise RepositoryWriteError(
                "IDM-W529: ESTIMATE_PROJECTION_MISMATCH"
            )

    @staticmethod
    def _assert_catalyst_projection_matches(
        con,
        catalyst: Catalyst,
    ) -> None:
        row = con.execute(
            """
            SELECT
                node_type,
                subject_id,
                description,
                as_of,
                expected_at
            FROM catalyst_facts
            WHERE node_id = %s
            """,
            (catalyst.id,),
        ).fetchone()

        if row is None:
            raise RepositoryWriteError(
                "IDM-W541: CATALYST_PROJECTION_WRITE_LOST"
            )

        actual = (
            str(row[0]),
            str(row[1]),
            str(row[2]),
            row[3],
            row[4],
        )

        expected = (
            catalyst.node_type.value,
            catalyst.subject_id,
            catalyst.description,
            catalyst.as_of,
            catalyst.expected_at,
        )

        if actual != expected:
            raise RepositoryWriteError(
                "IDM-W542: CATALYST_PROJECTION_MISMATCH"
            )

    @staticmethod
    def _assert_forecast_projection_matches(
        con,
        forecast: Forecast,
    ) -> None:
        row = con.execute(
            """
            SELECT
                node_type,
                subject_id,
                scenario,
                as_of,
                model_version
            FROM forecast_facts
            WHERE node_id = %s
            """,
            (forecast.id,),
        ).fetchone()

        if row is None:
            raise RepositoryWriteError(
                "IDM-W536: FORECAST_PROJECTION_WRITE_LOST"
            )

        expected = (
            forecast.node_type.value,
            forecast.subject_id,
            forecast.scenario,
            forecast.as_of,
            forecast.model_version,
        )

        actual = (
            str(row[0]),
            str(row[1]),
            str(row[2]),
            row[3],
            str(row[4]),
        )

        if actual != expected:
            raise RepositoryWriteError(
                "IDM-W537: FORECAST_PROJECTION_MISMATCH"
            )

    @staticmethod
    def _assert_valuation_projection_matches(
        con,
        valuation: Valuation,
    ) -> None:
        row = con.execute(
            """
            SELECT
                node_type,
                security_id,
                method,
                value,
                currency,
                as_of,
                model_version,
                scenario
            FROM valuation_facts
            WHERE node_id = %s
            """,
            (valuation.id,),
        ).fetchone()

        if row is None:
            raise RepositoryWriteError(
                "IDM-W532: VALUATION_PROJECTION_WRITE_LOST"
            )

        expected = (
            valuation.node_type.value,
            valuation.security_id,
            valuation.method,
            valuation.value,
            valuation.currency,
            valuation.as_of,
            valuation.model_version,
            valuation.scenario,
        )

        actual = (
            str(row[0]),
            str(row[1]),
            str(row[2]),
            row[3],
            str(row[4]),
            row[5],
            str(row[6]),
            str(row[7]),
        )

        if actual != expected:
            raise RepositoryWriteError(
                "IDM-W533: VALUATION_PROJECTION_MISMATCH"
            )

    @staticmethod
    def _assert_risk_projection_matches(
        con,
        risk: Risk,
    ) -> None:
        row = con.execute(
            """
            SELECT
                node_type,
                subject_id,
                description,
                as_of
            FROM risk_facts
            WHERE node_id = %s
            """,
            (risk.id,),
        ).fetchone()

        if row is None:
            raise RepositoryWriteError(
                "IDM-W554: RISK_PROJECTION_WRITE_LOST"
            )

        actual = (
            str(row[0]),
            str(row[1]),
            str(row[2]),
            row[3],
        )
        expected = (
            risk.node_type.value,
            risk.subject_id,
            risk.description,
            risk.as_of,
        )

        if actual != expected:
            raise RepositoryWriteError(
                "IDM-W555: RISK_PROJECTION_MISMATCH"
            )

    @staticmethod
    def _assert_recommendation_projection_matches(
        con,
        recommendation: Recommendation,
    ) -> None:
        row = con.execute(
            """
            SELECT
                node_type,
                security_id,
                action,
                as_of,
                created_by,
                rationale_claim_ids
            FROM recommendation_facts
            WHERE node_id = %s
            """,
            (recommendation.id,),
        ).fetchone()

        if row is None:
            raise RepositoryWriteError(
                "IDM-W549: RECOMMENDATION_PROJECTION_WRITE_LOST"
            )

        actual = (
            str(row[0]),
            str(row[1]),
            str(row[2]),
            row[3],
            str(row[4]),
            tuple(row[5]),
        )
        expected = (
            recommendation.node_type.value,
            recommendation.security_id,
            recommendation.action,
            recommendation.as_of,
            recommendation.created_by,
            recommendation.rationale_claim_ids,
        )

        if actual != expected:
            raise RepositoryWriteError(
                "IDM-W550: RECOMMENDATION_PROJECTION_MISMATCH"
            )

    @staticmethod
    def _assert_calculation_projection_matches(
        con,
        calculation: Calculation,
    ) -> None:
        row = con.execute(
            """
            SELECT
                node_type,
                subject_id,
                formula,
                input_ids,
                value,
                unit,
                currency,
                model_version
            FROM calculation_facts
            WHERE node_id = %s
            """,
            (calculation.id,),
        ).fetchone()

        if row is None:
            raise RepositoryWriteError(
                "IDM-W521: CALCULATION_PROJECTION_WRITE_LOST"
            )

        expected = (
            calculation.node_type.value,
            calculation.subject_id,
            calculation.formula,
            list(calculation.input_ids),
            calculation.value,
            calculation.unit,
            calculation.currency,
            calculation.model_version,
        )

        actual = (
            str(row[0]),
            str(row[1]),
            str(row[2]),
            list(row[3]),
            row[4],
            str(row[5]),
            str(row[6]) if row[6] is not None else None,
            str(row[7]),
        )

        if actual != expected:
            raise RepositoryWriteError(
                "IDM-W522: CALCULATION_PROJECTION_MISMATCH"
            )

    def add_estimate(
        self,
        estimate: Estimate,
    ) -> None:
        validate_node(estimate)
        payload, payload_hash = self._payload(estimate)

        with self.connect() as con:
            with con.transaction():
                node_exists = (
                    con.execute(
                        """
                        SELECT 1
                        FROM domain_nodes
                        WHERE id = %s
                        """,
                        (estimate.id,),
                    ).fetchone()
                    is not None
                )

                projection_exists = (
                    con.execute(
                        """
                        SELECT 1
                        FROM estimate_facts
                        WHERE node_id = %s
                        """,
                        (estimate.id,),
                    ).fetchone()
                    is not None
                )

                if node_exists and not projection_exists:
                    raise RepositoryWriteError(
                        "IDM-W528: ESTIMATE_PROJECTION_WRITE_LOST"
                    )

                if projection_exists and not node_exists:
                    raise RepositoryWriteError(
                        "IDM-W530: ESTIMATE_ORPHAN_PROJECTION"
                    )

                if node_exists:
                    self._assert_existing_node_matches(
                        con,
                        node_id=estimate.id,
                        node_type=estimate.node_type.value,
                        payload=payload,
                        payload_hash=payload_hash,
                    )
                    self._assert_estimate_projection_matches(
                        con,
                        estimate,
                    )
                    return

                self._insert_domain_node(
                    con,
                    node_id=estimate.id,
                    node_type=estimate.node_type.value,
                    payload=payload,
                    payload_hash=payload_hash,
                )

                result = con.execute(
                    """
                    INSERT INTO estimate_facts (
                        node_id,
                        node_type,
                        subject_id,
                        metric_name,
                        period_end,
                        value,
                        unit,
                        scenario,
                        model_version,
                        as_of,
                        currency
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (node_id) DO NOTHING
                    """,
                    (
                        estimate.id,
                        estimate.node_type.value,
                        estimate.subject_id,
                        estimate.metric_name,
                        estimate.period_end,
                        estimate.value,
                        estimate.unit,
                        estimate.scenario,
                        estimate.model_version,
                        estimate.as_of,
                        estimate.currency,
                    ),
                )

                if result.rowcount != 1:
                    raise RepositoryWriteError(
                        "IDM-W528: ESTIMATE_PROJECTION_WRITE_LOST"
                    )

    @staticmethod
    def _assert_catalyst_impact_projection_matches(
        con,
        catalyst_impact: CatalystImpact,
    ) -> None:
        row = con.execute(
            """
            SELECT
                node_type,
                catalyst_id,
                target_id,
                direction,
                magnitude,
                probability,
                confidence,
                horizon,
                rationale,
                as_of,
                created_by
            FROM catalyst_impact_facts
            WHERE node_id = %s
            """,
            (catalyst_impact.id,),
        ).fetchone()

        if row is None:
            raise RepositoryWriteError(
                "IDM-W548: CATALYST_IMPACT_PROJECTION_WRITE_LOST"
            )

        expected = (
            catalyst_impact.node_type.value,
            catalyst_impact.catalyst_id,
            catalyst_impact.target_id,
            catalyst_impact.direction,
            catalyst_impact.magnitude,
            catalyst_impact.probability,
            catalyst_impact.confidence,
            catalyst_impact.horizon,
            catalyst_impact.rationale,
            catalyst_impact.as_of,
            catalyst_impact.created_by,
        )

        if tuple(row) != expected:
            raise RepositoryWriteError(
                "IDM-W545: CATALYST_IMPACT_PROJECTION_MISMATCH"
            )

    def add_catalyst(
        self,
        catalyst: Catalyst,
    ) -> None:
        validate_node(catalyst)
        payload, payload_hash = self._payload(catalyst)

        with self.connect() as con:
            with con.transaction():
                node_exists = (
                    con.execute(
                        """
                        SELECT 1
                        FROM domain_nodes
                        WHERE id = %s
                        """,
                        (catalyst.id,),
                    ).fetchone()
                    is not None
                )

                projection_exists = (
                    con.execute(
                        """
                        SELECT 1
                        FROM catalyst_facts
                        WHERE node_id = %s
                        """,
                        (catalyst.id,),
                    ).fetchone()
                    is not None
                )

                if node_exists and not projection_exists:
                    raise RepositoryWriteError(
                        "IDM-W541: CATALYST_PROJECTION_WRITE_LOST"
                    )

                if projection_exists and not node_exists:
                    raise RepositoryWriteError(
                        "IDM-W543: CATALYST_ORPHAN_PROJECTION"
                    )

                if node_exists:
                    self._assert_existing_node_matches(
                        con,
                        node_id=catalyst.id,
                        node_type=catalyst.node_type.value,
                        payload=payload,
                        payload_hash=payload_hash,
                    )
                    self._assert_catalyst_projection_matches(
                        con,
                        catalyst,
                    )
                    return

                self._insert_domain_node(
                    con,
                    node_id=catalyst.id,
                    node_type=catalyst.node_type.value,
                    payload=payload,
                    payload_hash=payload_hash,
                )

                result = con.execute(
                    """
                    INSERT INTO catalyst_facts (
                        node_id,
                        node_type,
                        subject_id,
                        description,
                        as_of,
                        expected_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (node_id) DO NOTHING
                    """,
                    (
                        catalyst.id,
                        catalyst.node_type.value,
                        catalyst.subject_id,
                        catalyst.description,
                        catalyst.as_of,
                        catalyst.expected_at,
                    ),
                )

                if result.rowcount != 1:
                    raise RepositoryWriteError(
                        "IDM-W541: CATALYST_PROJECTION_WRITE_LOST"
                    )

    def add_catalyst_impact(
        self,
        catalyst_impact: CatalystImpact,
    ) -> None:
        validate_node(catalyst_impact)
        payload, payload_hash = self._payload(catalyst_impact)

        with self.connect() as con:
            with con.transaction():
                node_exists = (
                    con.execute(
                        """
                        SELECT 1
                        FROM domain_nodes
                        WHERE id = %s
                        """,
                        (catalyst_impact.id,),
                    ).fetchone()
                    is not None
                )

                projection_exists = (
                    con.execute(
                        """
                        SELECT 1
                        FROM catalyst_impact_facts
                        WHERE node_id = %s
                        """,
                        (catalyst_impact.id,),
                    ).fetchone()
                    is not None
                )

                if node_exists and not projection_exists:
                    raise RepositoryWriteError(
                        "IDM-W548: "
                        "CATALYST_IMPACT_PROJECTION_WRITE_LOST"
                    )

                if projection_exists and not node_exists:
                    raise RepositoryWriteError(
                        "IDM-W546: "
                        "CATALYST_IMPACT_ORPHAN_PROJECTION"
                    )

                if node_exists:
                    self._assert_existing_node_matches(
                        con,
                        node_id=catalyst_impact.id,
                        node_type=catalyst_impact.node_type.value,
                        payload=payload,
                        payload_hash=payload_hash,
                    )
                    self._assert_catalyst_impact_projection_matches(
                        con,
                        catalyst_impact,
                    )
                    return

                self._insert_domain_node(
                    con,
                    node_id=catalyst_impact.id,
                    node_type=catalyst_impact.node_type.value,
                    payload=payload,
                    payload_hash=payload_hash,
                )

                result = con.execute(
                    """
                    INSERT INTO catalyst_impact_facts (
                        node_id,
                        node_type,
                        catalyst_id,
                        target_id,
                        direction,
                        magnitude,
                        probability,
                        confidence,
                        horizon,
                        rationale,
                        as_of,
                        created_by
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (node_id) DO NOTHING
                    """,
                    (
                        catalyst_impact.id,
                        catalyst_impact.node_type.value,
                        catalyst_impact.catalyst_id,
                        catalyst_impact.target_id,
                        catalyst_impact.direction,
                        catalyst_impact.magnitude,
                        catalyst_impact.probability,
                        catalyst_impact.confidence,
                        catalyst_impact.horizon,
                        catalyst_impact.rationale,
                        catalyst_impact.as_of,
                        catalyst_impact.created_by,
                    ),
                )

                if result.rowcount != 1:
                    raise RepositoryWriteError(
                        "IDM-W548: "
                        "CATALYST_IMPACT_PROJECTION_WRITE_LOST"
                    )

    def add_catalyst_affects(
        self,
        catalyst_id: str,
        target_ids: tuple[str, ...],
    ) -> None:
        if not isinstance(catalyst_id, str) or not catalyst_id.strip():
            raise ValueError("catalyst_id must not be empty")

        if not is_canonical_content_id(
            catalyst_id,
            kind="catalyst",
        ):
            raise ValueError(
                "catalyst_id must be a canonical Catalyst ID"
            )

        if not isinstance(target_ids, tuple) or not target_ids:
            raise ValueError(
                "target_ids must be a non-empty tuple"
            )

        if any(
            not isinstance(target_id, str)
            or not target_id.strip()
            for target_id in target_ids
        ):
            raise ValueError(
                "target_ids must contain non-empty strings"
            )

        if len(set(target_ids)) != len(target_ids):
            raise ValueError(
                "target_ids must not contain duplicates"
            )

        with self.connect() as con:
            with con.transaction():
                source = con.execute(
                    """
                    SELECT node_type
                    FROM domain_nodes
                    WHERE id = %s
                    """,
                    (catalyst_id,),
                ).fetchone()

                if source is None:
                    raise RepositoryWriteError(
                        "IDM-W507: EDGE_SOURCE_NOT_FOUND"
                    )

                source_type = str(source[0])
                if source_type != NodeType.CATALYST.value:
                    raise RepositoryWriteError(
                        "IDM-W509: EDGE_SOURCE_TYPE_MISMATCH"
                    )

                catalyst_projection = con.execute(
                    """
                    SELECT
                        node_type,
                        subject_id,
                        description,
                        as_of,
                        expected_at
                    FROM catalyst_facts
                    WHERE node_id = %s
                    """,
                    (catalyst_id,),
                ).fetchone()

                if catalyst_projection is None:
                    raise RepositoryWriteError(
                        "IDM-W541: CATALYST_PROJECTION_WRITE_LOST"
                    )

                try:
                    persisted_catalyst = Catalyst(
                        id=catalyst_id,
                        subject_id=str(catalyst_projection[1]),
                        description=str(catalyst_projection[2]),
                        as_of=catalyst_projection[3],
                        expected_at=catalyst_projection[4],
                    )
                    validate_node(persisted_catalyst)
                except (TypeError, ValueError) as exc:
                    raise RepositoryWriteError(
                        "IDM-W542: CATALYST_PROJECTION_MISMATCH"
                    ) from exc

                payload, payload_hash = self._payload(
                    persisted_catalyst
                )

                self._assert_existing_node_matches(
                    con,
                    node_id=persisted_catalyst.id,
                    node_type=persisted_catalyst.node_type.value,
                    payload=payload,
                    payload_hash=payload_hash,
                )

                self._assert_catalyst_projection_matches(
                    con,
                    persisted_catalyst,
                )

                targets = {}

                for target_id in target_ids:
                    target = con.execute(
                        """
                        SELECT node_type
                        FROM domain_nodes
                        WHERE id = %s
                        """,
                        (target_id,),
                    ).fetchone()

                    if target is None:
                        raise RepositoryWriteError(
                            "IDM-W508: EDGE_TARGET_NOT_FOUND"
                        )

                    target_type = str(target[0])

                    if target_type not in (
                        NodeType.CLAIM.value,
                        NodeType.FORECAST.value,
                    ):
                        raise RepositoryWriteError(
                            "IDM-W510: EDGE_TARGET_TYPE_MISMATCH"
                        )

                    edge = Edge(
                        source_id=catalyst_id,
                        source_type=NodeType.CATALYST,
                        edge_type=EdgeType.AFFECTS,
                        target_id=target_id,
                        target_type=NodeType(target_type),
                    )
                    validate_edge(edge)

                    targets[target_id] = target_type

                existing_rows = con.execute(
                    """
                    SELECT target_id, target_type
                    FROM domain_edges
                    WHERE source_id = %s
                      AND source_type = 'Catalyst'
                      AND edge_type = 'AFFECTS'
                    ORDER BY target_id
                    """,
                    (catalyst_id,),
                ).fetchall()

                existing = {
                    (str(row[0]), str(row[1]))
                    for row in existing_rows
                }

                expected = {
                    (target_id, targets[target_id])
                    for target_id in target_ids
                }

                # Legal states:
                #   empty -> complete aggregate write
                #   exact complete set -> idempotent replay
                # Any partial, extra, wrong-type or divergent set is
                # persistent-state corruption and is never repaired.
                if existing:
                    if (
                        existing != expected
                        or len(existing_rows) != len(expected)
                    ):
                        raise RepositoryWriteError(
                            "IDM-W544: "
                            "CATALYST_AFFECTS_SET_MISMATCH"
                        )
                    return

                # All endpoints and Catalyst AFFECTS semantics have
                # been validated before the first write.
                for target_id in target_ids:
                    result = con.execute(
                        """
                        INSERT INTO domain_edges (
                            source_id,
                            source_type,
                            edge_type,
                            target_id,
                            target_type,
                            created_at
                        )
                        VALUES (
                            %s,
                            'Catalyst',
                            'AFFECTS',
                            %s,
                            %s,
                            CURRENT_TIMESTAMP
                        )
                        ON CONFLICT DO NOTHING
                        """,
                        (
                            catalyst_id,
                            target_id,
                            targets[target_id],
                        ),
                    )

                    if result.rowcount != 1:
                        raise RepositoryWriteError(
                            "IDM-W544: "
                            "CATALYST_AFFECTS_SET_MISMATCH"
                        )

                persisted_rows = con.execute(
                    """
                    SELECT target_id, target_type
                    FROM domain_edges
                    WHERE source_id = %s
                      AND source_type = 'Catalyst'
                      AND edge_type = 'AFFECTS'
                    """,
                    (catalyst_id,),
                ).fetchall()

                persisted = {
                    (str(row[0]), str(row[1]))
                    for row in persisted_rows
                }

                if (
                    persisted != expected
                    or len(persisted_rows) != len(expected)
                ):
                    raise RepositoryWriteError(
                        "IDM-W544: "
                        "CATALYST_AFFECTS_SET_MISMATCH"
                    )

    def add_forecast(
        self,
        forecast: Forecast,
    ) -> None:
        validate_node(forecast)
        payload, payload_hash = self._payload(forecast)

        with self.connect() as con:
            with con.transaction():
                node_exists = (
                    con.execute(
                        """
                        SELECT 1
                        FROM domain_nodes
                        WHERE id = %s
                        """,
                        (forecast.id,),
                    ).fetchone()
                    is not None
                )

                projection_exists = (
                    con.execute(
                        """
                        SELECT 1
                        FROM forecast_facts
                        WHERE node_id = %s
                        """,
                        (forecast.id,),
                    ).fetchone()
                    is not None
                )

                if node_exists and not projection_exists:
                    raise RepositoryWriteError(
                        "IDM-W536: FORECAST_PROJECTION_WRITE_LOST"
                    )

                if projection_exists and not node_exists:
                    raise RepositoryWriteError(
                        "IDM-W538: FORECAST_ORPHAN_PROJECTION"
                    )

                if node_exists:
                    self._assert_existing_node_matches(
                        con,
                        node_id=forecast.id,
                        node_type=forecast.node_type.value,
                        payload=payload,
                        payload_hash=payload_hash,
                    )
                    self._assert_forecast_projection_matches(
                        con,
                        forecast,
                    )
                    return

                self._insert_domain_node(
                    con,
                    node_id=forecast.id,
                    node_type=forecast.node_type.value,
                    payload=payload,
                    payload_hash=payload_hash,
                )

                result = con.execute(
                    """
                    INSERT INTO forecast_facts (
                        node_id,
                        node_type,
                        subject_id,
                        scenario,
                        as_of,
                        model_version
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (node_id) DO NOTHING
                    """,
                    (
                        forecast.id,
                        forecast.node_type.value,
                        forecast.subject_id,
                        forecast.scenario,
                        forecast.as_of,
                        forecast.model_version,
                    ),
                )

                if result.rowcount != 1:
                    raise RepositoryWriteError(
                        "IDM-W536: FORECAST_PROJECTION_WRITE_LOST"
                    )

    def add_forecast_estimates(
        self,
        forecast_id: str,
        estimate_ids: tuple[str, ...],
    ) -> None:
        if not isinstance(forecast_id, str) or not forecast_id.strip():
            raise ValueError("forecast_id must not be empty")
        if not is_canonical_content_id(
            forecast_id,
            kind="forecast",
        ):
            raise ValueError(
                "forecast_id must be a canonical Forecast ID"
            )
        if not isinstance(estimate_ids, tuple) or not estimate_ids:
            raise ValueError(
                "estimate_ids must be a non-empty tuple"
            )
        if any(
            not isinstance(estimate_id, str)
            or not estimate_id.strip()
            for estimate_id in estimate_ids
        ):
            raise ValueError(
                "estimate_ids must contain non-empty strings"
            )
        if len(estimate_ids) != len(set(estimate_ids)):
            raise ValueError(
                "estimate_ids must not contain duplicates"
            )

        with self.connect() as con:
            with con.transaction():
                source = con.execute(
                    """
                    SELECT node_type
                    FROM domain_nodes
                    WHERE id = %s
                    FOR UPDATE
                    """,
                    (forecast_id,),
                ).fetchone()

                if source is None:
                    raise RepositoryWriteError(
                        "IDM-W507: EDGE_SOURCE_NOT_FOUND"
                    )

                if str(source[0]) != "Forecast":
                    raise RepositoryWriteError(
                        "IDM-W509: EDGE_SOURCE_TYPE_MISMATCH"
                    )

                forecast_projection = con.execute(
                    """
                    SELECT
                        node_type,
                        subject_id,
                        scenario,
                        as_of,
                        model_version
                    FROM forecast_facts
                    WHERE node_id = %s
                    """,
                    (forecast_id,),
                ).fetchone()

                if forecast_projection is None:
                    raise RepositoryWriteError(
                        "IDM-W536: FORECAST_PROJECTION_WRITE_LOST"
                    )

                try:
                    persisted_forecast = Forecast(
                        id=forecast_id,
                        subject_id=forecast_projection[1],
                        scenario=forecast_projection[2],
                        as_of=forecast_projection[3],
                        model_version=forecast_projection[4],
                    )
                    validate_node(persisted_forecast)
                except (TypeError, ValueError) as exc:
                    raise RepositoryWriteError(
                        "IDM-W537: FORECAST_PROJECTION_MISMATCH"
                    ) from exc

                payload, payload_hash = self._payload(
                    persisted_forecast
                )
                self._assert_existing_node_matches(
                    con,
                    node_id=persisted_forecast.id,
                    node_type=persisted_forecast.node_type.value,
                    payload=payload,
                    payload_hash=payload_hash,
                )
                self._assert_forecast_projection_matches(
                    con,
                    persisted_forecast,
                )

                targets = {}

                for estimate_id in estimate_ids:
                    target = con.execute(
                        """
                        SELECT node_type
                        FROM domain_nodes
                        WHERE id = %s
                        """,
                        (estimate_id,),
                    ).fetchone()

                    if target is None:
                        raise RepositoryWriteError(
                            "IDM-W508: EDGE_TARGET_NOT_FOUND"
                        )

                    target_type = str(target[0])
                    if target_type != "Estimate":
                        raise RepositoryWriteError(
                            "IDM-W510: EDGE_TARGET_TYPE_MISMATCH"
                        )

                    estimate_projection = con.execute(
                        """
                        SELECT
                            node_type,
                            subject_id,
                            metric_name,
                            period_end,
                            value,
                            unit,
                            scenario,
                            model_version,
                            as_of,
                            currency
                        FROM estimate_facts
                        WHERE node_id = %s
                        """,
                        (estimate_id,),
                    ).fetchone()

                    if estimate_projection is None:
                        raise RepositoryWriteError(
                            "IDM-W528: ESTIMATE_PROJECTION_WRITE_LOST"
                        )

                    try:
                        persisted_estimate = Estimate(
                            id=estimate_id,
                            subject_id=estimate_projection[1],
                            metric_name=estimate_projection[2],
                            period_end=estimate_projection[3],
                            value=estimate_projection[4],
                            unit=estimate_projection[5],
                            scenario=estimate_projection[6],
                            model_version=estimate_projection[7],
                            as_of=estimate_projection[8],
                            currency=estimate_projection[9],
                        )
                        validate_node(persisted_estimate)
                    except (TypeError, ValueError) as exc:
                        raise RepositoryWriteError(
                            "IDM-W529: ESTIMATE_PROJECTION_MISMATCH"
                        ) from exc

                    estimate_payload, estimate_payload_hash = self._payload(
                        persisted_estimate
                    )
                    self._assert_existing_node_matches(
                        con,
                        node_id=persisted_estimate.id,
                        node_type=persisted_estimate.node_type.value,
                        payload=estimate_payload,
                        payload_hash=estimate_payload_hash,
                    )
                    self._assert_estimate_projection_matches(
                        con,
                        persisted_estimate,
                    )

                    if (
                        persisted_estimate.subject_id
                        != persisted_forecast.subject_id
                        or persisted_estimate.scenario
                        != persisted_forecast.scenario
                        or persisted_estimate.model_version
                        != persisted_forecast.model_version
                        or persisted_estimate.as_of
                        != persisted_forecast.as_of
                    ):
                        raise RepositoryWriteError(
                            "IDM-W540: "
                            "FORECAST_ESTIMATE_SEMANTIC_MISMATCH"
                        )

                    targets[estimate_id] = target_type

                existing_rows = con.execute(
                    """
                    SELECT target_id, target_type
                    FROM domain_edges
                    WHERE source_id = %s
                      AND source_type = 'Forecast'
                      AND edge_type = 'CONTAINS'
                    ORDER BY target_id
                    """,
                    (forecast_id,),
                ).fetchall()

                existing = {
                    (str(row[0]), str(row[1]))
                    for row in existing_rows
                }
                expected = {
                    (estimate_id, targets[estimate_id])
                    for estimate_id in estimate_ids
                }

                # Legal states:
                #   empty -> complete aggregate write
                #   exact complete set -> idempotent replay
                # Any partial, extra, wrong-type or divergent set is
                # persistent-state corruption and is never repaired.
                if existing:
                    if (
                        existing != expected
                        or len(existing_rows) != len(expected)
                    ):
                        raise RepositoryWriteError(
                            "IDM-W539: "
                            "FORECAST_COMPOSITION_SET_MISMATCH"
                        )
                    return

                # All endpoints and Forecast/Estimate semantic dimensions
                # have been validated before the first write.
                for estimate_id in estimate_ids:
                    result = con.execute(
                        """
                        INSERT INTO domain_edges (
                            source_id,
                            source_type,
                            edge_type,
                            target_id,
                            target_type,
                            created_at
                        )
                        VALUES (
                            %s,
                            'Forecast',
                            'CONTAINS',
                            %s,
                            'Estimate',
                            CURRENT_TIMESTAMP
                        )
                        ON CONFLICT DO NOTHING
                        """,
                        (
                            forecast_id,
                            estimate_id,
                        ),
                    )

                    if result.rowcount != 1:
                        raise RepositoryWriteError(
                            "IDM-W539: "
                            "FORECAST_COMPOSITION_SET_MISMATCH"
                        )

                persisted_rows = con.execute(
                    """
                    SELECT target_id, target_type
                    FROM domain_edges
                    WHERE source_id = %s
                      AND source_type = 'Forecast'
                      AND edge_type = 'CONTAINS'
                    """,
                    (forecast_id,),
                ).fetchall()

                persisted = {
                    (str(row[0]), str(row[1]))
                    for row in persisted_rows
                }

                if (
                    persisted != expected
                    or len(persisted_rows) != len(expected)
                ):
                    raise RepositoryWriteError(
                        "IDM-W539: "
                        "FORECAST_COMPOSITION_SET_MISMATCH"
                    )

    def add_risk(
        self,
        risk: Risk,
    ) -> None:
        validate_node(risk)
        payload, payload_hash = self._payload(risk)

        with self.connect() as con:
            with con.transaction():
                node_exists, projection_exists = con.execute(
                    """
                    SELECT
                        EXISTS (
                            SELECT 1
                            FROM domain_nodes
                            WHERE id = %s
                        ),
                        EXISTS (
                            SELECT 1
                            FROM risk_facts
                            WHERE node_id = %s
                        )
                    """,
                    (
                        risk.id,
                        risk.id,
                    ),
                ).fetchone()

                if node_exists and not projection_exists:
                    raise RepositoryWriteError(
                        "IDM-W554: RISK_PROJECTION_WRITE_LOST"
                    )

                if projection_exists and not node_exists:
                    raise RepositoryWriteError(
                        "IDM-W556: RISK_ORPHAN_PROJECTION"
                    )

                if node_exists:
                    self._assert_existing_node_matches(
                        con,
                        node_id=risk.id,
                        node_type=risk.node_type.value,
                        payload=payload,
                        payload_hash=payload_hash,
                    )
                    self._assert_risk_projection_matches(
                        con,
                        risk,
                    )
                    return

                self._insert_domain_node(
                    con,
                    node_id=risk.id,
                    node_type=risk.node_type.value,
                    payload=payload,
                    payload_hash=payload_hash,
                )

                result = con.execute(
                    """
                    INSERT INTO risk_facts (
                        node_id,
                        node_type,
                        subject_id,
                        description,
                        as_of
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (node_id) DO NOTHING
                    """,
                    (
                        risk.id,
                        risk.node_type.value,
                        risk.subject_id,
                        risk.description,
                        risk.as_of,
                    ),
                )

                if result.rowcount != 1:
                    self._assert_risk_projection_matches(
                        con,
                        risk,
                    )

    def risk_at(
        self,
        risk_id: str,
        research_cutoff: datetime,
    ) -> Risk | None:
        if not is_canonical_content_id(
            risk_id,
            kind="risk",
        ):
            raise ValueError(
                "risk_id must be a canonical Risk ID"
            )

        if (
            research_cutoff.tzinfo is None
            or research_cutoff.utcoffset() is None
        ):
            raise ValueError(
                "research_cutoff must be timezone-aware"
            )

        with self.connect() as con:
            anchor_row = con.execute(
                """
                SELECT
                    node_type,
                    canonical_payload,
                    payload_hash
                FROM domain_nodes
                WHERE id = %s
                """,
                (risk_id,),
            ).fetchone()

            if anchor_row is None:
                return None

            if str(anchor_row[0]) != NodeType.RISK.value:
                raise RepositoryReadError(
                    "IDM-R558: RISK_TYPE_MISMATCH"
                )

            row = con.execute(
                """
                SELECT
                    node_type,
                    subject_id,
                    description,
                    as_of
                FROM risk_facts
                WHERE node_id = %s
                """,
                (risk_id,),
            ).fetchone()

            if row is None:
                raise RepositoryReadError(
                    "IDM-R561: RISK_PROJECTION_NOT_FOUND"
                )

            if str(row[0]) != NodeType.RISK.value:
                raise RepositoryReadError(
                    "IDM-R558: RISK_TYPE_MISMATCH"
                )

            try:
                risk = Risk(
                    id=risk_id,
                    subject_id=str(row[1]),
                    description=str(row[2]),
                    as_of=row[3],
                )
                validate_node(risk)
            except (TypeError, ValueError) as exc:
                raise RepositoryReadError(
                    "IDM-R559: INVALID_STORED_RISK"
                ) from exc

            payload, payload_hash = self._payload(risk)

            if (
                anchor_row[1] != json.loads(payload)
                or str(anchor_row[2]) != payload_hash
            ):
                raise RepositoryReadError(
                    "IDM-R560: RISK_INTEGRITY_FAILURE"
                )

            if risk.as_of > research_cutoff:
                return None

            return risk

    def latest_risk_at(
        self,
        subject_id: str,
        research_cutoff: datetime,
    ) -> Risk | None:
        if not isinstance(subject_id, str) or not subject_id.strip():
            raise ValueError(
                "subject_id must not be empty"
            )

        if (
            research_cutoff.tzinfo is None
            or research_cutoff.utcoffset() is None
        ):
            raise ValueError(
                "research_cutoff must be timezone-aware"
            )

        with self.connect() as con:
            rows = con.execute(
                """
                SELECT node_id
                FROM risk_facts
                WHERE subject_id = %s
                  AND as_of = (
                      SELECT MAX(as_of)
                      FROM risk_facts
                      WHERE subject_id = %s
                        AND as_of <= %s
                  )
                ORDER BY node_id
                """,
                (
                    subject_id,
                    subject_id,
                    research_cutoff,
                ),
            ).fetchall()

        if not rows:
            return None

        if len(rows) != 1:
            raise RepositoryReadError(
                "IDM-R562: RISK_PIT_AMBIGUITY"
            )

        return self.risk_at(
            str(rows[0][0]),
            research_cutoff,
        )

    def add_risk_affects(
        self,
        risk_id: str,
        target_ids: tuple[str, ...],
    ) -> None:
        if not isinstance(risk_id, str) or not risk_id.strip():
            raise ValueError("risk_id must not be empty")

        if not is_canonical_content_id(
            risk_id,
            kind="risk",
        ):
            raise ValueError(
                "risk_id must be a canonical Risk ID"
            )

        if not isinstance(target_ids, tuple) or not target_ids:
            raise ValueError(
                "target_ids must be a non-empty tuple"
            )

        if any(
            not isinstance(target_id, str)
            or not target_id.strip()
            for target_id in target_ids
        ):
            raise ValueError(
                "target_ids must contain non-empty strings"
            )

        if len(set(target_ids)) != len(target_ids):
            raise ValueError(
                "target_ids must not contain duplicates"
            )

        with self.connect() as con:
            with con.transaction():
                source = con.execute(
                    """
                    SELECT node_type
                    FROM domain_nodes
                    WHERE id = %s
                    """,
                    (risk_id,),
                ).fetchone()

                if source is None:
                    raise RepositoryWriteError(
                        "IDM-W507: EDGE_SOURCE_NOT_FOUND"
                    )

                source_type = str(source[0])
                if source_type != NodeType.RISK.value:
                    raise RepositoryWriteError(
                        "IDM-W509: EDGE_SOURCE_TYPE_MISMATCH"
                    )

                risk_projection = con.execute(
                    """
                    SELECT
                        node_type,
                        subject_id,
                        description,
                        as_of
                    FROM risk_facts
                    WHERE node_id = %s
                    """,
                    (risk_id,),
                ).fetchone()

                if risk_projection is None:
                    raise RepositoryWriteError(
                        "IDM-W554: RISK_PROJECTION_WRITE_LOST"
                    )

                try:
                    persisted_risk = Risk(
                        id=risk_id,
                        subject_id=str(risk_projection[1]),
                        description=str(risk_projection[2]),
                        as_of=risk_projection[3],
                    )
                    validate_node(persisted_risk)
                except (TypeError, ValueError) as exc:
                    raise RepositoryWriteError(
                        "IDM-W555: RISK_PROJECTION_MISMATCH"
                    ) from exc

                payload, payload_hash = self._payload(
                    persisted_risk
                )

                self._assert_existing_node_matches(
                    con,
                    node_id=persisted_risk.id,
                    node_type=persisted_risk.node_type.value,
                    payload=payload,
                    payload_hash=payload_hash,
                )

                self._assert_risk_projection_matches(
                    con,
                    persisted_risk,
                )

                targets = {}

                for target_id in target_ids:
                    target = con.execute(
                        """
                        SELECT node_type
                        FROM domain_nodes
                        WHERE id = %s
                        """,
                        (target_id,),
                    ).fetchone()

                    if target is None:
                        raise RepositoryWriteError(
                            "IDM-W508: EDGE_TARGET_NOT_FOUND"
                        )

                    target_type = str(target[0])

                    if target_type not in (
                        NodeType.CLAIM.value,
                        NodeType.FORECAST.value,
                        NodeType.VALUATION.value,
                    ):
                        raise RepositoryWriteError(
                            "IDM-W510: EDGE_TARGET_TYPE_MISMATCH"
                        )

                    edge = Edge(
                        source_id=risk_id,
                        source_type=NodeType.RISK,
                        edge_type=EdgeType.AFFECTS,
                        target_id=target_id,
                        target_type=NodeType(target_type),
                    )
                    validate_edge(edge)

                    targets[target_id] = target_type

                existing_rows = con.execute(
                    """
                    SELECT target_id, target_type
                    FROM domain_edges
                    WHERE source_id = %s
                      AND source_type = 'Risk'
                      AND edge_type = 'AFFECTS'
                    ORDER BY target_id
                    """,
                    (risk_id,),
                ).fetchall()

                existing = {
                    (str(row[0]), str(row[1]))
                    for row in existing_rows
                }

                expected = {
                    (target_id, targets[target_id])
                    for target_id in target_ids
                }

                # Legal states:
                #   empty -> complete aggregate write
                #   exact complete set -> idempotent replay
                # Any partial, extra, wrong-type or divergent set is
                # persistent-state corruption and is never repaired.
                if existing:
                    if (
                        existing != expected
                        or len(existing_rows) != len(expected)
                    ):
                        raise RepositoryWriteError(
                            "IDM-W557: "
                            "RISK_AFFECTS_SET_MISMATCH"
                        )
                    return

                # All endpoints and Risk AFFECTS semantics have
                # been validated before the first write.
                for target_id in target_ids:
                    result = con.execute(
                        """
                        INSERT INTO domain_edges (
                            source_id,
                            source_type,
                            edge_type,
                            target_id,
                            target_type,
                            created_at
                        )
                        VALUES (
                            %s,
                            'Risk',
                            'AFFECTS',
                            %s,
                            %s,
                            CURRENT_TIMESTAMP
                        )
                        ON CONFLICT DO NOTHING
                        """,
                        (
                            risk_id,
                            target_id,
                            targets[target_id],
                        ),
                    )

                    if result.rowcount != 1:
                        raise RepositoryWriteError(
                            "IDM-W557: "
                            "RISK_AFFECTS_SET_MISMATCH"
                        )

                persisted_rows = con.execute(
                    """
                    SELECT target_id, target_type
                    FROM domain_edges
                    WHERE source_id = %s
                      AND source_type = 'Risk'
                      AND edge_type = 'AFFECTS'
                    """,
                    (risk_id,),
                ).fetchall()

                persisted = {
                    (str(row[0]), str(row[1]))
                    for row in persisted_rows
                }

                if (
                    persisted != expected
                    or len(persisted_rows) != len(expected)
                ):
                    raise RepositoryWriteError(
                        "IDM-W557: "
                        "RISK_AFFECTS_SET_MISMATCH"
                    )


    def add_recommendation(
        self,
        recommendation: Recommendation,
    ) -> None:
        validate_node(recommendation)
        payload, payload_hash = self._payload(recommendation)

        with self.connect() as con:
            with con.transaction():
                node_exists, projection_exists = con.execute(
                    """
                    SELECT
                        EXISTS (
                            SELECT 1
                            FROM domain_nodes
                            WHERE id = %s
                        ),
                        EXISTS (
                            SELECT 1
                            FROM recommendation_facts
                            WHERE node_id = %s
                        )
                    """,
                    (
                        recommendation.id,
                        recommendation.id,
                    ),
                ).fetchone()

                if node_exists and not projection_exists:
                    raise RepositoryWriteError(
                        "IDM-W549: RECOMMENDATION_PROJECTION_WRITE_LOST"
                    )

                if projection_exists and not node_exists:
                    raise RepositoryWriteError(
                        "IDM-W551: RECOMMENDATION_ORPHAN_PROJECTION"
                    )

                if node_exists:
                    self._assert_existing_node_matches(
                        con,
                        node_id=recommendation.id,
                        node_type=recommendation.node_type.value,
                        payload=payload,
                        payload_hash=payload_hash,
                    )
                    self._assert_recommendation_projection_matches(
                        con,
                        recommendation,
                    )
                    return

                self._insert_domain_node(
                    con,
                    node_id=recommendation.id,
                    node_type=recommendation.node_type.value,
                    payload=payload,
                    payload_hash=payload_hash,
                )

                result = con.execute(
                    """
                    INSERT INTO recommendation_facts (
                        node_id,
                        node_type,
                        security_id,
                        action,
                        as_of,
                        created_by,
                        rationale_claim_ids
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (node_id) DO NOTHING
                    """,
                    (
                        recommendation.id,
                        recommendation.node_type.value,
                        recommendation.security_id,
                        recommendation.action,
                        recommendation.as_of,
                        recommendation.created_by,
                        list(recommendation.rationale_claim_ids),
                    ),
                )

                if result.rowcount != 1:
                    self._assert_recommendation_projection_matches(
                        con,
                        recommendation,
                    )

    def recommendation_at(
        self,
        recommendation_id: str,
        research_cutoff: datetime,
    ) -> Recommendation | None:
        if not is_canonical_content_id(
            recommendation_id,
            kind="recommendation",
        ):
            raise ValueError(
                "recommendation_id must be a canonical Recommendation ID"
            )

        if (
            research_cutoff.tzinfo is None
            or research_cutoff.utcoffset() is None
        ):
            raise ValueError(
                "research_cutoff must be timezone-aware"
            )

        with self.connect() as con:
            anchor_row = con.execute(
                """
                SELECT
                    node_type,
                    canonical_payload,
                    payload_hash
                FROM domain_nodes
                WHERE id = %s
                """,
                (recommendation_id,),
            ).fetchone()

            if anchor_row is None:
                return None

            if str(anchor_row[0]) != NodeType.RECOMMENDATION.value:
                raise RepositoryReadError(
                    "IDM-R563: RECOMMENDATION_TYPE_MISMATCH"
                )

            row = con.execute(
                """
                SELECT
                    node_type,
                    security_id,
                    action,
                    as_of,
                    created_by,
                    rationale_claim_ids
                FROM recommendation_facts
                WHERE node_id = %s
                """,
                (recommendation_id,),
            ).fetchone()

            if row is None:
                raise RepositoryReadError(
                    "IDM-R566: RECOMMENDATION_PROJECTION_NOT_FOUND"
                )

            if str(row[0]) != NodeType.RECOMMENDATION.value:
                raise RepositoryReadError(
                    "IDM-R563: RECOMMENDATION_TYPE_MISMATCH"
                )

            try:
                recommendation = Recommendation(
                    id=recommendation_id,
                    security_id=str(row[1]),
                    action=str(row[2]),
                    as_of=row[3],
                    created_by=str(row[4]),
                    rationale_claim_ids=tuple(row[5]),
                )
                validate_node(recommendation)
            except (TypeError, ValueError) as exc:
                raise RepositoryReadError(
                    "IDM-R564: INVALID_STORED_RECOMMENDATION"
                ) from exc

            payload, payload_hash = self._payload(
                recommendation
            )

            if (
                anchor_row[1] != json.loads(payload)
                or str(anchor_row[2]) != payload_hash
            ):
                raise RepositoryReadError(
                    "IDM-R565: RECOMMENDATION_INTEGRITY_FAILURE"
                )

            if recommendation.as_of > research_cutoff:
                return None

            return recommendation

    def latest_recommendation_at(
        self,
        security_id: str,
        research_cutoff: datetime,
    ) -> Recommendation | None:
        if not isinstance(security_id, str) or not security_id.strip():
            raise ValueError(
                "security_id must not be empty"
            )

        if (
            research_cutoff.tzinfo is None
            or research_cutoff.utcoffset() is None
        ):
            raise ValueError(
                "research_cutoff must be timezone-aware"
            )

        with self.connect() as con:
            rows = con.execute(
                """
                SELECT node_id
                FROM recommendation_facts
                WHERE security_id = %s
                  AND as_of = (
                      SELECT MAX(as_of)
                      FROM recommendation_facts
                      WHERE security_id = %s
                        AND as_of <= %s
                  )
                ORDER BY node_id
                """,
                (
                    security_id,
                    security_id,
                    research_cutoff,
                ),
            ).fetchall()

        if not rows:
            return None

        if len(rows) != 1:
            raise RepositoryReadError(
                "IDM-R567: RECOMMENDATION_PIT_AMBIGUITY"
            )

        return self.recommendation_at(
            str(rows[0][0]),
            research_cutoff,
        )

    def valuation_at(
        self,
        valuation_id: str,
        research_cutoff: datetime,
    ) -> Valuation | None:
        if not is_canonical_content_id(
            valuation_id,
            kind="valuation",
        ):
            raise ValueError(
                "valuation_id must be a canonical Valuation ID"
            )

        if (
            research_cutoff.tzinfo is None
            or research_cutoff.utcoffset() is None
        ):
            raise ValueError(
                "research_cutoff must be timezone-aware"
            )

        with self.connect() as con:
            anchor_row = con.execute(
                """
                SELECT
                    node_type,
                    canonical_payload,
                    payload_hash
                FROM domain_nodes
                WHERE id = %s
                """,
                (valuation_id,),
            ).fetchone()

            if anchor_row is None:
                return None

            if str(anchor_row[0]) != NodeType.VALUATION.value:
                raise RepositoryReadError(
                    "IDM-R568: VALUATION_TYPE_MISMATCH"
                )

            row = con.execute(
                """
                SELECT
                    node_type,
                    security_id,
                    method,
                    value,
                    currency,
                    as_of,
                    model_version,
                    scenario
                FROM valuation_facts
                WHERE node_id = %s
                """,
                (valuation_id,),
            ).fetchone()

            if row is None:
                raise RepositoryReadError(
                    "IDM-R571: VALUATION_PROJECTION_NOT_FOUND"
                )

            if str(row[0]) != NodeType.VALUATION.value:
                raise RepositoryReadError(
                    "IDM-R568: VALUATION_TYPE_MISMATCH"
                )

            try:
                valuation = Valuation(
                    id=valuation_id,
                    security_id=str(row[1]),
                    method=str(row[2]),
                    value=row[3],
                    currency=str(row[4]),
                    as_of=row[5],
                    model_version=str(row[6]),
                    scenario=str(row[7]),
                )
                validate_node(valuation)
            except (TypeError, ValueError) as exc:
                raise RepositoryReadError(
                    "IDM-R569: INVALID_STORED_VALUATION"
                ) from exc

            payload, payload_hash = self._payload(
                valuation
            )

            if (
                anchor_row[1] != json.loads(payload)
                or str(anchor_row[2]) != payload_hash
            ):
                raise RepositoryReadError(
                    "IDM-R570: VALUATION_INTEGRITY_FAILURE"
                )

            if valuation.as_of > research_cutoff:
                return None

            return valuation

    def latest_valuation_at(
        self,
        security_id: str,
        method: str,
        scenario: str,
        research_cutoff: datetime,
    ) -> Valuation | None:
        if not isinstance(security_id, str) or not security_id.strip():
            raise ValueError(
                "security_id must not be empty"
            )

        if not isinstance(method, str) or not method.strip():
            raise ValueError(
                "method must not be empty"
            )

        if not isinstance(scenario, str) or not scenario.strip():
            raise ValueError(
                "scenario must not be empty"
            )

        if (
            research_cutoff.tzinfo is None
            or research_cutoff.utcoffset() is None
        ):
            raise ValueError(
                "research_cutoff must be timezone-aware"
            )

        with self.connect() as con:
            rows = con.execute(
                """
                SELECT node_id
                FROM valuation_facts
                WHERE security_id = %s
                  AND method = %s
                  AND scenario = %s
                  AND as_of = (
                      SELECT MAX(as_of)
                      FROM valuation_facts
                      WHERE security_id = %s
                        AND method = %s
                        AND scenario = %s
                        AND as_of <= %s
                  )
                ORDER BY node_id
                """,
                (
                    security_id,
                    method,
                    scenario,
                    security_id,
                    method,
                    scenario,
                    research_cutoff,
                ),
            ).fetchall()

        if not rows:
            return None

        if len(rows) != 1:
            raise RepositoryReadError(
                "IDM-R572: VALUATION_PIT_AMBIGUITY"
            )

        return self.valuation_at(
            str(rows[0][0]),
            research_cutoff,
        )

    def add_valuation(
        self,
        valuation: Valuation,
    ) -> None:
        with self.connect() as con:
            with con.transaction():
                self._add_valuation_in_transaction(
                    con,
                    valuation,
                )

    def _add_valuation_in_transaction(
        self,
        con,
        valuation: Valuation,
    ) -> None:
        validate_node(valuation)
        payload, payload_hash = self._payload(valuation)

        node_exists, projection_exists = con.execute(
            """
            SELECT
                EXISTS (
                    SELECT 1
                    FROM domain_nodes
                    WHERE id = %s
                ),
                EXISTS (
                    SELECT 1
                    FROM valuation_facts
                    WHERE node_id = %s
                )
            """,
            (
                valuation.id,
                valuation.id,
            ),
        ).fetchone()

        if node_exists and not projection_exists:
            raise RepositoryWriteError(
                "IDM-W532: VALUATION_PROJECTION_WRITE_LOST"
            )

        if projection_exists and not node_exists:
            raise RepositoryWriteError(
                "IDM-W534: VALUATION_ORPHAN_PROJECTION"
            )

        if node_exists:
            self._assert_existing_node_matches(
                con,
                node_id=valuation.id,
                node_type=valuation.node_type.value,
                payload=payload,
                payload_hash=payload_hash,
            )
            self._assert_valuation_projection_matches(
                con,
                valuation,
            )
            return

        self._insert_domain_node(
            con,
            node_id=valuation.id,
            node_type=valuation.node_type.value,
            payload=payload,
            payload_hash=payload_hash,
        )

        result = con.execute(
            """
            INSERT INTO valuation_facts (
                node_id,
                node_type,
                security_id,
                method,
                value,
                currency,
                as_of,
                model_version,
                scenario
            )
            VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s
            )
            ON CONFLICT (node_id) DO NOTHING
            """,
            (
                valuation.id,
                valuation.node_type.value,
                valuation.security_id,
                valuation.method,
                valuation.value,
                valuation.currency,
                valuation.as_of,
                valuation.model_version,
                valuation.scenario,
            ),
        )

        if result.rowcount != 1:
            self._assert_valuation_projection_matches(
                con,
                valuation,
            )

    def _load_exact_estimate(
        self,
        con,
        estimate_id: str,
    ) -> Estimate:
        row = con.execute(
            """
            SELECT
                n.node_type,
                n.canonical_payload,
                n.payload_hash,
                e.node_type,
                e.subject_id,
                e.metric_name,
                e.period_end,
                e.value,
                e.unit,
                e.scenario,
                e.model_version,
                e.as_of,
                e.currency
            FROM domain_nodes AS n
            LEFT JOIN estimate_facts AS e
              ON e.node_id = n.id
            WHERE n.id = %s
            """,
            (estimate_id,),
        ).fetchone()

        if (
            row is None
            or str(row[0]) != NodeType.ESTIMATE.value
            or row[3] is None
        ):
            raise RepositoryReadError(
                "IDM-R554: VALUATION_DEPENDENCY_INTEGRITY_FAILURE"
            )

        try:
            estimate = Estimate(
                id=estimate_id,
                subject_id=row[4],
                metric_name=row[5],
                period_end=row[6],
                value=row[7],
                unit=row[8],
                scenario=row[9],
                model_version=row[10],
                as_of=row[11],
                currency=row[12],
            )
            validate_node(estimate)
        except (TypeError, ValueError) as exc:
            raise RepositoryReadError(
                "IDM-R554: VALUATION_DEPENDENCY_INTEGRITY_FAILURE"
            ) from exc

        payload, payload_hash = self._payload(estimate)
        if (
            row[1] != json.loads(payload)
            or str(row[2]) != payload_hash
            or str(row[3]) != NodeType.ESTIMATE.value
        ):
            raise RepositoryReadError(
                "IDM-R554: VALUATION_DEPENDENCY_INTEGRITY_FAILURE"
            )

        return estimate

    def _load_exact_forecast(
        self,
        con,
        forecast_id: str,
    ) -> Forecast:
        row = con.execute(
            """
            SELECT
                n.node_type,
                n.canonical_payload,
                n.payload_hash,
                f.node_type,
                f.subject_id,
                f.scenario,
                f.as_of,
                f.model_version
            FROM domain_nodes AS n
            LEFT JOIN forecast_facts AS f
              ON f.node_id = n.id
            WHERE n.id = %s
            """,
            (forecast_id,),
        ).fetchone()

        if (
            row is None
            or str(row[0]) != NodeType.FORECAST.value
            or row[3] is None
        ):
            raise RepositoryReadError(
                "IDM-R554: VALUATION_DEPENDENCY_INTEGRITY_FAILURE"
            )

        try:
            forecast = Forecast(
                id=forecast_id,
                subject_id=row[4],
                scenario=row[5],
                as_of=row[6],
                model_version=row[7],
            )
            validate_node(forecast)
        except (TypeError, ValueError) as exc:
            raise RepositoryReadError(
                "IDM-R554: VALUATION_DEPENDENCY_INTEGRITY_FAILURE"
            ) from exc

        payload, payload_hash = self._payload(forecast)
        if (
            row[1] != json.loads(payload)
            or str(row[2]) != payload_hash
            or str(row[3]) != NodeType.FORECAST.value
        ):
            raise RepositoryReadError(
                "IDM-R554: VALUATION_DEPENDENCY_INTEGRITY_FAILURE"
            )

        return forecast

    def _load_exact_catalyst_impact(
        self,
        con,
        catalyst_impact_id: str,
    ) -> CatalystImpact:
        anchor = con.execute(
            """
            SELECT
                node_type,
                canonical_payload,
                payload_hash
            FROM domain_nodes
            WHERE id = %s
            """,
            (catalyst_impact_id,),
        ).fetchone()

        if (
            anchor is None
            or str(anchor[0]) != NodeType.CATALYST_IMPACT.value
        ):
            raise RepositoryReadError(
                "IDM-R554: VALUATION_DEPENDENCY_INTEGRITY_FAILURE"
            )

        row = con.execute(
            """
            SELECT
                node_type,
                catalyst_id,
                target_id,
                direction,
                magnitude,
                probability,
                confidence,
                horizon,
                rationale,
                as_of,
                created_by
            FROM catalyst_impact_facts
            WHERE node_id = %s
            """,
            (catalyst_impact_id,),
        ).fetchone()

        if (
            row is None
            or str(row[0]) != NodeType.CATALYST_IMPACT.value
        ):
            raise RepositoryReadError(
                "IDM-R554: VALUATION_DEPENDENCY_INTEGRITY_FAILURE"
            )

        try:
            catalyst_impact = CatalystImpact(
                id=catalyst_impact_id,
                catalyst_id=str(row[1]),
                target_id=str(row[2]),
                direction=str(row[3]),
                magnitude=str(row[4]),
                probability=row[5],
                confidence=row[6],
                horizon=str(row[7]),
                rationale=str(row[8]),
                as_of=row[9],
                created_by=str(row[10]),
            )
            validate_node(catalyst_impact)
        except (TypeError, ValueError) as exc:
            raise RepositoryReadError(
                "IDM-R554: VALUATION_DEPENDENCY_INTEGRITY_FAILURE"
            ) from exc

        payload, payload_hash = self._payload(catalyst_impact)
        if (
            anchor[1] != json.loads(payload)
            or str(anchor[2]) != payload_hash
        ):
            raise RepositoryReadError(
                "IDM-R554: VALUATION_DEPENDENCY_INTEGRITY_FAILURE"
            )

        return catalyst_impact

    def _load_exact_valuation(
        self,
        con,
        valuation_id: str,
    ) -> Valuation:
        row = con.execute(
            """
            SELECT
                n.node_type,
                n.canonical_payload,
                n.payload_hash,
                v.node_type,
                v.security_id,
                v.method,
                v.value,
                v.currency,
                v.as_of,
                v.model_version,
                v.scenario
            FROM domain_nodes AS n
            LEFT JOIN valuation_facts AS v
              ON v.node_id = n.id
            WHERE n.id = %s
            """,
            (valuation_id,),
        ).fetchone()

        if row is None:
            raise RepositoryReadError(
                "IDM-R545: VALUATION_NOT_FOUND"
            )

        if str(row[0]) != NodeType.VALUATION.value:
            raise RepositoryReadError(
                "IDM-R546: VALUATION_TYPE_MISMATCH"
            )

        if row[3] is None:
            raise RepositoryReadError(
                "IDM-R547: VALUATION_PROJECTION_NOT_FOUND"
            )

        try:
            valuation = Valuation(
                id=valuation_id,
                security_id=row[4],
                method=row[5],
                value=row[6],
                currency=row[7],
                as_of=row[8],
                model_version=row[9],
                scenario=row[10],
            )
            validate_node(valuation)
        except (TypeError, ValueError) as exc:
            raise RepositoryReadError(
                "IDM-R548: INVALID_STORED_VALUATION"
            ) from exc

        payload, payload_hash = self._payload(valuation)

        if (
            row[1] != json.loads(payload)
            or str(row[2]) != payload_hash
        ):
            raise RepositoryReadError(
                "IDM-R549: VALUATION_INTEGRITY_FAILURE"
            )

        if str(row[3]) != NodeType.VALUATION.value:
            raise RepositoryReadError(
                "IDM-R548: INVALID_STORED_VALUATION"
            )

        return valuation

    def valuation_inputs_by_ids_at(
        self,
        dependency_ids: tuple[str, ...],
        research_cutoff,
    ) -> tuple:
        if (
            not isinstance(dependency_ids, tuple)
            or not dependency_ids
        ):
            raise ValueError(
                "dependency_ids must be a non-empty tuple"
            )
        if any(
            not isinstance(dependency_id, str)
            or not dependency_id.strip()
            for dependency_id in dependency_ids
        ):
            raise ValueError(
                "dependency_ids must contain non-empty strings"
            )
        if len(dependency_ids) != len(set(dependency_ids)):
            raise ValueError(
                "dependency_ids must not contain duplicates"
            )
        if (
            research_cutoff.tzinfo is None
            or research_cutoff.utcoffset() is None
        ):
            raise ValueError(
                "research_cutoff must be timezone-aware"
            )

        not_visible = (
            "IDM-R550: "
            "VALUATION_INPUT_NOT_VISIBLE_AT_CUTOFF"
        )

        resolved = []
        with self.connect() as con:
            for dependency_id in sorted(dependency_ids):
                row = con.execute(
                    """
                    SELECT node_type
                    FROM domain_nodes
                    WHERE id = %s
                    """,
                    (dependency_id,),
                ).fetchone()

                if row is None:
                    raise RepositoryReadError(not_visible)

                target_type = str(row[0])

                try:
                    if target_type == NodeType.ESTIMATE.value:
                        dependency = self._load_exact_estimate(
                            con,
                            dependency_id,
                        )
                        if (
                            dependency is None
                            or dependency.as_of > research_cutoff
                        ):
                            raise RepositoryReadError(not_visible)

                    elif target_type == NodeType.FORECAST.value:
                        dependency = self._load_exact_forecast(
                            con,
                            dependency_id,
                        )
                        if (
                            dependency is None
                            or dependency.as_of > research_cutoff
                        ):
                            raise RepositoryReadError(not_visible)

                    elif target_type == NodeType.CATALYST_IMPACT.value:
                        dependency = self._load_exact_catalyst_impact(
                            con,
                            dependency_id,
                        )
                        if (
                            dependency is None
                            or dependency.as_of > research_cutoff
                        ):
                            raise RepositoryReadError(not_visible)

                    elif target_type == NodeType.METRIC.value:
                        dependency = self._load_exact_metric(
                            con,
                            dependency_id,
                        )
                        if (
                            dependency is None
                            or not available_at(
                                dependency,
                                research_cutoff,
                            )
                        ):
                            raise RepositoryReadError(not_visible)

                    elif target_type == NodeType.CALCULATION.value:
                        dependency, visible = self._calculation_visible_at(
                            con,
                            dependency_id,
                            research_cutoff,
                            set(),
                        )
                        if not visible:
                            raise RepositoryReadError(not_visible)
                    else:
                        raise RepositoryReadError(not_visible)

                except RepositoryReadError as exc:
                    if str(exc) == not_visible:
                        raise
                    raise RepositoryReadError(not_visible) from exc

                resolved.append(dependency)

        return tuple(resolved)

    def valuation_inputs_at(
        self,
        valuation_id: str,
        research_cutoff,
    ) -> tuple:
        if not isinstance(valuation_id, str) or not valuation_id.strip():
            raise ValueError("valuation_id must not be empty")

        if not is_canonical_content_id(
            valuation_id,
            kind="valuation",
        ):
            raise ValueError(
                "valuation_id must be a canonical Valuation ID"
            )

        if (
            research_cutoff.tzinfo is None
            or research_cutoff.utcoffset() is None
        ):
            raise ValueError(
                "research_cutoff must be timezone-aware"
            )

        with self.connect() as con:
            valuation = self._load_exact_valuation(
                con,
                valuation_id,
            )

            if valuation.as_of > research_cutoff:
                raise RepositoryReadError(
                    "IDM-R551: VALUATION_NOT_VISIBLE_AT_CUTOFF"
                )

            dependency_rows = con.execute(
                """
                SELECT
                    target_id,
                    target_type,
                    created_at
                FROM domain_edges
                WHERE source_id = %s
                  AND source_type = 'Valuation'
                  AND edge_type = 'DEPENDS_ON'
                ORDER BY target_id
                """,
                (valuation_id,),
            ).fetchall()

            resolved = []

            for target_id, target_type, edge_created_at in dependency_rows:
                target_id = str(target_id)
                target_type = str(target_type)

                if edge_created_at > research_cutoff:
                    raise RepositoryReadError(
                        "IDM-R552: "
                        "VALUATION_DEPENDENCY_EDGE_NOT_VISIBLE_AT_CUTOFF"
                    )

                if target_type == NodeType.ESTIMATE.value:
                    row = con.execute(
                        """
                        SELECT
                            n.node_type,
                            n.canonical_payload,
                            n.payload_hash,
                            e.node_type,
                            e.subject_id,
                            e.metric_name,
                            e.period_end,
                            e.value,
                            e.unit,
                            e.scenario,
                            e.model_version,
                            e.as_of,
                            e.currency
                        FROM domain_nodes AS n
                        LEFT JOIN estimate_facts AS e
                          ON e.node_id = n.id
                        WHERE n.id = %s
                        """,
                        (target_id,),
                    ).fetchone()

                    if (
                        row is None
                        or str(row[0]) != NodeType.ESTIMATE.value
                        or row[3] is None
                    ):
                        raise RepositoryReadError(
                            "IDM-R550: "
                            "VALUATION_INPUT_NOT_VISIBLE_AT_CUTOFF"
                        )

                    try:
                        dependency = Estimate(
                            id=target_id,
                            subject_id=row[4],
                            metric_name=row[5],
                            period_end=row[6],
                            value=row[7],
                            unit=row[8],
                            scenario=row[9],
                            model_version=row[10],
                            as_of=row[11],
                            currency=row[12],
                        )
                        validate_node(dependency)
                    except (TypeError, ValueError) as exc:
                        raise RepositoryReadError(
                            "IDM-R550: "
                            "VALUATION_INPUT_NOT_VISIBLE_AT_CUTOFF"
                        ) from exc

                    dependency_payload, dependency_hash = (
                        self._payload(dependency)
                    )

                    if (
                        row[1] != json.loads(dependency_payload)
                        or str(row[2]) != dependency_hash
                        or str(row[3]) != NodeType.ESTIMATE.value
                    ):
                        raise RepositoryReadError(
                            "IDM-R550: "
                            "VALUATION_INPUT_NOT_VISIBLE_AT_CUTOFF"
                        )

                    if dependency.as_of > research_cutoff:
                        raise RepositoryReadError(
                            "IDM-R550: "
                            "VALUATION_INPUT_NOT_VISIBLE_AT_CUTOFF"
                        )

                    resolved.append(dependency)
                    continue

                if target_type == NodeType.CATALYST_IMPACT.value:
                    dependency = self.catalyst_impact_at(
                        target_id,
                        research_cutoff,
                    )

                    if dependency is None:
                        raise RepositoryReadError(
                            "IDM-R550: "
                            "VALUATION_INPUT_NOT_VISIBLE_AT_CUTOFF"
                        )

                    resolved.append(dependency)
                    continue

                if target_type == NodeType.FORECAST.value:
                    row = con.execute(
                        """
                        SELECT
                            n.node_type,
                            n.canonical_payload,
                            n.payload_hash,
                            f.node_type,
                            f.subject_id,
                            f.scenario,
                            f.as_of,
                            f.model_version
                        FROM domain_nodes AS n
                        LEFT JOIN forecast_facts AS f
                          ON f.node_id = n.id
                        WHERE n.id = %s
                        """,
                        (target_id,),
                    ).fetchone()

                    if (
                        row is None
                        or str(row[0]) != NodeType.FORECAST.value
                        or row[3] is None
                    ):
                        raise RepositoryReadError(
                            "IDM-R550: "
                            "VALUATION_INPUT_NOT_VISIBLE_AT_CUTOFF"
                        )

                    try:
                        dependency = Forecast(
                            id=target_id,
                            subject_id=row[4],
                            scenario=row[5],
                            as_of=row[6],
                            model_version=row[7],
                        )
                        validate_node(dependency)
                    except (TypeError, ValueError) as exc:
                        raise RepositoryReadError(
                            "IDM-R550: "
                            "VALUATION_INPUT_NOT_VISIBLE_AT_CUTOFF"
                        ) from exc

                    dependency_payload, dependency_hash = self._payload(
                        dependency
                    )
                    if (
                        row[1] != json.loads(dependency_payload)
                        or str(row[2]) != dependency_hash
                        or str(row[3]) != NodeType.FORECAST.value
                    ):
                        raise RepositoryReadError(
                            "IDM-R550: "
                            "VALUATION_INPUT_NOT_VISIBLE_AT_CUTOFF"
                        )

                    if dependency.as_of > research_cutoff:
                        raise RepositoryReadError(
                            "IDM-R550: "
                            "VALUATION_INPUT_NOT_VISIBLE_AT_CUTOFF"
                        )

                    resolved.append(dependency)
                    continue

                if target_type == NodeType.METRIC.value:
                    try:
                        dependency = self._load_exact_metric(
                            con,
                            target_id,
                        )
                    except RepositoryReadError as exc:
                        raise RepositoryReadError(
                            "IDM-R550: "
                            "VALUATION_INPUT_NOT_VISIBLE_AT_CUTOFF"
                        ) from exc

                    if not available_at(
                        dependency,
                        research_cutoff,
                    ):
                        raise RepositoryReadError(
                            "IDM-R550: "
                            "VALUATION_INPUT_NOT_VISIBLE_AT_CUTOFF"
                        )

                    resolved.append(dependency)
                    continue

                if target_type == NodeType.CALCULATION.value:
                    try:
                        dependency, visible = (
                            self._calculation_visible_at(
                                con,
                                target_id,
                                research_cutoff,
                                set(),
                            )
                        )
                    except RepositoryReadError as exc:
                        raise RepositoryReadError(
                            "IDM-R550: "
                            "VALUATION_INPUT_NOT_VISIBLE_AT_CUTOFF"
                        ) from exc

                    if not visible:
                        raise RepositoryReadError(
                            "IDM-R550: "
                            "VALUATION_INPUT_NOT_VISIBLE_AT_CUTOFF"
                        )

                    resolved.append(dependency)
                    continue

                raise RepositoryReadError(
                    "IDM-R550: "
                    "VALUATION_INPUT_NOT_VISIBLE_AT_CUTOFF"
                )

            return tuple(resolved)


    def add_calculation(
        self,
        calculation: Calculation,
    ) -> None:
        validate_node(calculation)
        validate_calculation(calculation)

        payload, payload_hash = self._payload(calculation)

        with self.connect() as con:
            with con.transaction():
                node_exists = (
                    con.execute(
                        """
                        SELECT 1
                        FROM domain_nodes
                        WHERE id = %s
                        """,
                        (calculation.id,),
                    ).fetchone()
                    is not None
                )

                projection_exists = (
                    con.execute(
                        """
                        SELECT 1
                        FROM calculation_facts
                        WHERE node_id = %s
                        """,
                        (calculation.id,),
                    ).fetchone()
                    is not None
                )

                if node_exists and not projection_exists:
                    raise RepositoryWriteError(
                        "IDM-W521: CALCULATION_PROJECTION_WRITE_LOST"
                    )

                if projection_exists and not node_exists:
                    raise RepositoryWriteError(
                        "IDM-W523: CALCULATION_ORPHAN_PROJECTION"
                    )

                if node_exists:
                    self._assert_existing_node_matches(
                        con,
                        node_id=calculation.id,
                        node_type=calculation.node_type.value,
                        payload=payload,
                        payload_hash=payload_hash,
                    )
                    self._assert_calculation_projection_matches(
                        con,
                        calculation,
                    )
                    return

                self._insert_domain_node(
                    con,
                    node_id=calculation.id,
                    node_type=calculation.node_type.value,
                    payload=payload,
                    payload_hash=payload_hash,
                )

                result = con.execute(
                    """
                    INSERT INTO calculation_facts (
                        node_id,
                        node_type,
                        subject_id,
                        formula,
                        input_ids,
                        value,
                        unit,
                        currency,
                        model_version
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (node_id) DO NOTHING
                    """,
                    (
                        calculation.id,
                        calculation.node_type.value,
                        calculation.subject_id,
                        calculation.formula,
                        list(calculation.input_ids),
                        calculation.value,
                        calculation.unit,
                        calculation.currency,
                        calculation.model_version,
                    ),
                )

                if result.rowcount != 1:
                    raise RepositoryWriteError(
                        "IDM-W521: CALCULATION_PROJECTION_WRITE_LOST"
                    )

    @staticmethod
    def _calculation_dependency_reaches(
        con,
        *,
        start_id: str,
        sought_id: str,
    ) -> bool:
        """
        Return True when an existing Calculation->Calculation DERIVED_FROM
        path from start_id reaches sought_id.

        A recursive SQL traversal is used instead of Python row-order
        traversal. UNION (not UNION ALL) makes pre-existing cycles finite.
        """
        row = con.execute(
            """
            WITH RECURSIVE reachable(node_id) AS (
                SELECT %s::text

                UNION

                SELECT e.target_id
                FROM domain_edges e
                JOIN reachable r
                  ON e.source_id = r.node_id
                WHERE e.source_type = 'Calculation'
                  AND e.edge_type = 'DERIVED_FROM'
                  AND e.target_type = 'Calculation'
            )
            SELECT 1
            FROM reachable
            WHERE node_id = %s
            LIMIT 1
            """,
            (start_id, sought_id),
        ).fetchone()
        return row is not None


    def add_recommendation_dependencies(
        self,
        recommendation_id: str,
        dependency_ids: tuple[str, ...],
    ) -> None:
        if (
            not isinstance(recommendation_id, str)
            or not recommendation_id.strip()
            or not is_canonical_content_id(
                recommendation_id,
                kind="recommendation",
            )
        ):
            raise ValueError(
                "recommendation_id must be a canonical Recommendation ID"
            )
        if not isinstance(dependency_ids, tuple) or not dependency_ids:
            raise ValueError("dependency_ids must be a non-empty tuple")
        if any(
            not isinstance(dependency_id, str) or not dependency_id
            for dependency_id in dependency_ids
        ):
            raise ValueError(
                "dependency_ids must contain non-empty strings"
            )
        if len(set(dependency_ids)) != len(dependency_ids):
            raise ValueError(
                "dependency_ids must not contain duplicates"
            )

        allowed_target_types = {
            "Valuation",
            "Claim",
            "Risk",
            "Catalyst",
        }

        with self.connect() as con:
            with con.transaction():
                source_row = con.execute(
                    """
                    SELECT
                        id,
                        node_type,
                        canonical_payload,
                        payload_hash
                    FROM domain_nodes
                    WHERE id = %s
                    """,
                    (recommendation_id,),
                ).fetchone()

                if source_row is None:
                    raise RepositoryWriteError(
                        "IDM-W507: EDGE_SOURCE_NOT_FOUND"
                    )

                if source_row[1] != "Recommendation":
                    raise RepositoryWriteError(
                        "IDM-W509: EDGE_SOURCE_TYPE_MISMATCH"
                    )

                projection_row = con.execute(
                    """
                    SELECT
                        security_id,
                        action,
                        as_of,
                        created_by,
                        rationale_claim_ids
                    FROM recommendation_facts
                    WHERE node_id = %s
                    """,
                    (recommendation_id,),
                ).fetchone()

                if projection_row is None:
                    raise RepositoryWriteError(
                        "IDM-W549: RECOMMENDATION_PROJECTION_WRITE_LOST"
                    )

                try:
                    recommendation = Recommendation(
                        id=recommendation_id,
                        security_id=projection_row[0],
                        action=projection_row[1],
                        as_of=projection_row[2],
                        created_by=projection_row[3],
                        rationale_claim_ids=tuple(
                            projection_row[4] or ()
                        ),
                    )
                    validate_node(recommendation)
                except (TypeError, ValueError) as exc:
                    raise RepositoryWriteError(
                        "IDM-W550: "
                        "RECOMMENDATION_PROJECTION_MISMATCH"
                    ) from exc

                payload, payload_hash = self._payload(
                    recommendation
                )

                try:
                    self._assert_existing_node_matches(
                        con,
                        node_id=recommendation.id,
                        node_type=recommendation.node_type.value,
                        payload=payload,
                        payload_hash=payload_hash,
                    )
                except RepositoryWriteError as exc:
                    raise RepositoryWriteError(
                        "IDM-W550: "
                        "RECOMMENDATION_PROJECTION_MISMATCH"
                    ) from exc

                self._assert_recommendation_projection_matches(
                    con,
                    recommendation,
                )

                placeholders = ", ".join(
                    ["%s"] * len(dependency_ids)
                )

                target_rows = con.execute(
                    f"""
                    SELECT id, node_type
                    FROM domain_nodes
                    WHERE id IN ({placeholders})
                    """,
                    dependency_ids,
                ).fetchall()

                targets = {
                    row[0]: row[1]
                    for row in target_rows
                }

                missing = [
                    dependency_id
                    for dependency_id in dependency_ids
                    if dependency_id not in targets
                ]
                if missing:
                    raise RepositoryWriteError(
                        "IDM-W508: EDGE_TARGET_NOT_FOUND"
                    )

                wrong_type = [
                    dependency_id
                    for dependency_id in dependency_ids
                    if targets[dependency_id]
                    not in allowed_target_types
                ]
                if wrong_type:
                    raise RepositoryWriteError(
                        "IDM-W510: EDGE_TARGET_TYPE_MISMATCH"
                    )

                supplied_claim_ids = {
                    dependency_id
                    for dependency_id in dependency_ids
                    if targets[dependency_id] == "Claim"
                }
                expected_claim_ids = set(
                    recommendation.rationale_claim_ids
                )

                if supplied_claim_ids != expected_claim_ids:
                    raise RepositoryWriteError(
                        "IDM-W553: "
                        "RECOMMENDATION_RATIONALE_CLAIM_SET_MISMATCH"
                    )

                expected = {
                    (
                        dependency_id,
                        targets[dependency_id],
                    )
                    for dependency_id in dependency_ids
                }

                existing_rows = con.execute(
                    """
                    SELECT target_id, target_type
                    FROM domain_edges
                    WHERE source_id = %s
                      AND source_type = 'Recommendation'
                      AND edge_type = 'DEPENDS_ON'
                    """,
                    (recommendation_id,),
                ).fetchall()

                existing = {
                    (row[0], row[1])
                    for row in existing_rows
                }

                if existing:
                    if existing == expected:
                        return
                    raise RepositoryWriteError(
                        "IDM-W552: "
                        "RECOMMENDATION_DEPENDENCY_SET_MISMATCH"
                    )

                for dependency_id in dependency_ids:
                    con.execute(
                        """
                        INSERT INTO domain_edges (
                            source_id,
                            source_type,
                            edge_type,
                            target_id,
                            target_type,
                            created_at
                        )
                        VALUES (
                            %s,
                            'Recommendation',
                            'DEPENDS_ON',
                            %s,
                            %s,
                            CURRENT_TIMESTAMP
                        )
                        """,
                        (
                            recommendation_id,
                            dependency_id,
                            targets[dependency_id],
                        ),
                    )

                persisted_rows = con.execute(
                    """
                    SELECT target_id, target_type
                    FROM domain_edges
                    WHERE source_id = %s
                      AND source_type = 'Recommendation'
                      AND edge_type = 'DEPENDS_ON'
                    """,
                    (recommendation_id,),
                ).fetchall()

                persisted = {
                    (row[0], row[1])
                    for row in persisted_rows
                }

                if persisted != expected:
                    raise RepositoryWriteError(
                        "IDM-W552: "
                        "RECOMMENDATION_DEPENDENCY_SET_MISMATCH"
                    )


    def add_valuation_dependencies(
        self,
        valuation_id: str,
        dependency_ids: tuple[str, ...],
    ) -> None:
        if not isinstance(valuation_id, str) or not valuation_id.strip():
            raise ValueError("valuation_id must not be empty")
        if not is_canonical_content_id(
            valuation_id,
            kind="valuation",
        ):
            raise ValueError(
                "valuation_id must be a canonical Valuation ID"
            )
        if not isinstance(dependency_ids, tuple) or not dependency_ids:
            raise ValueError(
                "dependency_ids must be a non-empty tuple"
            )
        if any(
            not isinstance(dependency_id, str)
            or not dependency_id.strip()
            for dependency_id in dependency_ids
        ):
            raise ValueError(
                "dependency_ids must contain non-empty strings"
            )
        if len(dependency_ids) != len(set(dependency_ids)):
            raise ValueError(
                "dependency_ids must not contain duplicates"
            )

        with self.connect() as con:
            with con.transaction():
                self._add_valuation_dependencies_in_transaction(
                    con,
                    valuation_id,
                    dependency_ids,
                )

    def _add_valuation_dependencies_in_transaction(
        self,
        con,
        valuation_id: str,
        dependency_ids: tuple[str, ...],
    ) -> None:
        if not isinstance(valuation_id, str) or not valuation_id.strip():
            raise ValueError("valuation_id must not be empty")
        if not is_canonical_content_id(
            valuation_id,
            kind="valuation",
        ):
            raise ValueError(
                "valuation_id must be a canonical Valuation ID"
            )
        if not isinstance(dependency_ids, tuple) or not dependency_ids:
            raise ValueError(
                "dependency_ids must be a non-empty tuple"
            )
        if any(
            not isinstance(dependency_id, str)
            or not dependency_id.strip()
            for dependency_id in dependency_ids
        ):
            raise ValueError(
                "dependency_ids must contain non-empty strings"
            )
        if len(dependency_ids) != len(set(dependency_ids)):
            raise ValueError(
                "dependency_ids must not contain duplicates"
            )

        source = con.execute(
            """
            SELECT node_type
            FROM domain_nodes
            WHERE id = %s
            FOR UPDATE
            """,
            (valuation_id,),
        ).fetchone()

        if source is None:
            raise RepositoryWriteError(
                "IDM-W507: EDGE_SOURCE_NOT_FOUND"
            )

        if str(source[0]) != "Valuation":
            raise RepositoryWriteError(
                "IDM-W509: EDGE_SOURCE_TYPE_MISMATCH"
            )

        projection = con.execute(
            """
            SELECT
                node_type,
                security_id,
                method,
                value,
                currency,
                as_of,
                model_version,
                scenario
            FROM valuation_facts
            WHERE node_id = %s
            """,
            (valuation_id,),
        ).fetchone()

        if projection is None:
            raise RepositoryWriteError(
                "IDM-W532: VALUATION_PROJECTION_WRITE_LOST"
            )

        try:
            persisted_valuation = Valuation(
                id=valuation_id,
                security_id=projection[1],
                method=projection[2],
                value=projection[3],
                currency=projection[4],
                as_of=projection[5],
                model_version=projection[6],
                scenario=projection[7],
            )
            validate_node(persisted_valuation)
        except (TypeError, ValueError) as exc:
            raise RepositoryWriteError(
                "IDM-W533: VALUATION_PROJECTION_MISMATCH"
            ) from exc

        payload, payload_hash = self._payload(
            persisted_valuation
        )
        self._assert_existing_node_matches(
            con,
            node_id=persisted_valuation.id,
            node_type=persisted_valuation.node_type.value,
            payload=payload,
            payload_hash=payload_hash,
        )
        self._assert_valuation_projection_matches(
            con,
            persisted_valuation,
        )

        targets = {}
        for target_id in dependency_ids:
            target = con.execute(
                """
                SELECT node_type
                FROM domain_nodes
                WHERE id = %s
                """,
                (target_id,),
            ).fetchone()

            if target is None:
                raise RepositoryWriteError(
                    "IDM-W508: EDGE_TARGET_NOT_FOUND"
                )

            target_type = str(target[0])
            if target_type not in (
                "Forecast",
                "Estimate",
                "Metric",
                "Calculation",
                "CatalystImpact",
            ):
                raise RepositoryWriteError(
                    "IDM-W510: EDGE_TARGET_TYPE_MISMATCH"
                )

            targets[target_id] = target_type

        existing_rows = con.execute(
            """
            SELECT target_id, target_type
            FROM domain_edges
            WHERE source_id = %s
              AND source_type = 'Valuation'
              AND edge_type = 'DEPENDS_ON'
            ORDER BY target_id
            """,
            (valuation_id,),
        ).fetchall()

        existing = {
            (str(row[0]), str(row[1]))
            for row in existing_rows
        }
        expected = {
            (target_id, targets[target_id])
            for target_id in dependency_ids
        }

        # Legal states:
        #   empty -> complete aggregate write
        #   exact complete set -> idempotent replay
        # Any partial, extra, wrong-type or divergent set is
        # persistent-state corruption and is never repaired.
        if existing:
            if (
                existing != expected
                or len(existing_rows) != len(expected)
            ):
                raise RepositoryWriteError(
                    "IDM-W535: "
                    "VALUATION_DEPENDENCY_SET_MISMATCH"
                )
            return

        # All endpoints have been validated before the first write.
        for target_id in dependency_ids:
            result = con.execute(
                """
                INSERT INTO domain_edges (
                    source_id,
                    source_type,
                    edge_type,
                    target_id,
                    target_type,
                    created_at
                )
                VALUES (
                    %s,
                    'Valuation',
                    'DEPENDS_ON',
                    %s,
                    %s,
                    CURRENT_TIMESTAMP
                )
                ON CONFLICT DO NOTHING
                """,
                (
                    valuation_id,
                    target_id,
                    targets[target_id],
                ),
            )

            if result.rowcount != 1:
                raise RepositoryWriteError(
                    "IDM-W535: "
                    "VALUATION_DEPENDENCY_SET_MISMATCH"
                )

        persisted_rows = con.execute(
            """
            SELECT target_id, target_type
            FROM domain_edges
            WHERE source_id = %s
              AND source_type = 'Valuation'
              AND edge_type = 'DEPENDS_ON'
            """,
            (valuation_id,),
        ).fetchall()

        persisted = {
            (str(row[0]), str(row[1]))
            for row in persisted_rows
        }

        if (
            persisted != expected
            or len(persisted_rows) != len(expected)
        ):
            raise RepositoryWriteError(
                "IDM-W535: "
                "VALUATION_DEPENDENCY_SET_MISMATCH"
            )

    def add_calculation_inputs(
        self,
        calculation_id: str,
    ) -> None:
        if not isinstance(calculation_id, str) or not calculation_id.strip():
            raise ValueError("calculation_id must not be empty")
        if not is_canonical_content_id(calculation_id, kind="calculation"):
            raise ValueError(
                "calculation_id must be a canonical Calculation ID"
            )

        with self.connect() as con:
            with con.transaction():
                source = con.execute(
                    """
                    SELECT node_type
                    FROM domain_nodes
                    WHERE id = %s
                    FOR UPDATE
                    """,
                    (calculation_id,),
                ).fetchone()

                if source is None:
                    raise RepositoryWriteError(
                        "IDM-W507: EDGE_SOURCE_NOT_FOUND"
                    )

                if str(source[0]) != "Calculation":
                    raise RepositoryWriteError(
                        "IDM-W509: EDGE_SOURCE_TYPE_MISMATCH"
                    )

                projection = con.execute(
                    """
                    SELECT
                        node_type,
                        subject_id,
                        formula,
                        input_ids,
                        value,
                        unit,
                        currency,
                        model_version
                    FROM calculation_facts
                    WHERE node_id = %s
                    """,
                    (calculation_id,),
                ).fetchone()

                if projection is None:
                    raise RepositoryWriteError(
                        "IDM-W526: CALCULATION_SOURCE_PROJECTION_NOT_FOUND"
                    )

                try:
                    persisted_calculation = Calculation(
                        id=calculation_id,
                        subject_id=projection[1],
                        formula=projection[2],
                        input_ids=tuple(projection[3]),
                        value=projection[4],
                        unit=projection[5],
                        currency=projection[6],
                        model_version=projection[7],
                    )
                    validate_node(persisted_calculation)
                    validate_calculation(persisted_calculation)
                except (TypeError, ValueError) as exc:
                    raise RepositoryWriteError(
                        "IDM-W522: CALCULATION_PROJECTION_MISMATCH"
                    ) from exc

                payload, payload_hash = self._payload(
                    persisted_calculation
                )
                self._assert_existing_node_matches(
                    con,
                    node_id=persisted_calculation.id,
                    node_type=persisted_calculation.node_type.value,
                    payload=payload,
                    payload_hash=payload_hash,
                )
                self._assert_calculation_projection_matches(
                    con,
                    persisted_calculation,
                )

                input_ids = persisted_calculation.input_ids

                # Calculation validation already guarantees non-empty,
                # duplicate-free input_ids at write time. Persistent state
                # must nevertheless be treated fail-closed.
                if not input_ids or len(input_ids) != len(set(input_ids)):
                    raise RepositoryWriteError(
                        "IDM-W524: CALCULATION_INPUT_SET_MISMATCH"
                    )

                targets = {}
                for target_id in input_ids:
                    target = con.execute(
                        """
                        SELECT node_type
                        FROM domain_nodes
                        WHERE id = %s
                        """,
                        (target_id,),
                    ).fetchone()

                    if target is None:
                        raise RepositoryWriteError(
                            "IDM-W508: EDGE_TARGET_NOT_FOUND"
                        )

                    target_type = str(target[0])
                    if target_type not in ("Metric", "Calculation"):
                        raise RepositoryWriteError(
                            "IDM-W510: EDGE_TARGET_TYPE_MISMATCH"
                        )

                    if target_id == calculation_id:
                        raise RepositoryWriteError(
                            "IDM-W525: CALCULATION_DEPENDENCY_CYCLE"
                        )

                    targets[target_id] = target_type

                existing_rows = con.execute(
                    """
                    SELECT target_id, target_type
                    FROM domain_edges
                    WHERE source_id = %s
                      AND source_type = 'Calculation'
                      AND edge_type = 'DERIVED_FROM'
                    ORDER BY target_id
                    """,
                    (calculation_id,),
                ).fetchall()

                existing = {
                    (str(row[0]), str(row[1]))
                    for row in existing_rows
                }
                expected = {
                    (target_id, targets[target_id])
                    for target_id in input_ids
                }

                # Legal states:
                #   empty -> complete aggregate write
                #   exact complete set -> idempotent replay
                # Any partial, extra, wrong-type or otherwise divergent set
                # is persistent-state corruption and is never repaired.
                if existing:
                    if existing != expected or len(existing_rows) != len(expected):
                        raise RepositoryWriteError(
                            "IDM-W524: CALCULATION_INPUT_SET_MISMATCH"
                        )

                    # Replay must still fail closed if the persisted graph has
                    # become cyclic through repository-external corruption.
                    for target_id, target_type in expected:
                        if (
                            target_type == "Calculation"
                            and self._calculation_dependency_reaches(
                                con,
                                start_id=target_id,
                                sought_id=calculation_id,
                            )
                        ):
                            raise RepositoryWriteError(
                                "IDM-W525: CALCULATION_DEPENDENCY_CYCLE"
                            )
                    return

                # Validate the entire aggregate before the first edge insert.
                # Therefore missing endpoints/cycles cannot leave a partial
                # provenance set.
                for target_id in input_ids:
                    if targets[target_id] != "Calculation":
                        continue

                    if self._calculation_dependency_reaches(
                        con,
                        start_id=target_id,
                        sought_id=calculation_id,
                    ):
                        raise RepositoryWriteError(
                            "IDM-W525: CALCULATION_DEPENDENCY_CYCLE"
                        )

                for target_id in input_ids:
                    result = con.execute(
                        """
                        INSERT INTO domain_edges (
                            source_id,
                            source_type,
                            edge_type,
                            target_id,
                            target_type,
                            created_at
                        )
                        VALUES (
                            %s,
                            'Calculation',
                            'DERIVED_FROM',
                            %s,
                            %s,
                            CURRENT_TIMESTAMP
                        )
                        ON CONFLICT DO NOTHING
                        """,
                        (
                            calculation_id,
                            target_id,
                            targets[target_id],
                        ),
                    )

                    if result.rowcount != 1:
                        raise RepositoryWriteError(
                            "IDM-W524: CALCULATION_INPUT_SET_MISMATCH"
                        )

                persisted_rows = con.execute(
                    """
                    SELECT target_id, target_type
                    FROM domain_edges
                    WHERE source_id = %s
                      AND source_type = 'Calculation'
                      AND edge_type = 'DERIVED_FROM'
                    """,
                    (calculation_id,),
                ).fetchall()

                persisted = {
                    (str(row[0]), str(row[1]))
                    for row in persisted_rows
                }

                if (
                    persisted != expected
                    or len(persisted_rows) != len(expected)
                ):
                    raise RepositoryWriteError(
                        "IDM-W524: CALCULATION_INPUT_SET_MISMATCH"
                    )

    def add_estimate_inputs(
        self,
        estimate_id: str,
        input_ids: tuple[str, ...],
    ) -> None:
        if not isinstance(estimate_id, str) or not estimate_id.strip():
            raise ValueError("estimate_id must not be empty")
        if not is_canonical_content_id(estimate_id, kind="estimate"):
            raise ValueError(
                "estimate_id must be a canonical Estimate ID"
            )
        if not isinstance(input_ids, tuple) or not input_ids:
            raise ValueError("input_ids must be a non-empty tuple")
        if any(
            not isinstance(input_id, str) or not input_id.strip()
            for input_id in input_ids
        ):
            raise ValueError("input_ids must contain non-empty strings")
        if len(input_ids) != len(set(input_ids)):
            raise ValueError("input_ids must not contain duplicates")

        with self.connect() as con:
            with con.transaction():
                source = con.execute(
                    """
                    SELECT node_type
                    FROM domain_nodes
                    WHERE id = %s
                    FOR UPDATE
                    """,
                    (estimate_id,),
                ).fetchone()

                if source is None:
                    raise RepositoryWriteError(
                        "IDM-W507: EDGE_SOURCE_NOT_FOUND"
                    )

                if str(source[0]) != "Estimate":
                    raise RepositoryWriteError(
                        "IDM-W509: EDGE_SOURCE_TYPE_MISMATCH"
                    )

                projection = con.execute(
                    """
                    SELECT
                        node_type,
                        subject_id,
                        metric_name,
                        period_end,
                        value,
                        unit,
                        scenario,
                        model_version,
                        as_of,
                        currency
                    FROM estimate_facts
                    WHERE node_id = %s
                    """,
                    (estimate_id,),
                ).fetchone()

                if projection is None:
                    raise RepositoryWriteError(
                        "IDM-W528: ESTIMATE_PROJECTION_WRITE_LOST"
                    )

                try:
                    persisted_estimate = Estimate(
                        id=estimate_id,
                        subject_id=projection[1],
                        metric_name=projection[2],
                        period_end=projection[3],
                        value=projection[4],
                        unit=projection[5],
                        scenario=projection[6],
                        model_version=projection[7],
                        as_of=projection[8],
                        currency=projection[9],
                    )
                    validate_node(persisted_estimate)
                except (TypeError, ValueError) as exc:
                    raise RepositoryWriteError(
                        "IDM-W529: ESTIMATE_PROJECTION_MISMATCH"
                    ) from exc

                payload, payload_hash = self._payload(
                    persisted_estimate
                )
                self._assert_existing_node_matches(
                    con,
                    node_id=persisted_estimate.id,
                    node_type=persisted_estimate.node_type.value,
                    payload=payload,
                    payload_hash=payload_hash,
                )
                self._assert_estimate_projection_matches(
                    con,
                    persisted_estimate,
                )

                targets = {}
                for target_id in input_ids:
                    target = con.execute(
                        """
                        SELECT node_type
                        FROM domain_nodes
                        WHERE id = %s
                        """,
                        (target_id,),
                    ).fetchone()

                    if target is None:
                        raise RepositoryWriteError(
                            "IDM-W508: EDGE_TARGET_NOT_FOUND"
                        )

                    target_type = str(target[0])
                    if target_type not in (
                        "Metric",
                        "Calculation",
                        "Claim",
                    ):
                        raise RepositoryWriteError(
                            "IDM-W510: EDGE_TARGET_TYPE_MISMATCH"
                        )

                    targets[target_id] = target_type

                existing_rows = con.execute(
                    """
                    SELECT target_id, target_type
                    FROM domain_edges
                    WHERE source_id = %s
                      AND source_type = 'Estimate'
                      AND edge_type = 'DERIVED_FROM'
                    ORDER BY target_id
                    """,
                    (estimate_id,),
                ).fetchall()

                existing = {
                    (str(row[0]), str(row[1]))
                    for row in existing_rows
                }
                expected = {
                    (target_id, targets[target_id])
                    for target_id in input_ids
                }

                # Legal states:
                #   empty -> complete aggregate write
                #   exact complete set -> idempotent replay
                # Any partial, extra, wrong-type or divergent set is
                # persistent-state corruption and is never repaired.
                if existing:
                    if (
                        existing != expected
                        or len(existing_rows) != len(expected)
                    ):
                        raise RepositoryWriteError(
                            "IDM-W531: ESTIMATE_INPUT_SET_MISMATCH"
                        )
                    return

                # All endpoints have been validated before the first write.
                for target_id in input_ids:
                    result = con.execute(
                        """
                        INSERT INTO domain_edges (
                            source_id,
                            source_type,
                            edge_type,
                            target_id,
                            target_type,
                            created_at
                        )
                        VALUES (
                            %s,
                            'Estimate',
                            'DERIVED_FROM',
                            %s,
                            %s,
                            CURRENT_TIMESTAMP
                        )
                        ON CONFLICT DO NOTHING
                        """,
                        (
                            estimate_id,
                            target_id,
                            targets[target_id],
                        ),
                    )

                    if result.rowcount != 1:
                        raise RepositoryWriteError(
                            "IDM-W531: ESTIMATE_INPUT_SET_MISMATCH"
                        )

                persisted_rows = con.execute(
                    """
                    SELECT target_id, target_type
                    FROM domain_edges
                    WHERE source_id = %s
                      AND source_type = 'Estimate'
                      AND edge_type = 'DERIVED_FROM'
                    """,
                    (estimate_id,),
                ).fetchall()

                persisted = {
                    (str(row[0]), str(row[1]))
                    for row in persisted_rows
                }

                if (
                    persisted != expected
                    or len(persisted_rows) != len(expected)
                ):
                    raise RepositoryWriteError(
                        "IDM-W531: ESTIMATE_INPUT_SET_MISMATCH"
                    )

    def add_metric(self, metric: Metric) -> None:
        validate_node(metric)
        payload, payload_hash = self._payload(metric)

        with self.connect() as con:
            with con.transaction():
                if metric.supersedes_id is not None:
                    self._validate_metric_revision_append(
                        con,
                        metric,
                    )

                self._insert_domain_node(
                    con,
                    node_id=metric.id,
                    node_type=metric.node_type.value,
                    payload=payload,
                    payload_hash=payload_hash,
                )

                result = con.execute(
                    """
                    INSERT INTO metric_facts (
                        node_id,
                        node_type,
                        subject_id,
                        name,
                        value,
                        unit,
                        currency,
                        period_start,
                        period_end,
                        effective_at,
                        observed_at,
                        published_at,
                        ingested_at,
                        source_id,
                        source_version,
                        supersedes_id
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (node_id) DO NOTHING
                    """,
                    (
                        metric.id,
                        metric.node_type.value,
                        metric.subject_id,
                        metric.name,
                        metric.value,
                        metric.unit,
                        metric.currency,
                        metric.period_start,
                        metric.period_end,
                        metric.effective_at,
                        metric.observed_at,
                        metric.published_at,
                        metric.ingested_at,
                        metric.source_id,
                        metric.source_version,
                        metric.supersedes_id,
                    ),
                )

                if result.rowcount == 0:
                    self._assert_metric_projection_matches(
                        con,
                        metric,
                    )

    def add_security(
        self,
        security: Security,
    ) -> None:
        validate_node(security)
        payload, payload_hash = self._payload(security)

        with self.connect() as con:
            with con.transaction():
                self._insert_domain_node(
                    con,
                    node_id=security.id,
                    node_type=security.node_type.value,
                    payload=payload,
                    payload_hash=payload_hash,
                )

    def _load_exact_security(
        self,
        con,
        security_id: str,
    ) -> Security | None:
        row = con.execute(
            """
            SELECT
                canonical_payload,
                payload_hash
            FROM domain_nodes
            WHERE id = %s
              AND node_type = 'Security'
            """,
            (security_id,),
        ).fetchone()

        if row is None:
            return None

        stored_payload, stored_hash = row

        if canonical_sha256(stored_payload) != str(stored_hash):
            raise RepositoryReadError(
                "IDM-R505: STORED_NODE_INTEGRITY_FAILURE"
            )

        try:
            security = Security(
                id=str(stored_payload["id"]),
                company_id=str(stored_payload["company_id"]),
                venue=str(stored_payload["venue"]),
                ticker=str(stored_payload["ticker"]),
                currency=str(stored_payload["currency"]),
            )
            validate_node(security)
        except (KeyError, TypeError, ValueError) as exc:
            raise RepositoryReadError(
                "IDM-R553: INVALID_STORED_SECURITY_PAYLOAD"
            ) from exc

        self._assert_stored_node_integrity(
            node=security,
            stored_payload=stored_payload,
            stored_hash=str(stored_hash),
        )
        return security

    def security(
        self,
        security_id: str,
    ) -> Security | None:
        with self.connect() as con:
            return self._load_exact_security(
                con,
                security_id,
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
        source_id: str,
        relation: EdgeType,
        target_id: str,
        created_at,
        write_lost_code: str,
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
                source_id,
                relation.value,
                target_id,
            ),
        ).fetchone()

        if row is None:
            raise RepositoryWriteError(write_lost_code)

        if row[0] != created_at:
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
                        source_type,
                        edge_type,
                        target_id,
                        target_type,
                        created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (
                        link.claim_id,
                        NodeType.CLAIM.value,
                        link.relation.value,
                        link.evidence_id,
                        NodeType.EVIDENCE.value,
                        link.created_at,
                    ),
                )

                if result.rowcount == 0:
                    self._assert_existing_edge_matches(
                        con,
                        source_id=link.claim_id,
                        relation=link.relation,
                        target_id=link.evidence_id,
                        created_at=link.created_at,
                        write_lost_code=(
                            "IDM-W511: CLAIM_EVIDENCE_EDGE_WRITE_LOST"
                        ),
                    )

    def add_metric_evidence_link(
        self,
        link: MetricEvidenceLink,
    ) -> None:
        validate_metric_evidence_link(link)

        edge = Edge(
            source_id=link.metric_id,
            source_type=NodeType.METRIC,
            edge_type=link.relation,
            target_id=link.evidence_id,
            target_type=NodeType.EVIDENCE,
        )
        validate_edge(edge)

        with self.connect() as con:
            with con.transaction():
                self._assert_edge_endpoint(
                    con,
                    node_id=link.metric_id,
                    expected_type=NodeType.METRIC,
                    missing_code="IDM-W507: EDGE_SOURCE_NOT_FOUND",
                    mismatch_code="IDM-W509: EDGE_SOURCE_TYPE_MISMATCH",
                )

                self._assert_edge_endpoint(
                    con,
                    node_id=link.evidence_id,
                    expected_type=NodeType.EVIDENCE,
                    missing_code="IDM-W508: EDGE_TARGET_NOT_FOUND",
                    mismatch_code="IDM-W510: EDGE_TARGET_TYPE_MISMATCH",
                )

                result = con.execute(
                    """
                    INSERT INTO domain_edges (
                        source_id,
                        source_type,
                        edge_type,
                        target_id,
                        target_type,
                        created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (
                        link.metric_id,
                        NodeType.METRIC.value,
                        link.relation.value,
                        link.evidence_id,
                        NodeType.EVIDENCE.value,
                        link.created_at,
                    ),
                )

                if result.rowcount == 0:
                    self._assert_existing_edge_matches(
                        con,
                        source_id=link.metric_id,
                        relation=link.relation,
                        target_id=link.evidence_id,
                        created_at=link.created_at,
                        write_lost_code=(
                            "IDM-W518: METRIC_EVIDENCE_EDGE_WRITE_LOST"
                        ),
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

    @staticmethod
    def _parse_metric_payload_datetime(
        value,
        *,
        optional: bool = False,
    ):
        from datetime import datetime

        if value is None and optional:
            return None

        if isinstance(value, datetime):
            return value

        if not isinstance(value, str):
            raise RepositoryReadError(
                "IDM-R509: INVALID_STORED_METRIC_PAYLOAD"
            )

        try:
            return datetime.fromisoformat(
                value.replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise RepositoryReadError(
                "IDM-R509: INVALID_STORED_METRIC_PAYLOAD"
            ) from exc

    @staticmethod
    def _assert_stored_metric_integrity(
        *,
        metric: Metric,
        stored_payload,
        stored_hash,
        projection_row,
    ) -> None:
        try:
            actual_hash = canonical_sha256(stored_payload)
            reconstructed_payload = json.loads(
                canonical_json(metric)
            )
        except (TypeError, ValueError) as exc:
            raise RepositoryReadError(
                "IDM-R510: STORED_METRIC_INTEGRITY_FAILURE"
            ) from exc

        expected_projection = (
            metric.node_type.value,
            metric.subject_id,
            metric.name,
            metric.value,
            metric.unit,
            metric.currency,
            metric.period_start,
            metric.period_end,
            metric.effective_at,
            metric.observed_at,
            metric.published_at,
            metric.ingested_at,
            metric.source_id,
            metric.source_version,
            metric.supersedes_id,
        )

        if (
            actual_hash != str(stored_hash)
            or reconstructed_payload != stored_payload
            or tuple(projection_row) != expected_projection
        ):
            raise RepositoryReadError(
                "IDM-R510: STORED_METRIC_INTEGRITY_FAILURE"
            )


    def _load_exact_calculation(
        self,
        con,
        calculation_id: str,
    ) -> Calculation:
        row = con.execute(
            """
            SELECT
                n.node_type,
                n.canonical_payload,
                n.payload_hash,
                c.node_type,
                c.subject_id,
                c.formula,
                c.input_ids,
                c.value,
                c.unit,
                c.currency,
                c.model_version
            FROM domain_nodes AS n
            LEFT JOIN calculation_facts AS c
              ON c.node_id = n.id
            WHERE n.id = %s
            """,
            (calculation_id,),
        ).fetchone()

        if row is None:
            raise RepositoryReadError(
                "Calculation persistent state not found"
            )

        if str(row[0]) != NodeType.CALCULATION.value:
            raise RepositoryReadError(
                "Calculation persistent node type mismatch"
            )

        if row[3] is None:
            raise RepositoryReadError(
                "Calculation projection not found"
            )

        try:
            calculation = Calculation(
                id=calculation_id,
                subject_id=row[4],
                formula=row[5],
                input_ids=tuple(row[6]),
                value=row[7],
                unit=row[8],
                currency=row[9],
                model_version=row[10],
            )
            validate_node(calculation)
            validate_calculation(calculation)
        except (TypeError, ValueError) as exc:
            raise RepositoryReadError(
                "Invalid stored Calculation projection"
            ) from exc

        if str(row[3]) != NodeType.CALCULATION.value:
            raise RepositoryReadError(
                "Calculation projection type mismatch"
            )

        self._assert_stored_node_integrity(
            node=calculation,
            stored_payload=row[1],
            stored_hash=row[2],
        )

        expected_projection = (
            calculation.node_type.value,
            calculation.subject_id,
            calculation.formula,
            list(calculation.input_ids),
            calculation.value,
            calculation.unit,
            calculation.currency,
            calculation.model_version,
        )

        actual_projection = (
            str(row[3]),
            str(row[4]),
            str(row[5]),
            list(row[6]),
            row[7],
            str(row[8]),
            str(row[9]) if row[9] is not None else None,
            str(row[10]),
        )

        if actual_projection != expected_projection:
            raise RepositoryReadError(
                "Stored Calculation projection mismatch"
            )

        return calculation

    def _load_exact_metric(
        self,
        con,
        metric_id: str,
    ) -> Metric:
        row = con.execute(
            """
            SELECT
                m.node_id,
                m.node_type,
                m.subject_id,
                m.name,
                m.value,
                m.unit,
                m.currency,
                m.period_start,
                m.period_end,
                m.effective_at,
                m.observed_at,
                m.published_at,
                m.ingested_at,
                m.source_id,
                m.source_version,
                m.supersedes_id,
                n.node_type,
                n.canonical_payload,
                n.payload_hash
            FROM domain_nodes AS n
            LEFT JOIN metric_facts AS m
              ON m.node_id = n.id
            WHERE n.id = %s
            """,
            (metric_id,),
        ).fetchone()

        if row is None:
            raise RepositoryReadError(
                "Metric dependency persistent state not found"
            )

        if str(row[16]) != NodeType.METRIC.value:
            raise RepositoryReadError(
                "Metric dependency node type mismatch"
            )

        if row[0] is None:
            raise RepositoryReadError(
                "Metric dependency projection not found"
            )

        if str(row[1]) != NodeType.METRIC.value:
            raise RepositoryReadError(
                "Metric dependency projection type mismatch"
            )

        try:
            metric = Metric(
                id=str(row[0]),
                subject_id=row[2],
                name=row[3],
                value=row[4],
                unit=row[5],
                currency=row[6],
                period_start=row[7],
                period_end=row[8],
                effective_at=row[9],
                observed_at=row[10],
                published_at=row[11],
                ingested_at=row[12],
                source_id=row[13],
                source_version=row[14],
                supersedes_id=row[15],
            )
            validate_node(metric)
        except (TypeError, ValueError) as exc:
            raise RepositoryReadError(
                "Invalid stored Metric dependency"
            ) from exc

        self._assert_stored_metric_integrity(
            metric=metric,
            stored_payload=row[17],
            stored_hash=row[18],
            projection_row=row[1:16],
        )

        return metric

    def _calculation_visible_at(
        self,
        con,
        calculation_id: str,
        research_cutoff: datetime,
        visiting: set[str],
    ) -> tuple[Calculation, bool]:
        if calculation_id in visiting:
            raise RepositoryReadError(
                "Calculation dependency cycle detected"
            )

        calculation = self._load_exact_calculation(
            con,
            calculation_id,
        )

        edge_rows = con.execute(
            """
            SELECT
                target_id,
                target_type,
                created_at
            FROM domain_edges
            WHERE source_id = %s
              AND source_type = 'Calculation'
              AND edge_type = 'DERIVED_FROM'
            ORDER BY target_id
            """,
            (calculation_id,),
        ).fetchall()

        expected = set(calculation.input_ids)
        actual = {str(row[0]) for row in edge_rows}

        if (
            actual != expected
            or len(edge_rows) != len(expected)
        ):
            raise RepositoryReadError(
                "Calculation input provenance set mismatch"
            )

        edges_by_target = {
            str(row[0]): row
            for row in edge_rows
        }

        visiting.add(calculation_id)

        try:
            for input_id in calculation.input_ids:
                edge = edges_by_target[input_id]
                target_type = str(edge[1])

                if target_type == NodeType.METRIC.value:
                    # Persistent integrity is established before PIT
                    # visibility is evaluated.
                    metric = self._load_exact_metric(
                        con,
                        input_id,
                    )

                    if edge[2] > research_cutoff:
                        return calculation, False

                    if not available_at(
                        metric,
                        research_cutoff,
                    ):
                        return calculation, False

                elif target_type == NodeType.CALCULATION.value:
                    # Establish exact dependency aggregate integrity before
                    # PIT visibility. Its provenance closure, however, is
                    # traversed only when this parent edge is visible at the
                    # research cutoff.
                    self._load_exact_calculation(
                        con,
                        input_id,
                    )

                    if edge[2] > research_cutoff:
                        return calculation, False

                    _, child_visible = self._calculation_visible_at(
                        con,
                        input_id,
                        research_cutoff,
                        visiting,
                    )

                    if not child_visible:
                        return calculation, False

                else:
                    raise RepositoryReadError(
                        "Invalid Calculation dependency type"
                    )

            return calculation, True

        finally:
            visiting.remove(calculation_id)


    def _verify_calculation_reproducibility(
        self,
        con,
        calculation_id: str,
        visiting: set[str],
        memo: dict[
            str,
            tuple[
                Calculation,
                tuple[Decimal, str, str | None],
            ],
        ],
    ) -> tuple[
        Calculation,
        tuple[Decimal, str, str | None],
    ]:
        if calculation_id in memo:
            return memo[calculation_id]

        if calculation_id in visiting:
            raise RepositoryReadError(
                "Calculation dependency cycle"
            )

        visiting.add(calculation_id)

        try:
            calculation = self._load_exact_calculation(
                con,
                calculation_id,
            )

            edge_rows = con.execute(
                """
                SELECT
                    target_id,
                    target_type
                FROM domain_edges
                WHERE source_id = %s
                  AND source_type = 'Calculation'
                  AND edge_type = 'DERIVED_FROM'
                ORDER BY target_id
                """,
                (calculation_id,),
            ).fetchall()

            expected = set(calculation.input_ids)
            actual = {str(row[0]) for row in edge_rows}

            if (
                actual != expected
                or len(edge_rows) != len(expected)
            ):
                raise RepositoryReadError(
                    "Calculation input provenance set mismatch"
                )

            edges_by_target = {
                str(row[0]): row
                for row in edge_rows
            }

            inputs = []

            for input_id in calculation.input_ids:
                edge = edges_by_target[input_id]
                target_type = str(edge[1])

                if target_type == NodeType.METRIC.value:
                    metric = self._load_exact_metric(
                        con,
                        input_id,
                    )
                    inputs.append(
                        (
                            metric.value,
                            metric.unit,
                            metric.currency,
                        )
                    )

                elif target_type == NodeType.CALCULATION.value:
                    _, child_result = (
                        self._verify_calculation_reproducibility(
                            con,
                            input_id,
                            visiting,
                            memo,
                        )
                    )
                    inputs.append(child_result)

                else:
                    raise RepositoryReadError(
                        "Invalid Calculation dependency type"
                    )

            try:
                result = evaluate_calculation(
                    calculation,
                    inputs=tuple(inputs),
                    verify_materialized=True,
                )
            except (TypeError, ValueError) as exc:
                raise RepositoryReadError(
                    "Calculation materialization is not reproducible"
                ) from exc

            verified = (
                calculation,
                result,
            )
            memo[calculation_id] = verified
            return verified

        finally:
            visiting.remove(calculation_id)



    def persist_verified_valuation(
        self,
        valuation: Valuation,
        dependency_ids: tuple[str, ...],
    ) -> Valuation:
        validate_node(valuation)

        with self.connect() as con:
            with con.transaction():
                self._add_valuation_in_transaction(
                    con,
                    valuation,
                )
                self._add_valuation_dependencies_in_transaction(
                    con,
                    valuation.id,
                    dependency_ids,
                )
                verified = self._verify_valuation_in_connection(
                    con,
                    valuation.id,
                )

                if verified != valuation:
                    raise RepositoryWriteError(
                        "IDM-W547: VERIFIED_VALUATION_MISMATCH"
                    )

                return verified

    def verify_valuation(
        self,
        valuation_id: str,
    ) -> Valuation:
        if not is_canonical_content_id(
            valuation_id,
            kind="valuation",
        ):
            raise ValueError(
                "valuation_id must be a canonical Valuation ID"
            )

        with self.connect() as con:
            return self._verify_valuation_in_connection(
                con,
                valuation_id,
            )

    def _verify_valuation_in_connection(
        self,
        con,
        valuation_id: str,
    ) -> Valuation:
        if not is_canonical_content_id(
            valuation_id,
            kind="valuation",
        ):
            raise ValueError(
                "valuation_id must be a canonical Valuation ID"
            )

        valuation = self._load_exact_valuation(
            con,
            valuation_id,
        )

        security = self._load_exact_security(
            con,
            valuation.security_id,
        )
        if security is None:
            raise RepositoryReadError(
                "IDM-R555: VALUATION_SECURITY_NOT_FOUND"
            )

        rows = con.execute(
            """
            SELECT
                target_id,
                target_type
            FROM domain_edges
            WHERE source_id = %s
              AND source_type = 'Valuation'
              AND edge_type = 'DEPENDS_ON'
            ORDER BY target_id
            """,
            (valuation_id,),
        ).fetchall()

        dependencies = []
        calculation_memo = {}

        for target_id, target_type in rows:
            target_id = str(target_id)
            target_type = str(target_type)

            if target_type == NodeType.ESTIMATE.value:
                dependency = self._load_exact_estimate(
                    con,
                    target_id,
                )

            elif target_type == NodeType.FORECAST.value:
                dependency = self._load_exact_forecast(
                    con,
                    target_id,
                )

            elif target_type == NodeType.METRIC.value:
                try:
                    dependency = self._load_exact_metric(
                        con,
                        target_id,
                    )
                except RepositoryReadError as exc:
                    raise RepositoryReadError(
                        "IDM-R554: "
                        "VALUATION_DEPENDENCY_INTEGRITY_FAILURE"
                    ) from exc

            elif target_type == NodeType.CALCULATION.value:
                try:
                    dependency, _ = (
                        self._verify_calculation_reproducibility(
                            con,
                            target_id,
                            set(),
                            calculation_memo,
                        )
                    )
                except RepositoryReadError as exc:
                    raise RepositoryReadError(
                        "IDM-R554: "
                        "VALUATION_DEPENDENCY_INTEGRITY_FAILURE"
                    ) from exc

            elif target_type == NodeType.CATALYST_IMPACT.value:
                dependency = self._load_exact_catalyst_impact(
                    con,
                    target_id,
                )

            else:
                raise RepositoryReadError(
                    "IDM-R556: "
                    "VALUATION_DEPENDENCY_TYPE_UNSUPPORTED"
                )

            dependencies.append(dependency)

        try:
            evaluate_dcf_v1(
                tuple(dependencies),
                valuation=valuation,
                security=security,
                verify_materialized=True,
            )
        except (
            ValuationInputResolutionError,
            ValuationEvaluationError,
        ) as exc:
            raise RepositoryReadError(
                "IDM-R557: "
                "VALUATION_REPRODUCIBILITY_FAILURE"
            ) from exc

        return valuation

    def verify_calculation(
        self,
        calculation_id: str,
    ) -> Calculation:
        if not is_canonical_content_id(
            calculation_id,
            kind="calculation",
        ):
            raise ValueError(
                "calculation_id must be a canonical Calculation ID"
            )

        with self.connect() as con:
            calculation, _ = (
                self._verify_calculation_reproducibility(
                    con,
                    calculation_id,
                    set(),
                    {},
                )
            )
            return calculation

    def catalyst_impact_at(
        self,
        catalyst_impact_id: str,
        research_cutoff: datetime,
    ) -> CatalystImpact | None:
        if not is_canonical_content_id(
            catalyst_impact_id,
            kind="catalyst_impact",
        ):
            raise ValueError(
                "catalyst_impact_id must be a canonical CatalystImpact ID"
            )

        if (
            research_cutoff.tzinfo is None
            or research_cutoff.utcoffset() is None
        ):
            raise ValueError(
                "research_cutoff must be timezone-aware"
            )

        with self.connect() as con:
            anchor = con.execute(
                """
                SELECT
                    node_type,
                    canonical_payload,
                    payload_hash
                FROM domain_nodes
                WHERE id = %s
                """,
                (catalyst_impact_id,),
            ).fetchone()

            if anchor is None:
                return None

            if str(anchor[0]) != NodeType.CATALYST_IMPACT.value:
                raise RepositoryReadError(
                    "IDM-R540: CATALYST_IMPACT_TYPE_MISMATCH"
                )

            row = con.execute(
                """
                SELECT
                    node_type,
                    catalyst_id,
                    target_id,
                    direction,
                    magnitude,
                    probability,
                    confidence,
                    horizon,
                    rationale,
                    as_of,
                    created_by
                FROM catalyst_impact_facts
                WHERE node_id = %s
                """,
                (catalyst_impact_id,),
            ).fetchone()

            if row is None:
                raise RepositoryReadError(
                    "IDM-R543: "
                    "CATALYST_IMPACT_PROJECTION_NOT_FOUND"
                )

            if str(row[0]) != NodeType.CATALYST_IMPACT.value:
                raise RepositoryReadError(
                    "IDM-R540: CATALYST_IMPACT_TYPE_MISMATCH"
                )

            try:
                catalyst_impact = CatalystImpact(
                    id=catalyst_impact_id,
                    catalyst_id=str(row[1]),
                    target_id=str(row[2]),
                    direction=str(row[3]),
                    magnitude=str(row[4]),
                    probability=row[5],
                    confidence=row[6],
                    horizon=str(row[7]),
                    rationale=str(row[8]),
                    as_of=row[9],
                    created_by=str(row[10]),
                )
                validate_node(catalyst_impact)
            except (TypeError, ValueError) as exc:
                raise RepositoryReadError(
                    "IDM-R541: "
                    "INVALID_STORED_CATALYST_IMPACT"
                ) from exc

            payload, payload_hash = self._payload(
                catalyst_impact
            )

            if (
                anchor[1] != json.loads(payload)
                or str(anchor[2]) != payload_hash
            ):
                raise RepositoryReadError(
                    "IDM-R542: "
                    "CATALYST_IMPACT_INTEGRITY_FAILURE"
                )

            if catalyst_impact.as_of > research_cutoff:
                return None

            return catalyst_impact

    def latest_catalyst_impact_at(
        self,
        catalyst_id: str,
        target_id: str,
        research_cutoff: datetime,
    ) -> CatalystImpact | None:
        if not isinstance(catalyst_id, str) or not catalyst_id.strip():
            raise ValueError(
                "catalyst_id must not be empty"
            )

        if not isinstance(target_id, str) or not target_id.strip():
            raise ValueError(
                "target_id must not be empty"
            )

        if (
            research_cutoff.tzinfo is None
            or research_cutoff.utcoffset() is None
        ):
            raise ValueError(
                "research_cutoff must be timezone-aware"
            )

        # Pair-level PIT lookup is visibility-first. Future independent
        # assessments are outside the historical information set and must
        # not be integrity-traversed by this query.
        with self.connect() as con:
            rows = con.execute(
                """
                SELECT node_id
                FROM catalyst_impact_facts
                WHERE catalyst_id = %s
                  AND target_id = %s
                  AND as_of = (
                      SELECT MAX(as_of)
                      FROM catalyst_impact_facts
                      WHERE catalyst_id = %s
                        AND target_id = %s
                        AND as_of <= %s
                  )
                ORDER BY node_id
                """,
                (
                    catalyst_id,
                    target_id,
                    catalyst_id,
                    target_id,
                    research_cutoff,
                ),
            ).fetchall()

        if not rows:
            return None

        # Same pair + same latest visible as_of has no deterministic
        # semantic winner. Ordering above is only deterministic observation,
        # never a tie-break selection rule.
        if len(rows) != 1:
            raise RepositoryReadError(
                "IDM-R544: CATALYST_IMPACT_PIT_AMBIGUITY"
            )

        # The selected PIT-visible assessment still receives the complete
        # exact-ID integrity contract before it can be returned.
        return self.catalyst_impact_at(
            str(rows[0][0]),
            research_cutoff,
        )

    def calculation_at(
        self,
        calculation_id: str,
        research_cutoff: datetime,
    ) -> Calculation | None:
        if not is_canonical_content_id(
            calculation_id,
            kind="calculation",
        ):
            raise ValueError(
                "calculation_id must be a canonical Calculation ID"
            )

        if (
            research_cutoff.tzinfo is None
            or research_cutoff.utcoffset() is None
        ):
            raise ValueError(
                "research_cutoff must be timezone-aware"
            )

        with self.connect() as con:
            calculation, visible = self._calculation_visible_at(
                con,
                calculation_id,
                research_cutoff,
                set(),
            )

            if not visible:
                return None

            return calculation

    def active_metric_at(
        self,
        metric_id: str,
        research_cutoff: datetime,
    ) -> Metric | None:
        if (
            research_cutoff.tzinfo is None
            or research_cutoff.utcoffset() is None
        ):
            raise ValueError(
                "research_cutoff must be timezone-aware"
            )

        if (
            not isinstance(metric_id, str)
            or not metric_id.startswith("metric:")
            or metric_id == "metric:"
        ):
            raise ValueError(
                "metric_id must reference Metric"
            )

        with self.connect() as con:
            anchor_row = con.execute(
                """
                SELECT
                    node_type
                FROM domain_nodes
                WHERE id = %s
                """,
                (metric_id,),
            ).fetchone()

            if anchor_row is None:
                raise RepositoryReadError(
                    "IDM-R506: METRIC_NOT_FOUND"
                )

            if str(anchor_row[0]) != NodeType.METRIC.value:
                raise RepositoryReadError(
                    "IDM-R507: METRIC_TYPE_MISMATCH"
                )

            anchor_projection = con.execute(
                """
                SELECT node_id
                FROM metric_facts
                WHERE node_id = %s
                """,
                (metric_id,),
            ).fetchone()

            if anchor_projection is None:
                raise RepositoryReadError(
                    "IDM-R508: METRIC_PROJECTION_NOT_FOUND"
                )

            rows = con.execute(
                """
                WITH RECURSIVE
                ancestors(node_id, supersedes_id) AS (
                    SELECT
                        m.node_id,
                        m.supersedes_id
                    FROM metric_facts AS m
                    WHERE m.node_id = %s

                    UNION

                    SELECT
                        parent.node_id,
                        parent.supersedes_id
                    FROM metric_facts AS parent
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
                    FROM metric_facts AS child
                    JOIN descendants AS parent
                      ON child.supersedes_id = parent.node_id
                )
                SELECT
                    m.node_id,
                    m.node_type,
                    m.subject_id,
                    m.name,
                    m.value,
                    m.unit,
                    m.currency,
                    m.period_start,
                    m.period_end,
                    m.effective_at,
                    m.observed_at,
                    m.published_at,
                    m.ingested_at,
                    m.source_id,
                    m.source_version,
                    m.supersedes_id,
                    n.node_type,
                    n.canonical_payload,
                    n.payload_hash
                FROM descendants AS d
                JOIN metric_facts AS m
                  ON m.node_id = d.node_id
                LEFT JOIN domain_nodes AS n
                  ON n.id = m.node_id
                ORDER BY m.node_id
                """,
                (metric_id,),
            ).fetchall()

            if not rows:
                raise RepositoryReadError(
                    "IDM-R510: STORED_METRIC_INTEGRITY_FAILURE"
                )

            metrics = []

            for row in rows:
                if row[16] is None:
                    raise RepositoryReadError(
                        "IDM-R510: STORED_METRIC_INTEGRITY_FAILURE"
                    )

                if str(row[16]) != NodeType.METRIC.value:
                    raise RepositoryReadError(
                        "IDM-R510: STORED_METRIC_INTEGRITY_FAILURE"
                    )

                payload = row[17]

                try:
                    if not isinstance(payload, dict):
                        raise TypeError(
                            "canonical Metric payload must be an object"
                        )

                    metric = Metric(
                        id=str(row[0]),
                        subject_id=payload["subject_id"],
                        name=payload["name"],
                        value=Decimal(str(payload["value"])),
                        unit=payload["unit"],
                        currency=payload.get("currency"),
                        period_start=self._parse_metric_payload_datetime(
                            payload.get("period_start"),
                            optional=True,
                        ),
                        period_end=self._parse_metric_payload_datetime(
                            payload["period_end"]
                        ),
                        effective_at=self._parse_metric_payload_datetime(
                            payload["effective_at"]
                        ),
                        observed_at=self._parse_metric_payload_datetime(
                            payload["observed_at"]
                        ),
                        published_at=self._parse_metric_payload_datetime(
                            payload["published_at"]
                        ),
                        ingested_at=self._parse_metric_payload_datetime(
                            payload["ingested_at"]
                        ),
                        source_id=payload["source_id"],
                        source_version=payload["source_version"],
                        supersedes_id=payload.get("supersedes_id"),
                    )
                    validate_node(metric)
                except RepositoryReadError:
                    raise
                except (
                    KeyError,
                    TypeError,
                    ValueError,
                    ArithmeticError,
                ) as exc:
                    raise RepositoryReadError(
                        "IDM-R509: INVALID_STORED_METRIC_PAYLOAD"
                    ) from exc

                projection_row = (
                    row[1],
                    row[2],
                    row[3],
                    row[4],
                    row[5],
                    row[6],
                    row[7],
                    row[8],
                    row[9],
                    row[10],
                    row[11],
                    row[12],
                    row[13],
                    row[14],
                    row[15],
                )

                self._assert_stored_metric_integrity(
                    metric=metric,
                    stored_payload=payload,
                    stored_hash=row[18],
                    projection_row=projection_row,
                )

                metrics.append(metric)

            try:
                return active_revision_at(
                    metrics,
                    research_cutoff,
                )
            except ValueError as exc:
                raise RepositoryReadError(
                    "IDM-R510: STORED_METRIC_INTEGRITY_FAILURE"
                ) from exc

    def evidence_for_metric_at(
        self,
        metric_id: str,
        research_cutoff: datetime,
    ) -> tuple[Evidence, ...]:
        if (
            research_cutoff.tzinfo is None
            or research_cutoff.utcoffset() is None
        ):
            raise ValueError(
                "research_cutoff must be timezone-aware"
            )

        if (
            not isinstance(metric_id, str)
            or not metric_id.startswith("metric:")
            or metric_id == "metric:"
        ):
            raise ValueError(
                "metric_id must reference Metric"
            )

        with self.connect() as con:
            metric_row = con.execute(
                """
                SELECT
                    node_type,
                    canonical_payload,
                    payload_hash
                FROM domain_nodes
                WHERE id = %s
                """,
                (metric_id,),
            ).fetchone()

            if metric_row is None:
                raise RepositoryReadError(
                    "IDM-R506: METRIC_NOT_FOUND"
                )

            if str(metric_row[0]) != NodeType.METRIC.value:
                raise RepositoryReadError(
                    "IDM-R507: METRIC_TYPE_MISMATCH"
                )

            projection_row = con.execute(
                """
                SELECT
                    node_type,
                    subject_id,
                    name,
                    value,
                    unit,
                    currency,
                    period_start,
                    period_end,
                    effective_at,
                    observed_at,
                    published_at,
                    ingested_at,
                    source_id,
                    source_version,
                    supersedes_id
                FROM metric_facts
                WHERE node_id = %s
                """,
                (metric_id,),
            ).fetchone()

            if projection_row is None:
                raise RepositoryReadError(
                    "IDM-R508: METRIC_PROJECTION_NOT_FOUND"
                )

            metric_payload = metric_row[1]

            try:
                if not isinstance(metric_payload, dict):
                    raise TypeError(
                        "canonical Metric payload must be an object"
                    )

                metric = Metric(
                    id=metric_id,
                    subject_id=metric_payload["subject_id"],
                    name=metric_payload["name"],
                    value=Decimal(str(metric_payload["value"])),
                    unit=metric_payload["unit"],
                    currency=metric_payload.get("currency"),
                    period_start=self._parse_metric_payload_datetime(
                        metric_payload.get("period_start"),
                        optional=True,
                    ),
                    period_end=self._parse_metric_payload_datetime(
                        metric_payload["period_end"]
                    ),
                    effective_at=self._parse_metric_payload_datetime(
                        metric_payload["effective_at"]
                    ),
                    observed_at=self._parse_metric_payload_datetime(
                        metric_payload["observed_at"]
                    ),
                    published_at=self._parse_metric_payload_datetime(
                        metric_payload["published_at"]
                    ),
                    ingested_at=self._parse_metric_payload_datetime(
                        metric_payload["ingested_at"]
                    ),
                    source_id=metric_payload["source_id"],
                    source_version=metric_payload["source_version"],
                    supersedes_id=metric_payload.get(
                        "supersedes_id"
                    ),
                )
                validate_node(metric)
            except RepositoryReadError:
                raise
            except (
                KeyError,
                TypeError,
                ValueError,
                ArithmeticError,
            ) as exc:
                raise RepositoryReadError(
                    "IDM-R509: INVALID_STORED_METRIC_PAYLOAD"
                ) from exc

            self._assert_stored_metric_integrity(
                metric=metric,
                stored_payload=metric_payload,
                stored_hash=metric_row[2],
                projection_row=projection_row,
            )

            link_rows = con.execute(
                """
                SELECT
                    target_id,
                    edge_type,
                    created_at
                FROM domain_edges
                WHERE source_id = %s
                  AND source_type = 'Metric'
                  AND edge_type = 'SUPPORTED_BY'
                  AND target_type = 'Evidence'
                ORDER BY target_id, edge_type
                """,
                (metric_id,),
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

            try:
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
            except (TypeError, ValueError, IndexError) as exc:
                raise RepositoryReadError(
                    "IDM-R505: STORED_NODE_INTEGRITY_FAILURE"
                ) from exc

            for node, row in zip(evidence, evidence_rows):
                try:
                    validate_node(node)
                except ValueError as exc:
                    raise RepositoryReadError(
                        "IDM-R505: STORED_NODE_INTEGRITY_FAILURE"
                    ) from exc

                if str(row[10]) != NodeType.EVIDENCE.value:
                    raise RepositoryReadError(
                        "IDM-R505: STORED_NODE_INTEGRITY_FAILURE"
                    )

                self._assert_stored_node_integrity(
                    node=node,
                    stored_payload=row[11],
                    stored_hash=row[12],
                )

            evidence_ids = {
                node.id
                for node in evidence
            }

            if not linked_ids.issubset(evidence_ids):
                raise RepositoryReadError(
                    "IDM-R503: LINKED_EVIDENCE_NOT_FOUND"
                )

            links = tuple(
                MetricEvidenceLink(
                    metric_id=metric_id,
                    evidence_id=str(row[0]),
                    relation=EdgeType(str(row[1])),
                    created_at=row[2],
                )
                for row in link_rows
            )

            for link in links:
                validate_metric_evidence_link(link)

            return eligible_metric_evidence(
                metric,
                evidence,
                links,
                research_cutoff,
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
