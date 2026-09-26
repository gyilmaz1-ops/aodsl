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
)
CURRENT_SCHEMA_VERSION = MIGRATIONS[-1].version

validate_migrations(MIGRATIONS)


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

                return target
