"""Index memory intervals and both directions of graph traversal."""

from alembic import op

revision = "0006_temporal_graph"
down_revision = "0005_claim_lifecycle"


def upgrade() -> None:
    op.execute("""
        ALTER TABLE memories
            ADD COLUMN starts_at timestamptz,
            ADD COLUMN ends_at timestamptz,
            ADD COLUMN timezone text,
            ADD COLUMN state text CHECK (
                state IN ('tentative', 'confirmed', 'cancelled', 'superseded', 'completed')
            ),
            ADD COLUMN time_span tstzrange GENERATED ALWAYS AS (
                CASE WHEN starts_at IS NOT NULL THEN tstzrange(starts_at, ends_at, '[)') END
            ) STORED,
            ADD CONSTRAINT memories_time_fields CHECK (
                (starts_at IS NULL AND ends_at IS NULL AND timezone IS NULL AND state IS NULL)
                OR (starts_at IS NOT NULL AND ends_at IS NOT NULL AND starts_at < ends_at
                    AND timezone IS NOT NULL)
            )
    """)
    op.execute("CREATE INDEX memories_time_span ON memories USING gist (time_span)")
    op.create_index(
        "relationships_from_entity",
        "relationships",
        ["workspace_id", "subject_id", "from_entity_id", "claim_id"],
    )
    op.create_index(
        "relationships_to_entity",
        "relationships",
        ["workspace_id", "subject_id", "to_entity_id", "claim_id"],
    )


def downgrade() -> None:
    op.drop_index("relationships_to_entity")
    op.drop_index("relationships_from_entity")
    op.drop_index("memories_time_span")
    op.drop_constraint("memories_time_fields", "memories")
    for column in ("time_span", "state", "timezone", "ends_at", "starts_at"):
        op.drop_column("memories", column)
