from __future__ import annotations

from dataclasses import dataclass
import hashlib


MIGRATION_HISTORY_TABLE = "investment_domain_schema_migrations"
MIGRATION_LOCK_NAMESPACE = "investment_domain_schema_migrations:v1"


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]

    @property
    def checksum(self) -> str:
        raw = (
            str(self.version)
            + "\n"
            + self.name
            + "\n"
            + "\n".join(self.statements)
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()


def validate_migrations(
    migrations: tuple[Migration, ...],
) -> None:
    versions = [migration.version for migration in migrations]

    if versions != sorted(versions):
        raise ValueError("IDM-M400: MIGRATIONS_NOT_ORDERED")

    if len(versions) != len(set(versions)):
        raise ValueError("IDM-M401: DUPLICATE_MIGRATION_VERSION")

    if versions and versions != list(range(1, versions[-1] + 1)):
        raise ValueError("IDM-M402: MIGRATION_VERSION_GAP")


INITIAL_SCHEMA = Migration(
    version=1,
    name="initial_evidence_store",
    statements=(
        """
CREATE TABLE domain_nodes (
    id TEXT PRIMARY KEY,
    node_type TEXT NOT NULL,
    canonical_payload JSONB NOT NULL,
    payload_hash TEXT NOT NULL,
    stored_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT domain_nodes_node_type
        CHECK (
            node_type IN (
                'Company',
                'Security',
                'Metric',
                'Claim',
                'Evidence',
                'Calculation',
                'Estimate',
                'Forecast',
                'Catalyst',
                'Risk',
                'Valuation',
                'Recommendation'
            )
        ),

    CONSTRAINT domain_nodes_payload_hash_sha256
        CHECK (payload_hash ~ '^[0-9a-f]{64}$'),

    CONSTRAINT domain_nodes_id_node_type_unique
        UNIQUE (id, node_type)
)
""".strip(),
        """
CREATE TABLE evidence_facts (
    node_id TEXT PRIMARY KEY,
    node_type TEXT NOT NULL DEFAULT 'Evidence',

    source_id TEXT NOT NULL,
    source_version TEXT NOT NULL,
    content_hash TEXT NOT NULL,

    effective_at TIMESTAMPTZ NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    published_at TIMESTAMPTZ NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL,

    supersedes_id TEXT NULL
        REFERENCES evidence_facts(node_id)
        ON DELETE RESTRICT,

    source_uri TEXT NULL,

    CONSTRAINT evidence_node_type
        CHECK (node_type = 'Evidence'),

    CONSTRAINT evidence_domain_node_fk
        FOREIGN KEY (node_id, node_type)
        REFERENCES domain_nodes(id, node_type)
        ON DELETE RESTRICT,

    CONSTRAINT evidence_content_hash_sha256
        CHECK (content_hash ~ '^[0-9a-f]{64}$'),

    CONSTRAINT evidence_publication_order
        CHECK (observed_at <= published_at),

    CONSTRAINT evidence_ingestion_order
        CHECK (published_at <= ingested_at),

    CONSTRAINT evidence_no_self_supersession
        CHECK (
            supersedes_id IS NULL
            OR supersedes_id <> node_id
        )
)
""".strip(),
        """
CREATE TABLE claim_facts (
    node_id TEXT PRIMARY KEY,
    node_type TEXT NOT NULL DEFAULT 'Claim',

    subject_id TEXT NOT NULL,
    predicate TEXT NOT NULL,
    as_of TIMESTAMPTZ NOT NULL,
    polarity TEXT NOT NULL,
    scope TEXT NOT NULL,

    CONSTRAINT claim_node_type
        CHECK (node_type = 'Claim'),

    CONSTRAINT claim_domain_node_fk
        FOREIGN KEY (node_id, node_type)
        REFERENCES domain_nodes(id, node_type)
        ON DELETE RESTRICT
)
""".strip(),
        """
CREATE TABLE domain_edges (
    source_id TEXT NOT NULL
        REFERENCES domain_nodes(id)
        ON DELETE RESTRICT,

    edge_type TEXT NOT NULL,

    target_id TEXT NOT NULL
        REFERENCES domain_nodes(id)
        ON DELETE RESTRICT,

    stored_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (source_id, edge_type, target_id),

    CONSTRAINT domain_edges_no_self_edge
        CHECK (source_id <> target_id),

    CONSTRAINT domain_edges_type
        CHECK (
            edge_type IN (
                'SUPPORTED_BY',
                'CONTRADICTED_BY'
            )
        )
)
""".strip(),
        """
CREATE INDEX idx_evidence_visibility
    ON evidence_facts (published_at, ingested_at, node_id)
""".strip(),
        """
CREATE UNIQUE INDEX idx_evidence_supersedes
    ON evidence_facts (supersedes_id)
    WHERE supersedes_id IS NOT NULL
""".strip(),
        """
CREATE INDEX idx_domain_edges_target
    ON domain_edges (target_id, edge_type, source_id)
""".strip(),
    ),
)


EDGE_CREATED_AT = Migration(
    version=2,
    name="add_edge_created_at",
    statements=(
        """
ALTER TABLE domain_edges
    ADD COLUMN created_at TIMESTAMPTZ NULL
""".strip(),
        """
UPDATE domain_edges
SET created_at = stored_at
WHERE created_at IS NULL
""".strip(),
        """
ALTER TABLE domain_edges
    ALTER COLUMN created_at SET NOT NULL
""".strip(),
    ),
)


EDGE_ENDPOINT_TYPES = Migration(
    version=3,
    name="add_edge_endpoint_types",
    statements=(
        """
ALTER TABLE domain_edges
    ADD COLUMN source_type TEXT NULL,
    ADD COLUMN target_type TEXT NULL
""".strip(),
        """
UPDATE domain_edges
SET source_type = 'Claim',
    target_type = 'Evidence'
WHERE source_type IS NULL
   OR target_type IS NULL
""".strip(),
        """
ALTER TABLE domain_edges
    ALTER COLUMN source_type SET NOT NULL,
    ALTER COLUMN target_type SET NOT NULL
""".strip(),
        """
ALTER TABLE domain_edges
    ADD CONSTRAINT domain_edges_source_type
        CHECK (source_type = 'Claim'),
    ADD CONSTRAINT domain_edges_target_type
        CHECK (target_type = 'Evidence')
""".strip(),
        """
ALTER TABLE domain_edges
    ADD CONSTRAINT domain_edges_source_typed_fk
        FOREIGN KEY (source_id, source_type)
        REFERENCES domain_nodes(id, node_type)
        ON DELETE RESTRICT,
    ADD CONSTRAINT domain_edges_target_typed_fk
        FOREIGN KEY (target_id, target_type)
        REFERENCES domain_nodes(id, node_type)
        ON DELETE RESTRICT
""".strip(),
    ),
)


METRIC_FACTS = Migration(
    version=4,
    name="add_metric_facts",
    statements=(
        """
CREATE TABLE metric_facts (
    node_id TEXT PRIMARY KEY,
    node_type TEXT NOT NULL DEFAULT 'Metric',
    subject_id TEXT NOT NULL,
    name TEXT NOT NULL,
    value NUMERIC NOT NULL,
    unit TEXT NOT NULL,
    currency TEXT NULL,
    period_start TIMESTAMPTZ NULL,
    period_end TIMESTAMPTZ NOT NULL,
    effective_at TIMESTAMPTZ NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    published_at TIMESTAMPTZ NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL,
    source_id TEXT NOT NULL,
    source_version TEXT NOT NULL,
    supersedes_id TEXT NULL,
    CONSTRAINT metric_node_type
        CHECK (node_type = 'Metric'),
    CONSTRAINT metric_domain_node_fk
        FOREIGN KEY (node_id, node_type)
        REFERENCES domain_nodes(id, node_type)
        ON DELETE RESTRICT
)
""".strip(),
    ),
)


ADD_METRIC_EVIDENCE_EDGES = Migration(
    version=5,
    name="add_metric_evidence_edges",
    statements=(
        """
ALTER TABLE domain_edges
    DROP CONSTRAINT domain_edges_source_type
""".strip(),
        """
ALTER TABLE domain_edges
    ADD CONSTRAINT domain_edges_source_relation_target_type
        CHECK (
            target_type = 'Evidence'
            AND (
                (
                    source_type = 'Claim'
                    AND edge_type IN (
                        'SUPPORTED_BY',
                        'CONTRADICTED_BY'
                    )
                )
                OR
                (
                    source_type = 'Metric'
                    AND edge_type = 'SUPPORTED_BY'
                )
            )
        )
""".strip(),
    ),
)


METRIC_REVISION_CONSTRAINTS = Migration(
    version=6,
    name="add_metric_revision_constraints",
    statements=(
        """
ALTER TABLE metric_facts
    ADD CONSTRAINT metric_supersedes_fk
        FOREIGN KEY (supersedes_id)
        REFERENCES metric_facts(node_id)
        ON DELETE RESTRICT
""".strip(),
        """
ALTER TABLE metric_facts
    ADD CONSTRAINT metric_no_self_supersession
        CHECK (
            supersedes_id IS NULL
            OR supersedes_id <> node_id
        )
""".strip(),
        """
CREATE UNIQUE INDEX metric_one_successor_per_predecessor
    ON metric_facts (supersedes_id)
    WHERE supersedes_id IS NOT NULL
""".strip(),
    ),
)


CALCULATION_FACTS = Migration(
    version=7,
    name="add_calculation_facts",
    statements=(
        """
CREATE TABLE calculation_facts (
    node_id TEXT PRIMARY KEY,
    node_type TEXT NOT NULL DEFAULT 'Calculation',
    subject_id TEXT NOT NULL,
    formula TEXT NOT NULL,
    input_ids TEXT[] NOT NULL,
    value NUMERIC NOT NULL,
    unit TEXT NOT NULL,
    currency TEXT NULL,
    model_version TEXT NOT NULL,
    CONSTRAINT calculation_node_type
        CHECK (node_type = 'Calculation'),
    CONSTRAINT calculation_domain_node_fk
        FOREIGN KEY (node_id, node_type)
        REFERENCES domain_nodes(id, node_type)
        ON DELETE RESTRICT
)
""".strip(),
    ),
)



CALCULATION_INPUT_PROVENANCE = Migration(
    version=8,
    name="add_calculation_input_provenance",
    statements=(
        """
ALTER TABLE domain_edges
    DROP CONSTRAINT domain_edges_type
""".strip(),
        """
ALTER TABLE domain_edges
    ADD CONSTRAINT domain_edges_type
        CHECK (
            edge_type IN (
                'SUPPORTED_BY',
                'CONTRADICTED_BY',
                'DERIVED_FROM'
            )
        )
""".strip(),
        """
ALTER TABLE domain_edges
    DROP CONSTRAINT domain_edges_target_type
""".strip(),
        """
ALTER TABLE domain_edges
    ADD CONSTRAINT domain_edges_target_type
        CHECK (
            target_type IN (
                'Evidence',
                'Metric',
                'Calculation'
            )
        )
""".strip(),
        """
ALTER TABLE domain_edges
    DROP CONSTRAINT domain_edges_source_relation_target_type
""".strip(),
        """
ALTER TABLE domain_edges
    ADD CONSTRAINT domain_edges_source_relation_target_type
        CHECK (
            (
                source_type = 'Claim'
                AND edge_type IN ('SUPPORTED_BY', 'CONTRADICTED_BY')
                AND target_type = 'Evidence'
            )
            OR
            (
                source_type = 'Metric'
                AND edge_type = 'SUPPORTED_BY'
                AND target_type = 'Evidence'
            )
            OR
            (
                source_type = 'Calculation'
                AND edge_type = 'DERIVED_FROM'
                AND target_type IN ('Metric', 'Calculation')
            )
        )
""".strip(),
    ),
)


ESTIMATE_FACTS = Migration(
    version=9,
    name="add_estimate_facts",
    statements=(
        """
CREATE TABLE estimate_facts (
    node_id TEXT PRIMARY KEY,
    node_type TEXT NOT NULL DEFAULT 'Estimate',
    subject_id TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    period_end TIMESTAMPTZ NOT NULL,
    value NUMERIC NOT NULL,
    unit TEXT NOT NULL,
    scenario TEXT NOT NULL,
    model_version TEXT NOT NULL,
    as_of TIMESTAMPTZ NOT NULL,
    currency TEXT NULL,
    CONSTRAINT estimate_node_type
        CHECK (node_type = 'Estimate'),
    CONSTRAINT estimate_domain_node_fk
        FOREIGN KEY (node_id, node_type)
        REFERENCES domain_nodes(id, node_type)
        ON DELETE RESTRICT
)
""".strip(),
    ),
)


ESTIMATE_INPUT_PROVENANCE = Migration(
    version=10,
    name="add_estimate_input_provenance",
    statements=(
        """
ALTER TABLE domain_edges
    DROP CONSTRAINT domain_edges_target_type
""".strip(),
        """
ALTER TABLE domain_edges
    ADD CONSTRAINT domain_edges_target_type
        CHECK (
            target_type IN (
                'Evidence',
                'Metric',
                'Calculation',
                'Claim'
            )
        )
""".strip(),
        """
ALTER TABLE domain_edges
    DROP CONSTRAINT domain_edges_source_relation_target_type
""".strip(),
        """
ALTER TABLE domain_edges
    ADD CONSTRAINT domain_edges_source_relation_target_type
        CHECK (
            (
                source_type = 'Claim'
                AND edge_type IN ('SUPPORTED_BY', 'CONTRADICTED_BY')
                AND target_type = 'Evidence'
            )
            OR
            (
                source_type = 'Metric'
                AND edge_type = 'SUPPORTED_BY'
                AND target_type = 'Evidence'
            )
            OR
            (
                source_type = 'Calculation'
                AND edge_type = 'DERIVED_FROM'
                AND target_type IN ('Metric', 'Calculation')
            )
            OR
            (
                source_type = 'Estimate'
                AND edge_type = 'DERIVED_FROM'
                AND target_type IN ('Metric', 'Calculation', 'Claim')
            )
        )
""".strip(),
    ),
)


VALUATION_FACTS = Migration(
    version=11,
    name="add_valuation_facts",
    statements=(
        """
CREATE TABLE valuation_facts (
    node_id TEXT PRIMARY KEY,
    node_type TEXT NOT NULL DEFAULT 'Valuation',
    security_id TEXT NOT NULL,
    method TEXT NOT NULL,
    value NUMERIC NOT NULL,
    currency TEXT NOT NULL,
    as_of TIMESTAMPTZ NOT NULL,
    model_version TEXT NOT NULL,
    scenario TEXT NOT NULL,
    CONSTRAINT valuation_node_type
        CHECK (node_type = 'Valuation'),
    CONSTRAINT valuation_domain_node_fk
        FOREIGN KEY (node_id, node_type)
        REFERENCES domain_nodes(id, node_type)
        ON DELETE RESTRICT
)
""".strip(),
    ),
)


VALUATION_DEPENDENCIES = Migration(
    version=12,
    name="add_valuation_dependencies",
    statements=(
        """
ALTER TABLE domain_edges
    DROP CONSTRAINT domain_edges_type
""".strip(),
        """
ALTER TABLE domain_edges
    ADD CONSTRAINT domain_edges_type
        CHECK (
            edge_type IN (
                'SUPPORTED_BY',
                'CONTRADICTED_BY',
                'DERIVED_FROM',
                'DEPENDS_ON'
            )
        )
""".strip(),
        """
ALTER TABLE domain_edges
    DROP CONSTRAINT domain_edges_target_type
""".strip(),
        """
ALTER TABLE domain_edges
    ADD CONSTRAINT domain_edges_target_type
        CHECK (
            target_type IN (
                'Evidence',
                'Metric',
                'Calculation',
                'Claim',
                'Forecast',
                'Estimate'
            )
        )
""".strip(),
        """
ALTER TABLE domain_edges
    DROP CONSTRAINT domain_edges_source_relation_target_type
""".strip(),
        """
ALTER TABLE domain_edges
    ADD CONSTRAINT domain_edges_source_relation_target_type
        CHECK (
            (
                source_type = 'Claim'
                AND edge_type IN ('SUPPORTED_BY', 'CONTRADICTED_BY')
                AND target_type = 'Evidence'
            )
            OR
            (
                source_type = 'Metric'
                AND edge_type = 'SUPPORTED_BY'
                AND target_type = 'Evidence'
            )
            OR
            (
                source_type = 'Calculation'
                AND edge_type = 'DERIVED_FROM'
                AND target_type IN ('Metric', 'Calculation')
            )
            OR
            (
                source_type = 'Estimate'
                AND edge_type = 'DERIVED_FROM'
                AND target_type IN ('Metric', 'Calculation', 'Claim')
            )
            OR
            (
                source_type = 'Valuation'
                AND edge_type = 'DEPENDS_ON'
                AND target_type IN (
                    'Forecast',
                    'Estimate',
                    'Metric',
                    'Calculation'
                )
            )
        )
""".strip(),
    ),
)



FORECAST_FACTS = Migration(
    version=13,
    name="add_forecast_facts",
    statements=(
        """
CREATE TABLE forecast_facts (
    node_id TEXT PRIMARY KEY,
    node_type TEXT NOT NULL DEFAULT 'Forecast',
    subject_id TEXT NOT NULL,
    scenario TEXT NOT NULL,
    as_of TIMESTAMPTZ NOT NULL,
    model_version TEXT NOT NULL,
    CONSTRAINT forecast_node_type
        CHECK (node_type = 'Forecast'),
    CONSTRAINT forecast_domain_node_fk
        FOREIGN KEY (node_id, node_type)
        REFERENCES domain_nodes(id, node_type)
        ON DELETE RESTRICT
)
""".strip(),
    ),
)


ADD_FORECAST_COMPOSITION = Migration(
    version=14,
    name="add_forecast_composition",
    statements=(
        """
ALTER TABLE domain_edges
    DROP CONSTRAINT domain_edges_type
""".strip(),
        """
ALTER TABLE domain_edges
    ADD CONSTRAINT domain_edges_type
        CHECK (
            edge_type IN (
                'SUPPORTED_BY',
                'CONTRADICTED_BY',
                'DERIVED_FROM',
                'DEPENDS_ON',
                'CONTAINS'
            )
        )
""".strip(),
        """
ALTER TABLE domain_edges
    DROP CONSTRAINT domain_edges_target_type
""".strip(),
        """
ALTER TABLE domain_edges
    ADD CONSTRAINT domain_edges_target_type
        CHECK (
            target_type IN (
                'Evidence',
                'Metric',
                'Calculation',
                'Claim',
                'Forecast',
                'Estimate'
            )
        )
""".strip(),
        """
ALTER TABLE domain_edges
    DROP CONSTRAINT domain_edges_source_relation_target_type
""".strip(),
        """
ALTER TABLE domain_edges
    ADD CONSTRAINT domain_edges_source_relation_target_type
        CHECK (
            (
                source_type = 'Claim'
                AND edge_type IN ('SUPPORTED_BY', 'CONTRADICTED_BY')
                AND target_type = 'Evidence'
            )
            OR
            (
                source_type = 'Metric'
                AND edge_type = 'SUPPORTED_BY'
                AND target_type = 'Evidence'
            )
            OR
            (
                source_type = 'Calculation'
                AND edge_type = 'DERIVED_FROM'
                AND target_type IN ('Metric', 'Calculation')
            )
            OR
            (
                source_type = 'Estimate'
                AND edge_type = 'DERIVED_FROM'
                AND target_type IN ('Metric', 'Calculation', 'Claim')
            )
            OR
            (
                source_type = 'Valuation'
                AND edge_type = 'DEPENDS_ON'
                AND target_type IN (
                    'Forecast',
                    'Estimate',
                    'Metric',
                    'Calculation'
                )
            )
            OR
            (
                source_type = 'Forecast'
                AND edge_type = 'CONTAINS'
                AND target_type = 'Estimate'
            )
        )
""".strip(),
    ),
)


CATALYST_FACTS = Migration(
    version=15,
    name="add_catalyst_facts",
    statements=(
        """
CREATE TABLE catalyst_facts (
    node_id TEXT PRIMARY KEY,
    node_type TEXT NOT NULL DEFAULT 'Catalyst',
    subject_id TEXT NOT NULL,
    description TEXT NOT NULL,
    as_of TIMESTAMPTZ NOT NULL,
    expected_at TIMESTAMPTZ,
    CONSTRAINT catalyst_node_type
        CHECK (node_type = 'Catalyst'),
    CONSTRAINT catalyst_domain_node_fk
        FOREIGN KEY (node_id, node_type)
        REFERENCES domain_nodes(id, node_type)
        ON DELETE RESTRICT
)
""".strip(),
    ),
)


CATALYST_AFFECTS_EDGES = Migration(
    version=16,
    name="add_catalyst_affects_edges",
    statements=(
        """
ALTER TABLE domain_edges
    DROP CONSTRAINT domain_edges_type
""".strip(),
        """
ALTER TABLE domain_edges
    ADD CONSTRAINT domain_edges_type
        CHECK (
            edge_type IN (
                'SUPPORTED_BY',
                'CONTRADICTED_BY',
                'DERIVED_FROM',
                'DEPENDS_ON',
                'CONTAINS',
                'AFFECTS'
            )
        )
""".strip(),
        """
ALTER TABLE domain_edges
    DROP CONSTRAINT domain_edges_source_relation_target_type
""".strip(),
        """
ALTER TABLE domain_edges
    ADD CONSTRAINT domain_edges_source_relation_target_type
        CHECK (
            (
                source_type = 'Claim'
                AND edge_type IN ('SUPPORTED_BY', 'CONTRADICTED_BY')
                AND target_type = 'Evidence'
            )
            OR
            (
                source_type = 'Metric'
                AND edge_type = 'SUPPORTED_BY'
                AND target_type = 'Evidence'
            )
            OR
            (
                source_type = 'Calculation'
                AND edge_type = 'DERIVED_FROM'
                AND target_type IN ('Metric', 'Calculation')
            )
            OR
            (
                source_type = 'Estimate'
                AND edge_type = 'DERIVED_FROM'
                AND target_type IN ('Metric', 'Calculation', 'Claim')
            )
            OR
            (
                source_type = 'Valuation'
                AND edge_type = 'DEPENDS_ON'
                AND target_type IN (
                    'Forecast',
                    'Estimate',
                    'Metric',
                    'Calculation'
                )
            )
            OR
            (
                source_type = 'Forecast'
                AND edge_type = 'CONTAINS'
                AND target_type = 'Estimate'
            )
            OR
            (
                source_type = 'Catalyst'
                AND edge_type = 'AFFECTS'
                AND target_type IN ('Claim', 'Forecast')
            )
        )
""".strip(),
    ),
)


CATALYST_IMPACT_FACTS = Migration(
    version=17,
    name="add_catalyst_impact_facts",
    statements=(
        """
ALTER TABLE domain_nodes
    DROP CONSTRAINT domain_nodes_node_type
""".strip(),
        """
ALTER TABLE domain_nodes
    ADD CONSTRAINT domain_nodes_node_type
        CHECK (
            node_type IN (
                'Company',
                'Security',
                'Metric',
                'Claim',
                'Evidence',
                'Calculation',
                'Estimate',
                'Forecast',
                'Catalyst',
                'CatalystImpact',
                'Risk',
                'Valuation',
                'Recommendation'
            )
        )
""".strip(),
        """
CREATE TABLE catalyst_impact_facts (
    node_id TEXT PRIMARY KEY,
    node_type TEXT NOT NULL DEFAULT 'CatalystImpact',
    catalyst_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    direction TEXT NOT NULL,
    magnitude TEXT NOT NULL,
    probability NUMERIC NOT NULL,
    confidence NUMERIC NOT NULL,
    horizon TEXT NOT NULL,
    rationale TEXT NOT NULL,
    as_of TIMESTAMPTZ NOT NULL,
    created_by TEXT NOT NULL,

    CONSTRAINT catalyst_impact_node_type
        CHECK (node_type = 'CatalystImpact'),

    CONSTRAINT catalyst_impact_domain_node_fk
        FOREIGN KEY (node_id, node_type)
        REFERENCES domain_nodes(id, node_type)
        ON DELETE RESTRICT,

    CONSTRAINT catalyst_impact_direction
        CHECK (
            direction IN ('POSITIVE', 'NEGATIVE', 'NEUTRAL')
        ),

    CONSTRAINT catalyst_impact_magnitude
        CHECK (
            magnitude IN ('LOW', 'MEDIUM', 'HIGH')
        ),

    CONSTRAINT catalyst_impact_horizon
        CHECK (
            horizon IN ('NEAR_TERM', 'MEDIUM_TERM', 'LONG_TERM')
        ),

    CONSTRAINT catalyst_impact_probability_range
        CHECK (
            probability >= 0
            AND probability <= 1
        ),

    CONSTRAINT catalyst_impact_confidence_range
        CHECK (
            confidence >= 0
            AND confidence <= 1
        )
)
""".strip(),
    ),
)



EXTEND_VALUATION_DEPENDENCIES_WITH_CATALYST_IMPACT = Migration(
    version=18,
    name="extend_valuation_dependencies_with_catalyst_impact",
    statements=(
        """
ALTER TABLE domain_edges
    DROP CONSTRAINT domain_edges_target_type
""".strip(),
        """
ALTER TABLE domain_edges
    ADD CONSTRAINT domain_edges_target_type
        CHECK (
            target_type IN (
                'Evidence',
                'Metric',
                'Calculation',
                'Claim',
                'Forecast',
                'Estimate',
                'CatalystImpact'
            )
        )
""".strip(),
        """
ALTER TABLE domain_edges
    DROP CONSTRAINT domain_edges_source_relation_target_type
""".strip(),
        """
ALTER TABLE domain_edges
    ADD CONSTRAINT domain_edges_source_relation_target_type
        CHECK (
            (
                source_type = 'Claim'
                AND edge_type IN ('SUPPORTED_BY', 'CONTRADICTED_BY')
                AND target_type = 'Evidence'
            )
            OR
            (
                source_type = 'Metric'
                AND edge_type = 'SUPPORTED_BY'
                AND target_type = 'Evidence'
            )
            OR
            (
                source_type = 'Calculation'
                AND edge_type = 'DERIVED_FROM'
                AND target_type IN ('Metric', 'Calculation')
            )
            OR
            (
                source_type = 'Estimate'
                AND edge_type = 'DERIVED_FROM'
                AND target_type IN ('Metric', 'Calculation', 'Claim')
            )
            OR
            (
                source_type = 'Valuation'
                AND edge_type = 'DEPENDS_ON'
                AND target_type IN (
                    'Forecast',
                    'Estimate',
                    'Metric',
                    'Calculation',
                    'CatalystImpact'
                )
            )
            OR
            (
                source_type = 'Forecast'
                AND edge_type = 'CONTAINS'
                AND target_type = 'Estimate'
            )
            OR
            (
                source_type = 'Catalyst'
                AND edge_type = 'AFFECTS'
                AND target_type IN ('Claim', 'Forecast')
            )
        )
""".strip(),
    ),
)



RECOMMENDATION_FACTS = Migration(
    version=19,
    name="add_recommendation_facts",
    statements=(
        """
CREATE TABLE recommendation_facts (
    node_id TEXT PRIMARY KEY,
    node_type TEXT NOT NULL DEFAULT 'Recommendation',
    security_id TEXT NOT NULL,
    action TEXT NOT NULL,
    as_of TIMESTAMPTZ NOT NULL,
    created_by TEXT NOT NULL,
    rationale_claim_ids TEXT[] NOT NULL,
    CONSTRAINT recommendation_node_type
        CHECK (node_type = 'Recommendation'),
    CONSTRAINT recommendation_domain_node_fk
        FOREIGN KEY (node_id, node_type)
        REFERENCES domain_nodes(id, node_type)
        ON DELETE RESTRICT
)
""".strip(),
    ),
)


RECOMMENDATION_DEPENDENCIES = Migration(
    version=20,
    name="add_recommendation_dependencies",
    statements=(
        """
ALTER TABLE domain_edges
    DROP CONSTRAINT domain_edges_target_type
""".strip(),
        """
ALTER TABLE domain_edges
    ADD CONSTRAINT domain_edges_target_type
        CHECK (
            target_type IN (
                'Evidence',
                'Metric',
                'Calculation',
                'Claim',
                'Forecast',
                'Estimate',
                'CatalystImpact',
                'Valuation',
                'Risk',
                'Catalyst'
            )
        )
""".strip(),
        """
ALTER TABLE domain_edges
    DROP CONSTRAINT domain_edges_source_relation_target_type
""".strip(),
        """
ALTER TABLE domain_edges
    ADD CONSTRAINT domain_edges_source_relation_target_type
        CHECK (
            (
                source_type = 'Claim'
                AND edge_type IN ('SUPPORTED_BY', 'CONTRADICTED_BY')
                AND target_type = 'Evidence'
            )
            OR
            (
                source_type = 'Metric'
                AND edge_type = 'SUPPORTED_BY'
                AND target_type = 'Evidence'
            )
            OR
            (
                source_type = 'Calculation'
                AND edge_type = 'DERIVED_FROM'
                AND target_type IN ('Metric', 'Calculation')
            )
            OR
            (
                source_type = 'Estimate'
                AND edge_type = 'DERIVED_FROM'
                AND target_type IN ('Metric', 'Calculation', 'Claim')
            )
            OR
            (
                source_type = 'Valuation'
                AND edge_type = 'DEPENDS_ON'
                AND target_type IN (
                    'Forecast',
                    'Estimate',
                    'Metric',
                    'Calculation',
                    'CatalystImpact'
                )
            )
            OR
            (
                source_type = 'Forecast'
                AND edge_type = 'CONTAINS'
                AND target_type = 'Estimate'
            )
            OR
            (
                source_type = 'Catalyst'
                AND edge_type = 'AFFECTS'
                AND target_type IN ('Claim', 'Forecast')
            )
            OR
            (
                source_type = 'Recommendation'
                AND edge_type = 'DEPENDS_ON'
                AND target_type IN (
                    'Valuation',
                    'Claim',
                    'Risk',
                    'Catalyst'
                )
            )
        )
""".strip(),
    ),
)


RISK_FACTS = Migration(
    version=21,
    name="add_risk_facts",
    statements=(
        """
CREATE TABLE risk_facts (
    node_id TEXT PRIMARY KEY,
    node_type TEXT NOT NULL DEFAULT 'Risk',
    subject_id TEXT NOT NULL,
    description TEXT NOT NULL,
    as_of TIMESTAMPTZ NOT NULL,

    CONSTRAINT risk_node_type
        CHECK (node_type = 'Risk'),

    CONSTRAINT risk_domain_node_fk
        FOREIGN KEY (node_id, node_type)
        REFERENCES domain_nodes(id, node_type)
        ON DELETE RESTRICT
)
""".strip(),
    ),
)

RISK_AFFECTS_EDGES = Migration(
    version=22,
    name="add_risk_affects_edges",
    statements=(
        """
ALTER TABLE domain_edges
    DROP CONSTRAINT domain_edges_target_type
""".strip(),
        """
ALTER TABLE domain_edges
    ADD CONSTRAINT domain_edges_target_type
        CHECK (
            target_type IN (
                'Evidence',
                'Metric',
                'Calculation',
                'Claim',
                'Forecast',
                'Estimate',
                'CatalystImpact',
                'Valuation',
                'Risk',
                'Catalyst'
            )
        )
""".strip(),
        """
ALTER TABLE domain_edges
    DROP CONSTRAINT domain_edges_source_relation_target_type
""".strip(),
        """
ALTER TABLE domain_edges
    ADD CONSTRAINT domain_edges_source_relation_target_type
        CHECK (
            (
                source_type = 'Claim'
                AND edge_type IN ('SUPPORTED_BY', 'CONTRADICTED_BY')
                AND target_type = 'Evidence'
            )
            OR
            (
                source_type = 'Metric'
                AND edge_type = 'SUPPORTED_BY'
                AND target_type = 'Evidence'
            )
            OR
            (
                source_type = 'Calculation'
                AND edge_type = 'DERIVED_FROM'
                AND target_type IN ('Metric', 'Calculation')
            )
            OR
            (
                source_type = 'Estimate'
                AND edge_type = 'DERIVED_FROM'
                AND target_type IN ('Metric', 'Calculation', 'Claim')
            )
            OR
            (
                source_type = 'Valuation'
                AND edge_type = 'DEPENDS_ON'
                AND target_type IN (
                    'Forecast',
                    'Estimate',
                    'Metric',
                    'Calculation',
                    'CatalystImpact'
                )
            )
            OR
            (
                source_type = 'Forecast'
                AND edge_type = 'CONTAINS'
                AND target_type = 'Estimate'
            )
            OR
            (
                source_type = 'Catalyst'
                AND edge_type = 'AFFECTS'
                AND target_type IN ('Claim', 'Forecast')
            )
            OR
            (
                source_type = 'Recommendation'
                AND edge_type = 'DEPENDS_ON'
                AND target_type IN (
                    'Valuation',
                    'Claim',
                    'Risk',
                    'Catalyst'
                )
            )
            OR
            (
                source_type = 'Risk'
                AND edge_type = 'AFFECTS'
                AND target_type IN ('Claim', 'Forecast', 'Valuation')
            )
        )
""".strip(),
    ),
)


MIGRATIONS = (
    INITIAL_SCHEMA,
    EDGE_CREATED_AT,
    EDGE_ENDPOINT_TYPES,
    METRIC_FACTS,
    ADD_METRIC_EVIDENCE_EDGES,
    METRIC_REVISION_CONSTRAINTS,
    CALCULATION_FACTS,
    CALCULATION_INPUT_PROVENANCE,
    ESTIMATE_FACTS,
    ESTIMATE_INPUT_PROVENANCE,
    VALUATION_FACTS,
    VALUATION_DEPENDENCIES,
    FORECAST_FACTS,
    ADD_FORECAST_COMPOSITION,
    CATALYST_FACTS,
    CATALYST_AFFECTS_EDGES,
    CATALYST_IMPACT_FACTS,
    EXTEND_VALUATION_DEPENDENCIES_WITH_CATALYST_IMPACT,
    RECOMMENDATION_FACTS,
    RECOMMENDATION_DEPENDENCIES,
    RISK_FACTS,
    RISK_AFFECTS_EDGES,
)
CURRENT_SCHEMA_VERSION = MIGRATIONS[-1].version

validate_migrations(MIGRATIONS)



# Canonical PostgreSQL 16 physical constraint surface for schema v22.
# This is a final-state contract, not a union of historical migration DDL.
EXPECTED_SCHEMA_V22_CONSTRAINTS = (('calculation_facts',
  'calculation_domain_node_fk',
  'f',
  'FOREIGN KEY (node_id, node_type) REFERENCES domain_nodes(id, node_type) ON DELETE RESTRICT'),
 ('calculation_facts', 'calculation_facts_pkey', 'p', 'PRIMARY KEY (node_id)'),
 ('calculation_facts', 'calculation_node_type', 'c', "CHECK ((node_type = 'Calculation'::text))"),
 ('catalyst_facts',
  'catalyst_domain_node_fk',
  'f',
  'FOREIGN KEY (node_id, node_type) REFERENCES domain_nodes(id, node_type) ON DELETE RESTRICT'),
 ('catalyst_facts', 'catalyst_facts_pkey', 'p', 'PRIMARY KEY (node_id)'),
 ('catalyst_facts', 'catalyst_node_type', 'c', "CHECK ((node_type = 'Catalyst'::text))"),
 ('catalyst_impact_facts',
  'catalyst_impact_confidence_range',
  'c',
  'CHECK (((confidence >= (0)::numeric) AND (confidence <= (1)::numeric)))'),
 ('catalyst_impact_facts',
  'catalyst_impact_direction',
  'c',
  "CHECK ((direction = ANY (ARRAY['POSITIVE'::text, 'NEGATIVE'::text, 'NEUTRAL'::text])))"),
 ('catalyst_impact_facts',
  'catalyst_impact_domain_node_fk',
  'f',
  'FOREIGN KEY (node_id, node_type) REFERENCES domain_nodes(id, node_type) ON DELETE RESTRICT'),
 ('catalyst_impact_facts', 'catalyst_impact_facts_pkey', 'p', 'PRIMARY KEY (node_id)'),
 ('catalyst_impact_facts',
  'catalyst_impact_horizon',
  'c',
  "CHECK ((horizon = ANY (ARRAY['NEAR_TERM'::text, 'MEDIUM_TERM'::text, 'LONG_TERM'::text])))"),
 ('catalyst_impact_facts',
  'catalyst_impact_magnitude',
  'c',
  "CHECK ((magnitude = ANY (ARRAY['LOW'::text, 'MEDIUM'::text, 'HIGH'::text])))"),
 ('catalyst_impact_facts',
  'catalyst_impact_node_type',
  'c',
  "CHECK ((node_type = 'CatalystImpact'::text))"),
 ('catalyst_impact_facts',
  'catalyst_impact_probability_range',
  'c',
  'CHECK (((probability >= (0)::numeric) AND (probability <= (1)::numeric)))'),
 ('claim_facts',
  'claim_domain_node_fk',
  'f',
  'FOREIGN KEY (node_id, node_type) REFERENCES domain_nodes(id, node_type) ON DELETE RESTRICT'),
 ('claim_facts', 'claim_facts_pkey', 'p', 'PRIMARY KEY (node_id)'),
 ('claim_facts', 'claim_node_type', 'c', "CHECK ((node_type = 'Claim'::text))"),
 ('domain_edges', 'domain_edges_no_self_edge', 'c', 'CHECK ((source_id <> target_id))'),
 ('domain_edges', 'domain_edges_pkey', 'p', 'PRIMARY KEY (source_id, edge_type, target_id)'),
 ('domain_edges',
  'domain_edges_source_id_fkey',
  'f',
  'FOREIGN KEY (source_id) REFERENCES domain_nodes(id) ON DELETE RESTRICT'),
 ('domain_edges',
  'domain_edges_source_relation_target_type',
  'c',
  "CHECK ((((source_type = 'Claim'::text) AND (edge_type = ANY (ARRAY['SUPPORTED_BY'::text, "
  "'CONTRADICTED_BY'::text])) AND (target_type = 'Evidence'::text)) OR ((source_type = "
  "'Metric'::text) AND (edge_type = 'SUPPORTED_BY'::text) AND (target_type = 'Evidence'::text)) OR "
  "((source_type = 'Calculation'::text) AND (edge_type = 'DERIVED_FROM'::text) AND (target_type = "
  "ANY (ARRAY['Metric'::text, 'Calculation'::text]))) OR ((source_type = 'Estimate'::text) AND "
  "(edge_type = 'DERIVED_FROM'::text) AND (target_type = ANY (ARRAY['Metric'::text, "
  "'Calculation'::text, 'Claim'::text]))) OR ((source_type = 'Valuation'::text) AND (edge_type = "
  "'DEPENDS_ON'::text) AND (target_type = ANY (ARRAY['Forecast'::text, 'Estimate'::text, "
  "'Metric'::text, 'Calculation'::text, 'CatalystImpact'::text]))) OR ((source_type = "
  "'Forecast'::text) AND (edge_type = 'CONTAINS'::text) AND (target_type = 'Estimate'::text)) OR "
  "((source_type = 'Catalyst'::text) AND (edge_type = 'AFFECTS'::text) AND (target_type = ANY "
  "(ARRAY['Claim'::text, 'Forecast'::text]))) OR ((source_type = 'Recommendation'::text) AND "
  "(edge_type = 'DEPENDS_ON'::text) AND (target_type = ANY (ARRAY['Valuation'::text, "
  "'Claim'::text, 'Risk'::text, 'Catalyst'::text]))) OR ((source_type = 'Risk'::text) AND "
  "(edge_type = 'AFFECTS'::text) AND (target_type = ANY (ARRAY['Claim'::text, 'Forecast'::text, "
  "'Valuation'::text])))))"),
 ('domain_edges',
  'domain_edges_source_typed_fk',
  'f',
  'FOREIGN KEY (source_id, source_type) REFERENCES domain_nodes(id, node_type) ON DELETE RESTRICT'),
 ('domain_edges',
  'domain_edges_target_id_fkey',
  'f',
  'FOREIGN KEY (target_id) REFERENCES domain_nodes(id) ON DELETE RESTRICT'),
 ('domain_edges',
  'domain_edges_target_type',
  'c',
  "CHECK ((target_type = ANY (ARRAY['Evidence'::text, 'Metric'::text, 'Calculation'::text, "
  "'Claim'::text, 'Forecast'::text, 'Estimate'::text, 'CatalystImpact'::text, 'Valuation'::text, "
  "'Risk'::text, 'Catalyst'::text])))"),
 ('domain_edges',
  'domain_edges_target_typed_fk',
  'f',
  'FOREIGN KEY (target_id, target_type) REFERENCES domain_nodes(id, node_type) ON DELETE RESTRICT'),
 ('domain_edges',
  'domain_edges_type',
  'c',
  "CHECK ((edge_type = ANY (ARRAY['SUPPORTED_BY'::text, 'CONTRADICTED_BY'::text, "
  "'DERIVED_FROM'::text, 'DEPENDS_ON'::text, 'CONTAINS'::text, 'AFFECTS'::text])))"),
 ('domain_nodes', 'domain_nodes_id_node_type_unique', 'u', 'UNIQUE (id, node_type)'),
 ('domain_nodes',
  'domain_nodes_node_type',
  'c',
  "CHECK ((node_type = ANY (ARRAY['Company'::text, 'Security'::text, 'Metric'::text, "
  "'Claim'::text, 'Evidence'::text, 'Calculation'::text, 'Estimate'::text, 'Forecast'::text, "
  "'Catalyst'::text, 'CatalystImpact'::text, 'Risk'::text, 'Valuation'::text, "
  "'Recommendation'::text])))"),
 ('domain_nodes',
  'domain_nodes_payload_hash_sha256',
  'c',
  "CHECK ((payload_hash ~ '^[0-9a-f]{64}$'::text))"),
 ('domain_nodes', 'domain_nodes_pkey', 'p', 'PRIMARY KEY (id)'),
 ('estimate_facts',
  'estimate_domain_node_fk',
  'f',
  'FOREIGN KEY (node_id, node_type) REFERENCES domain_nodes(id, node_type) ON DELETE RESTRICT'),
 ('estimate_facts', 'estimate_facts_pkey', 'p', 'PRIMARY KEY (node_id)'),
 ('estimate_facts', 'estimate_node_type', 'c', "CHECK ((node_type = 'Estimate'::text))"),
 ('evidence_facts',
  'evidence_content_hash_sha256',
  'c',
  "CHECK ((content_hash ~ '^[0-9a-f]{64}$'::text))"),
 ('evidence_facts',
  'evidence_domain_node_fk',
  'f',
  'FOREIGN KEY (node_id, node_type) REFERENCES domain_nodes(id, node_type) ON DELETE RESTRICT'),
 ('evidence_facts', 'evidence_facts_pkey', 'p', 'PRIMARY KEY (node_id)'),
 ('evidence_facts',
  'evidence_facts_supersedes_id_fkey',
  'f',
  'FOREIGN KEY (supersedes_id) REFERENCES evidence_facts(node_id) ON DELETE RESTRICT'),
 ('evidence_facts', 'evidence_ingestion_order', 'c', 'CHECK ((published_at <= ingested_at))'),
 ('evidence_facts',
  'evidence_no_self_supersession',
  'c',
  'CHECK (((supersedes_id IS NULL) OR (supersedes_id <> node_id)))'),
 ('evidence_facts', 'evidence_node_type', 'c', "CHECK ((node_type = 'Evidence'::text))"),
 ('evidence_facts', 'evidence_publication_order', 'c', 'CHECK ((observed_at <= published_at))'),
 ('forecast_facts',
  'forecast_domain_node_fk',
  'f',
  'FOREIGN KEY (node_id, node_type) REFERENCES domain_nodes(id, node_type) ON DELETE RESTRICT'),
 ('forecast_facts', 'forecast_facts_pkey', 'p', 'PRIMARY KEY (node_id)'),
 ('forecast_facts', 'forecast_node_type', 'c', "CHECK ((node_type = 'Forecast'::text))"),
 ('investment_domain_schema_migrations',
  'investment_domain_schema_migrations_pkey',
  'p',
  'PRIMARY KEY (version)'),
 ('metric_facts',
  'metric_domain_node_fk',
  'f',
  'FOREIGN KEY (node_id, node_type) REFERENCES domain_nodes(id, node_type) ON DELETE RESTRICT'),
 ('metric_facts', 'metric_facts_pkey', 'p', 'PRIMARY KEY (node_id)'),
 ('metric_facts',
  'metric_no_self_supersession',
  'c',
  'CHECK (((supersedes_id IS NULL) OR (supersedes_id <> node_id)))'),
 ('metric_facts', 'metric_node_type', 'c', "CHECK ((node_type = 'Metric'::text))"),
 ('metric_facts',
  'metric_supersedes_fk',
  'f',
  'FOREIGN KEY (supersedes_id) REFERENCES metric_facts(node_id) ON DELETE RESTRICT'),
 ('recommendation_facts',
  'recommendation_domain_node_fk',
  'f',
  'FOREIGN KEY (node_id, node_type) REFERENCES domain_nodes(id, node_type) ON DELETE RESTRICT'),
 ('recommendation_facts', 'recommendation_facts_pkey', 'p', 'PRIMARY KEY (node_id)'),
 ('recommendation_facts',
  'recommendation_node_type',
  'c',
  "CHECK ((node_type = 'Recommendation'::text))"),
 ('risk_facts',
  'risk_domain_node_fk',
  'f',
  'FOREIGN KEY (node_id, node_type) REFERENCES domain_nodes(id, node_type) ON DELETE RESTRICT'),
 ('risk_facts', 'risk_facts_pkey', 'p', 'PRIMARY KEY (node_id)'),
 ('risk_facts', 'risk_node_type', 'c', "CHECK ((node_type = 'Risk'::text))"),
 ('valuation_facts',
  'valuation_domain_node_fk',
  'f',
  'FOREIGN KEY (node_id, node_type) REFERENCES domain_nodes(id, node_type) ON DELETE RESTRICT'),
 ('valuation_facts', 'valuation_facts_pkey', 'p', 'PRIMARY KEY (node_id)'),
 ('valuation_facts', 'valuation_node_type', 'c', "CHECK ((node_type = 'Valuation'::text))"))



EXPECTED_SCHEMA_V22_COLUMNS = (('calculation_facts', 'node_id', 1, 'text', 'text', 'NO', ''),
 ('calculation_facts', 'node_type', 2, 'text', 'text', 'NO', "'Calculation'::text"),
 ('calculation_facts', 'subject_id', 3, 'text', 'text', 'NO', ''),
 ('calculation_facts', 'formula', 4, 'text', 'text', 'NO', ''),
 ('calculation_facts', 'input_ids', 5, 'ARRAY', '_text', 'NO', ''),
 ('calculation_facts', 'value', 6, 'numeric', 'numeric', 'NO', ''),
 ('calculation_facts', 'unit', 7, 'text', 'text', 'NO', ''),
 ('calculation_facts', 'currency', 8, 'text', 'text', 'YES', ''),
 ('calculation_facts', 'model_version', 9, 'text', 'text', 'NO', ''),
 ('catalyst_facts', 'node_id', 1, 'text', 'text', 'NO', ''),
 ('catalyst_facts', 'node_type', 2, 'text', 'text', 'NO', "'Catalyst'::text"),
 ('catalyst_facts', 'subject_id', 3, 'text', 'text', 'NO', ''),
 ('catalyst_facts', 'description', 4, 'text', 'text', 'NO', ''),
 ('catalyst_facts', 'as_of', 5, 'timestamp with time zone', 'timestamptz', 'NO', ''),
 ('catalyst_facts', 'expected_at', 6, 'timestamp with time zone', 'timestamptz', 'YES', ''),
 ('catalyst_impact_facts', 'node_id', 1, 'text', 'text', 'NO', ''),
 ('catalyst_impact_facts', 'node_type', 2, 'text', 'text', 'NO', "'CatalystImpact'::text"),
 ('catalyst_impact_facts', 'catalyst_id', 3, 'text', 'text', 'NO', ''),
 ('catalyst_impact_facts', 'target_id', 4, 'text', 'text', 'NO', ''),
 ('catalyst_impact_facts', 'direction', 5, 'text', 'text', 'NO', ''),
 ('catalyst_impact_facts', 'magnitude', 6, 'text', 'text', 'NO', ''),
 ('catalyst_impact_facts', 'probability', 7, 'numeric', 'numeric', 'NO', ''),
 ('catalyst_impact_facts', 'confidence', 8, 'numeric', 'numeric', 'NO', ''),
 ('catalyst_impact_facts', 'horizon', 9, 'text', 'text', 'NO', ''),
 ('catalyst_impact_facts', 'rationale', 10, 'text', 'text', 'NO', ''),
 ('catalyst_impact_facts', 'as_of', 11, 'timestamp with time zone', 'timestamptz', 'NO', ''),
 ('catalyst_impact_facts', 'created_by', 12, 'text', 'text', 'NO', ''),
 ('claim_facts', 'node_id', 1, 'text', 'text', 'NO', ''),
 ('claim_facts', 'node_type', 2, 'text', 'text', 'NO', "'Claim'::text"),
 ('claim_facts', 'subject_id', 3, 'text', 'text', 'NO', ''),
 ('claim_facts', 'predicate', 4, 'text', 'text', 'NO', ''),
 ('claim_facts', 'as_of', 5, 'timestamp with time zone', 'timestamptz', 'NO', ''),
 ('claim_facts', 'polarity', 6, 'text', 'text', 'NO', ''),
 ('claim_facts', 'scope', 7, 'text', 'text', 'NO', ''),
 ('domain_edges', 'source_id', 1, 'text', 'text', 'NO', ''),
 ('domain_edges', 'edge_type', 2, 'text', 'text', 'NO', ''),
 ('domain_edges', 'target_id', 3, 'text', 'text', 'NO', ''),
 ('domain_edges',
  'stored_at',
  4,
  'timestamp with time zone',
  'timestamptz',
  'NO',
  'CURRENT_TIMESTAMP'),
 ('domain_edges', 'created_at', 5, 'timestamp with time zone', 'timestamptz', 'NO', ''),
 ('domain_edges', 'source_type', 6, 'text', 'text', 'NO', ''),
 ('domain_edges', 'target_type', 7, 'text', 'text', 'NO', ''),
 ('domain_nodes', 'id', 1, 'text', 'text', 'NO', ''),
 ('domain_nodes', 'node_type', 2, 'text', 'text', 'NO', ''),
 ('domain_nodes', 'canonical_payload', 3, 'jsonb', 'jsonb', 'NO', ''),
 ('domain_nodes', 'payload_hash', 4, 'text', 'text', 'NO', ''),
 ('domain_nodes',
  'stored_at',
  5,
  'timestamp with time zone',
  'timestamptz',
  'NO',
  'CURRENT_TIMESTAMP'),
 ('estimate_facts', 'node_id', 1, 'text', 'text', 'NO', ''),
 ('estimate_facts', 'node_type', 2, 'text', 'text', 'NO', "'Estimate'::text"),
 ('estimate_facts', 'subject_id', 3, 'text', 'text', 'NO', ''),
 ('estimate_facts', 'metric_name', 4, 'text', 'text', 'NO', ''),
 ('estimate_facts', 'period_end', 5, 'timestamp with time zone', 'timestamptz', 'NO', ''),
 ('estimate_facts', 'value', 6, 'numeric', 'numeric', 'NO', ''),
 ('estimate_facts', 'unit', 7, 'text', 'text', 'NO', ''),
 ('estimate_facts', 'scenario', 8, 'text', 'text', 'NO', ''),
 ('estimate_facts', 'model_version', 9, 'text', 'text', 'NO', ''),
 ('estimate_facts', 'as_of', 10, 'timestamp with time zone', 'timestamptz', 'NO', ''),
 ('estimate_facts', 'currency', 11, 'text', 'text', 'YES', ''),
 ('evidence_facts', 'node_id', 1, 'text', 'text', 'NO', ''),
 ('evidence_facts', 'node_type', 2, 'text', 'text', 'NO', "'Evidence'::text"),
 ('evidence_facts', 'source_id', 3, 'text', 'text', 'NO', ''),
 ('evidence_facts', 'source_version', 4, 'text', 'text', 'NO', ''),
 ('evidence_facts', 'content_hash', 5, 'text', 'text', 'NO', ''),
 ('evidence_facts', 'effective_at', 6, 'timestamp with time zone', 'timestamptz', 'NO', ''),
 ('evidence_facts', 'observed_at', 7, 'timestamp with time zone', 'timestamptz', 'NO', ''),
 ('evidence_facts', 'published_at', 8, 'timestamp with time zone', 'timestamptz', 'NO', ''),
 ('evidence_facts', 'ingested_at', 9, 'timestamp with time zone', 'timestamptz', 'NO', ''),
 ('evidence_facts', 'supersedes_id', 10, 'text', 'text', 'YES', ''),
 ('evidence_facts', 'source_uri', 11, 'text', 'text', 'YES', ''),
 ('forecast_facts', 'node_id', 1, 'text', 'text', 'NO', ''),
 ('forecast_facts', 'node_type', 2, 'text', 'text', 'NO', "'Forecast'::text"),
 ('forecast_facts', 'subject_id', 3, 'text', 'text', 'NO', ''),
 ('forecast_facts', 'scenario', 4, 'text', 'text', 'NO', ''),
 ('forecast_facts', 'as_of', 5, 'timestamp with time zone', 'timestamptz', 'NO', ''),
 ('forecast_facts', 'model_version', 6, 'text', 'text', 'NO', ''),
 ('investment_domain_schema_migrations', 'version', 1, 'integer', 'int4', 'NO', ''),
 ('investment_domain_schema_migrations', 'name', 2, 'text', 'text', 'NO', ''),
 ('investment_domain_schema_migrations', 'checksum', 3, 'text', 'text', 'NO', ''),
 ('investment_domain_schema_migrations',
  'applied_at',
  4,
  'timestamp with time zone',
  'timestamptz',
  'NO',
  'CURRENT_TIMESTAMP'),
 ('metric_facts', 'node_id', 1, 'text', 'text', 'NO', ''),
 ('metric_facts', 'node_type', 2, 'text', 'text', 'NO', "'Metric'::text"),
 ('metric_facts', 'subject_id', 3, 'text', 'text', 'NO', ''),
 ('metric_facts', 'name', 4, 'text', 'text', 'NO', ''),
 ('metric_facts', 'value', 5, 'numeric', 'numeric', 'NO', ''),
 ('metric_facts', 'unit', 6, 'text', 'text', 'NO', ''),
 ('metric_facts', 'currency', 7, 'text', 'text', 'YES', ''),
 ('metric_facts', 'period_start', 8, 'timestamp with time zone', 'timestamptz', 'YES', ''),
 ('metric_facts', 'period_end', 9, 'timestamp with time zone', 'timestamptz', 'NO', ''),
 ('metric_facts', 'effective_at', 10, 'timestamp with time zone', 'timestamptz', 'NO', ''),
 ('metric_facts', 'observed_at', 11, 'timestamp with time zone', 'timestamptz', 'NO', ''),
 ('metric_facts', 'published_at', 12, 'timestamp with time zone', 'timestamptz', 'NO', ''),
 ('metric_facts', 'ingested_at', 13, 'timestamp with time zone', 'timestamptz', 'NO', ''),
 ('metric_facts', 'source_id', 14, 'text', 'text', 'NO', ''),
 ('metric_facts', 'source_version', 15, 'text', 'text', 'NO', ''),
 ('metric_facts', 'supersedes_id', 16, 'text', 'text', 'YES', ''),
 ('recommendation_facts', 'node_id', 1, 'text', 'text', 'NO', ''),
 ('recommendation_facts', 'node_type', 2, 'text', 'text', 'NO', "'Recommendation'::text"),
 ('recommendation_facts', 'security_id', 3, 'text', 'text', 'NO', ''),
 ('recommendation_facts', 'action', 4, 'text', 'text', 'NO', ''),
 ('recommendation_facts', 'as_of', 5, 'timestamp with time zone', 'timestamptz', 'NO', ''),
 ('recommendation_facts', 'created_by', 6, 'text', 'text', 'NO', ''),
 ('recommendation_facts', 'rationale_claim_ids', 7, 'ARRAY', '_text', 'NO', ''),
 ('risk_facts', 'node_id', 1, 'text', 'text', 'NO', ''),
 ('risk_facts', 'node_type', 2, 'text', 'text', 'NO', "'Risk'::text"),
 ('risk_facts', 'subject_id', 3, 'text', 'text', 'NO', ''),
 ('risk_facts', 'description', 4, 'text', 'text', 'NO', ''),
 ('risk_facts', 'as_of', 5, 'timestamp with time zone', 'timestamptz', 'NO', ''),
 ('valuation_facts', 'node_id', 1, 'text', 'text', 'NO', ''),
 ('valuation_facts', 'node_type', 2, 'text', 'text', 'NO', "'Valuation'::text"),
 ('valuation_facts', 'security_id', 3, 'text', 'text', 'NO', ''),
 ('valuation_facts', 'method', 4, 'text', 'text', 'NO', ''),
 ('valuation_facts', 'value', 5, 'numeric', 'numeric', 'NO', ''),
 ('valuation_facts', 'currency', 6, 'text', 'text', 'NO', ''),
 ('valuation_facts', 'as_of', 7, 'timestamp with time zone', 'timestamptz', 'NO', ''),
 ('valuation_facts', 'model_version', 8, 'text', 'text', 'NO', ''),
 ('valuation_facts', 'scenario', 9, 'text', 'text', 'NO', ''))

EXPECTED_SCHEMA_V22_EXPLICIT_INDEXES = (('domain_edges',
  'idx_domain_edges_target',
  False,
  False,
  'CREATE INDEX idx_domain_edges_target ON public.domain_edges USING btree (target_id, edge_type, '
  'source_id)'),
 ('evidence_facts',
  'idx_evidence_supersedes',
  True,
  False,
  'CREATE UNIQUE INDEX idx_evidence_supersedes ON public.evidence_facts USING btree '
  '(supersedes_id) WHERE (supersedes_id IS NOT NULL)'),
 ('evidence_facts',
  'idx_evidence_visibility',
  False,
  False,
  'CREATE INDEX idx_evidence_visibility ON public.evidence_facts USING btree (published_at, '
  'ingested_at, node_id)'),
 ('metric_facts',
  'metric_one_successor_per_predecessor',
  True,
  False,
  'CREATE UNIQUE INDEX metric_one_successor_per_predecessor ON public.metric_facts USING btree '
  '(supersedes_id) WHERE (supersedes_id IS NOT NULL)'))

EXPECTED_SCHEMA_V22_TRIGGERS: tuple[
    tuple[str, str, str, str, str], ...
] = ()


# Exact Investment Domain table boundary owned by the canonical v22
# physical-schema contract. Objects on unrelated tables in the same
# PostgreSQL schema are intentionally outside this attestation surface.
EXPECTED_SCHEMA_V22_TABLES = (
    "calculation_facts",
    "catalyst_facts",
    "catalyst_impact_facts",
    "claim_facts",
    "domain_edges",
    "domain_nodes",
    "estimate_facts",
    "evidence_facts",
    "forecast_facts",
    "investment_domain_schema_migrations",
    "metric_facts",
    "recommendation_facts",
    "risk_facts",
    "valuation_facts",
)


class MigrationError(RuntimeError):
    """Fail-closed Investment Domain schema migration error."""


def advisory_lock_key(namespace: str = MIGRATION_LOCK_NAMESPACE) -> int:
    """Derive a deterministic PostgreSQL signed BIGINT advisory-lock key."""
    digest = hashlib.sha256(namespace.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


class PostgreSQLMigrationManager:
    """
    PostgreSQL schema authority for the Investment Domain Evidence Store.

    Migration ownership, history validation, and pending migration execution
    occur in one transaction protected by a transaction-scoped advisory lock.
    """

    def __init__(
        self,
        dsn: str,
        migrations: tuple[Migration, ...] = MIGRATIONS,
        lock_timeout_ms: int = 5000,
    ) -> None:
        validate_migrations(migrations)

        if lock_timeout_ms <= 0:
            raise ValueError(
                "IDM-M403: LOCK_TIMEOUT_MUST_BE_POSITIVE"
            )

        self.dsn = dsn
        self.migrations = tuple(migrations)
        self.lock_timeout_ms = int(lock_timeout_ms)

    @staticmethod
    def _psycopg():
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError(
                "psycopg is required for PostgreSQLMigrationManager"
            ) from exc
        return psycopg

    def connect(self):
        return self._psycopg().connect(self.dsn)

    @property
    def max_supported_version(self) -> int:
        return self.migrations[-1].version if self.migrations else 0

    def _bootstrap(self, con) -> None:
        con.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {MIGRATION_HISTORY_TABLE} (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                checksum TEXT NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL
                    DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

    def _applied(self, con) -> dict[int, tuple[str, str]]:
        rows = con.execute(
            f"""
            SELECT version, name, checksum
            FROM {MIGRATION_HISTORY_TABLE}
            ORDER BY version
            """
        ).fetchall()

        return {
            int(row[0]): (str(row[1]), str(row[2]))
            for row in rows
        }

    def _validate_history(
        self,
        rows: dict[int, tuple[str, str]],
    ) -> int:
        if rows and max(rows) > self.max_supported_version:
            raise MigrationError(
                "IDM-M410: DATABASE_SCHEMA_NEWER_THAN_RUNTIME"
            )

        applied_versions = sorted(rows)
        if (
            applied_versions
            and applied_versions
            != list(range(1, applied_versions[-1] + 1))
        ):
            raise MigrationError(
                "IDM-M414: APPLIED_MIGRATION_HISTORY_GAP"
            )

        by_version = {
            migration.version: migration
            for migration in self.migrations
        }

        for version, (name, checksum) in rows.items():
            migration = by_version.get(version)

            if (
                name != migration.name
                or checksum != migration.checksum
            ):
                raise MigrationError(
                    "IDM-M412: MIGRATION_CHECKSUM_MISMATCH"
                )

        return max(rows) if rows else 0

    @staticmethod
    def _physical_column_surface(con) -> tuple[
        tuple[str, str, int, str, str, str, str], ...
    ]:
        rows = con.execute(
            """
            SELECT
                c.table_name,
                c.column_name,
                c.ordinal_position,
                c.data_type,
                c.udt_name,
                c.is_nullable,
                COALESCE(c.column_default, '')
            FROM information_schema.columns AS c
            WHERE c.table_schema = current_schema()
              AND c.table_name = ANY(%s)
            ORDER BY c.table_name, c.ordinal_position
            """,
            (list(EXPECTED_SCHEMA_V22_TABLES),),
        ).fetchall()

        return tuple(
            (
                str(table_name),
                str(column_name),
                int(position),
                str(data_type),
                str(udt_name),
                str(nullable),
                str(default),
            )
            for (
                table_name,
                column_name,
                position,
                data_type,
                udt_name,
                nullable,
                default,
            ) in rows
        )

    @staticmethod
    def _physical_explicit_index_surface(con) -> tuple[
        tuple[str, str, bool, bool, str], ...
    ]:
        rows = con.execute(
            """
            SELECT
                t.relname,
                i.relname,
                ix.indisunique,
                ix.indisprimary,
                pg_get_indexdef(i.oid)
            FROM pg_index AS ix
            JOIN pg_class AS i
              ON i.oid = ix.indexrelid
            JOIN pg_class AS t
              ON t.oid = ix.indrelid
            JOIN pg_namespace AS n
              ON n.oid = t.relnamespace
            WHERE n.nspname = current_schema()
              AND t.relname = ANY(%s)
              AND NOT EXISTS (
                  SELECT 1
                  FROM pg_constraint AS c
                  WHERE c.conindid = i.oid
              )
            ORDER BY t.relname, i.relname
            """,
            (list(EXPECTED_SCHEMA_V22_TABLES),),
        ).fetchall()

        return tuple(
            (
                str(table_name),
                str(index_name),
                bool(unique),
                bool(primary),
                str(definition),
            )
            for (
                table_name,
                index_name,
                unique,
                primary,
                definition,
            ) in rows
        )

    @staticmethod
    def _physical_trigger_surface(con) -> tuple[
        tuple[str, str, str, str, str], ...
    ]:
        rows = con.execute(
            """
            SELECT
                event_object_table,
                trigger_name,
                event_manipulation,
                action_timing,
                action_statement
            FROM information_schema.triggers
            WHERE trigger_schema = current_schema()
              AND event_object_table = ANY(%s)
            ORDER BY
                event_object_table,
                trigger_name,
                event_manipulation
            """,
            (list(EXPECTED_SCHEMA_V22_TABLES),),
        ).fetchall()

        return tuple(
            tuple(str(value) for value in row)
            for row in rows
        )

    @staticmethod
    def _physical_constraint_surface(con) -> tuple[
        tuple[str, str, str, str], ...
    ]:
        rows = con.execute(
            """
            SELECT
                t.relname,
                c.conname,
                c.contype,
                pg_get_constraintdef(c.oid, false)
            FROM pg_constraint AS c
            JOIN pg_class AS t
              ON t.oid = c.conrelid
            JOIN pg_namespace AS n
              ON n.oid = t.relnamespace
            WHERE n.nspname = current_schema()
              AND t.relname = ANY(%s)
            ORDER BY t.relname, c.conname
            """,
            (list(EXPECTED_SCHEMA_V22_TABLES),),
        ).fetchall()

        return tuple(
            (
                str(table_name),
                str(constraint_name),
                str(constraint_type),
                str(definition),
            )
            for (
                table_name,
                constraint_name,
                constraint_type,
                definition,
            ) in rows
        )

    def _uses_canonical_migration_lineage(self) -> bool:
        return self.migrations == MIGRATIONS

    @staticmethod
    def _surface_diff(expected, actual):
        expected_set = set(expected)
        actual_set = set(actual)
        return (
            sorted(expected_set - actual_set),
            sorted(actual_set - expected_set),
        )

    def _validate_physical_schema(self, con, target: int) -> None:
        if not self._uses_canonical_migration_lineage():
            return

        if target != CURRENT_SCHEMA_VERSION:
            return

        expected_constraints = EXPECTED_SCHEMA_V22_CONSTRAINTS
        actual_constraints = self._physical_constraint_surface(con)

        expected_constraint_map = {
            (table_name, constraint_name): (
                constraint_type,
                definition,
            )
            for (
                table_name,
                constraint_name,
                constraint_type,
                definition,
            ) in expected_constraints
        }
        actual_constraint_map = {
            (table_name, constraint_name): (
                constraint_type,
                definition,
            )
            for (
                table_name,
                constraint_name,
                constraint_type,
                definition,
            ) in actual_constraints
        }

        constraint_missing = sorted(
            set(expected_constraint_map)
            - set(actual_constraint_map)
        )
        constraint_unexpected = sorted(
            set(actual_constraint_map)
            - set(expected_constraint_map)
        )
        constraint_mismatched = sorted(
            key
            for key in (
                set(expected_constraint_map)
                & set(actual_constraint_map)
            )
            if (
                expected_constraint_map[key]
                != actual_constraint_map[key]
            )
        )

        column_missing, column_unexpected = self._surface_diff(
            EXPECTED_SCHEMA_V22_COLUMNS,
            self._physical_column_surface(con),
        )
        index_missing, index_unexpected = self._surface_diff(
            EXPECTED_SCHEMA_V22_EXPLICIT_INDEXES,
            self._physical_explicit_index_surface(con),
        )
        trigger_missing, trigger_unexpected = self._surface_diff(
            EXPECTED_SCHEMA_V22_TRIGGERS,
            self._physical_trigger_surface(con),
        )

        if not any((
            constraint_missing,
            constraint_unexpected,
            constraint_mismatched,
            column_missing,
            column_unexpected,
            index_missing,
            index_unexpected,
            trigger_missing,
            trigger_unexpected,
        )):
            return

        raise MigrationError(
            "IDM-M411: PHYSICAL_SCHEMA_MISMATCH: "
            "constraints: "
            f"missing={constraint_missing!r}; "
            f"unexpected={constraint_unexpected!r}; "
            f"mismatched={constraint_mismatched!r}; "
            "columns: "
            f"missing={column_missing!r}; "
            f"unexpected={column_unexpected!r}; "
            "indexes: "
            f"missing={index_missing!r}; "
            f"unexpected={index_unexpected!r}; "
            "triggers: "
            f"missing={trigger_missing!r}; "
            f"unexpected={trigger_unexpected!r}"
        )

    def migrate(self, target_version: int | None = None) -> int:
        target = (
            self.max_supported_version
            if target_version is None
            else int(target_version)
        )

        if target < 0 or target > self.max_supported_version:
            raise MigrationError(
                "IDM-M409: UNSUPPORTED_TARGET_SCHEMA_VERSION"
            )

        with self.connect() as con:
            with con.transaction():
                con.execute(
                    "SELECT set_config('lock_timeout', %s, true)",
                    (f"{self.lock_timeout_ms}ms",),
                )

                con.execute(
                    "SELECT pg_advisory_xact_lock(%s)",
                    (advisory_lock_key(),),
                )

                self._bootstrap(con)
                rows = self._applied(con)
                current = self._validate_history(rows)

                if target < current:
                    raise MigrationError(
                        "IDM-M413: DOWNGRADE_NOT_SUPPORTED"
                    )

                for migration in self.migrations:
                    if not (
                        current < migration.version <= target
                    ):
                        continue

                    for statement in migration.statements:
                        con.execute(statement)

                    con.execute(
                        f"""
                        INSERT INTO {MIGRATION_HISTORY_TABLE}
                            (version, name, checksum)
                        VALUES (%s, %s, %s)
                        """,
                        (
                            migration.version,
                            migration.name,
                            migration.checksum,
                        ),
                    )

                self._validate_physical_schema(con, target)
                return target
