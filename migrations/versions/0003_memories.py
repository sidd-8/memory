"""Add source-backed memories, claims, and revision history."""

from alembic import op

revision = "0003_memories"
down_revision = "0002_identity"


def upgrade() -> None:
    op.execute("""
        CREATE TABLE memories (
            workspace_id uuid NOT NULL,
            subject_id uuid NOT NULL,
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            category text NOT NULL CHECK (category IN (
                'semantic', 'episodic', 'procedural', 'project', 'working', 'commitment'
            )),
            content text NOT NULL CHECK (btrim(content) <> ''),
            embedding public.vector,
            expires_at timestamptz,
            revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (workspace_id, subject_id, id),
            FOREIGN KEY (workspace_id, subject_id)
                REFERENCES subjects (workspace_id, id)
        )
    """)
    op.execute("""
        CREATE TABLE sources (
            workspace_id uuid NOT NULL,
            subject_id uuid NOT NULL,
            memory_id uuid NOT NULL,
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            origin text NOT NULL CHECK (btrim(origin) <> ''),
            source_ref text NOT NULL CHECK (btrim(source_ref) <> ''),
            observed_at timestamptz NOT NULL,
            trust_class text NOT NULL CHECK (trust_class IN (
                'unverified', 'agent_reported', 'user_confirmed', 'authoritative'
            )),
            PRIMARY KEY (workspace_id, subject_id, memory_id, id),
            FOREIGN KEY (workspace_id, subject_id, memory_id)
                REFERENCES memories (workspace_id, subject_id, id) ON DELETE CASCADE
        )
    """)
    op.execute("""
        CREATE TABLE entities (
            workspace_id uuid NOT NULL,
            subject_id uuid NOT NULL,
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            kind text NOT NULL CHECK (btrim(kind) <> ''),
            reference text NOT NULL CHECK (btrim(reference) <> ''),
            PRIMARY KEY (workspace_id, subject_id, id),
            UNIQUE (workspace_id, subject_id, kind, reference),
            FOREIGN KEY (workspace_id, subject_id)
                REFERENCES subjects (workspace_id, id)
        )
    """)
    op.execute("""
        CREATE TABLE claims (
            workspace_id uuid NOT NULL,
            subject_id uuid NOT NULL,
            memory_id uuid NOT NULL,
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            source_id uuid NOT NULL,
            subject_entity_id uuid,
            predicate text NOT NULL CHECK (btrim(predicate) <> ''),
            object_value text CHECK (btrim(object_value) <> ''),
            object_entity_id uuid,
            valid_from timestamptz,
            valid_to timestamptz,
            confidence double precision CHECK (confidence BETWEEN 0 AND 1),
            status text NOT NULL DEFAULT 'candidate' CHECK (status IN (
                'candidate', 'active', 'review_due', 'stale', 'superseded', 'archived'
            )),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            superseded_at timestamptz,
            PRIMARY KEY (workspace_id, subject_id, id),
            FOREIGN KEY (workspace_id, subject_id, memory_id)
                REFERENCES memories (workspace_id, subject_id, id) ON DELETE CASCADE,
            FOREIGN KEY (workspace_id, subject_id, memory_id, source_id)
                REFERENCES sources (workspace_id, subject_id, memory_id, id),
            FOREIGN KEY (workspace_id, subject_id, subject_entity_id)
                REFERENCES entities (workspace_id, subject_id, id),
            FOREIGN KEY (workspace_id, subject_id, object_entity_id)
                REFERENCES entities (workspace_id, subject_id, id),
            CHECK ((object_value IS NULL) <> (object_entity_id IS NULL)),
            CHECK (object_entity_id IS NULL OR subject_entity_id IS NOT NULL),
            CHECK (valid_from IS NULL OR valid_to IS NULL OR valid_from < valid_to),
            CHECK ((status = 'superseded') = (superseded_at IS NOT NULL))
        )
    """)
    op.execute("""
        CREATE TABLE relationships (
            workspace_id uuid NOT NULL,
            subject_id uuid NOT NULL,
            claim_id uuid NOT NULL,
            from_entity_id uuid NOT NULL,
            to_entity_id uuid NOT NULL,
            predicate text NOT NULL,
            PRIMARY KEY (workspace_id, subject_id, claim_id),
            FOREIGN KEY (workspace_id, subject_id, claim_id)
                REFERENCES claims (workspace_id, subject_id, id) ON DELETE CASCADE,
            FOREIGN KEY (workspace_id, subject_id, from_entity_id)
                REFERENCES entities (workspace_id, subject_id, id),
            FOREIGN KEY (workspace_id, subject_id, to_entity_id)
                REFERENCES entities (workspace_id, subject_id, id)
        )
    """)
    op.execute("""
        CREATE TABLE memory_versions (
            workspace_id uuid NOT NULL,
            subject_id uuid NOT NULL,
            memory_id uuid NOT NULL,
            revision integer NOT NULL,
            agent_id uuid NOT NULL,
            snapshot jsonb NOT NULL,
            PRIMARY KEY (workspace_id, subject_id, memory_id, revision),
            FOREIGN KEY (workspace_id, subject_id, memory_id)
                REFERENCES memories (workspace_id, subject_id, id) ON DELETE CASCADE,
            FOREIGN KEY (workspace_id, agent_id)
                REFERENCES agents (workspace_id, id)
        )
    """)
    op.execute("""
        CREATE TABLE memory_requests (
            workspace_id uuid NOT NULL,
            subject_id uuid NOT NULL,
            agent_id uuid NOT NULL,
            key_digest bytea NOT NULL,
            request_digest bytea,
            memory_id uuid,
            revision integer,
            PRIMARY KEY (workspace_id, subject_id, agent_id, key_digest),
            FOREIGN KEY (workspace_id, subject_id)
                REFERENCES subjects (workspace_id, id),
            FOREIGN KEY (workspace_id, agent_id)
                REFERENCES agents (workspace_id, id),
            FOREIGN KEY (workspace_id, subject_id, memory_id, revision)
                REFERENCES memory_versions (workspace_id, subject_id, memory_id, revision)
                ON DELETE SET NULL (memory_id, revision),
            CHECK ((memory_id IS NULL) = (revision IS NULL))
        )
    """)
    op.create_index(
        "claims_by_memory", "claims", ["workspace_id", "subject_id", "memory_id"]
    )
    op.create_index(
        "memory_requests_by_memory",
        "memory_requests",
        ["workspace_id", "subject_id", "memory_id"],
    )


def downgrade() -> None:
    for table in (
        "memory_requests",
        "memory_versions",
        "relationships",
        "claims",
        "entities",
        "sources",
        "memories",
    ):
        op.drop_table(table)
