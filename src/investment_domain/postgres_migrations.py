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


MIGRATIONS = (
    INITIAL_SCHEMA,
    EDGE_CREATED_AT,
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
