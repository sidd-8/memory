"""Add workspace settings and scoped retrieval activity."""

from alembic import op

revision = "0007_console"
down_revision = "0006_temporal_graph"

MEMORY_SCOPES = """
    'subject:read', 'memory:read', 'memory:write', 'memory:verify', 'memory:delete', 'graph:read'
"""


def _replace_scope_constraint(extra: str = "") -> None:
    op.execute("ALTER TABLE api_keys DROP CONSTRAINT api_keys_scopes_check")
    op.execute(f"""
        ALTER TABLE api_keys ADD CONSTRAINT api_keys_scopes_check CHECK (
            cardinality(scopes) > 0 AND array_position(scopes, NULL) IS NULL
            AND scopes <@ ARRAY[{MEMORY_SCOPES}{extra}]
        )
    """)


def upgrade() -> None:
    _replace_scope_constraint(", 'workspace:manage'")
    op.execute("""
        ALTER TABLE workspaces
            ADD COLUMN default_retention_days integer CHECK (default_retention_days BETWEEN 1 AND 3650),
            ADD COLUMN default_review_days integer CHECK (default_review_days BETWEEN 1 AND 3650)
    """)
    op.execute("""
        CREATE TABLE retrieval_events (
            workspace_id uuid NOT NULL,
            subject_id uuid NOT NULL,
            agent_id uuid NOT NULL,
            memory_id uuid,
            result_count integer NOT NULL,
            recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            FOREIGN KEY (workspace_id, subject_id) REFERENCES subjects (workspace_id, id),
            FOREIGN KEY (workspace_id, agent_id) REFERENCES agents (workspace_id, id),
            FOREIGN KEY (workspace_id, subject_id, memory_id)
                REFERENCES memories (workspace_id, subject_id, id) ON DELETE SET NULL (memory_id)
        )
    """)
    op.create_index(
        "retrieval_activity",
        "retrieval_events",
        ["workspace_id", "subject_id", "recorded_at"],
    )
    op.create_index(
        "retrieval_memory",
        "retrieval_events",
        ["workspace_id", "subject_id", "memory_id"],
    )


def downgrade() -> None:
    op.drop_table("retrieval_events")
    op.drop_column("workspaces", "default_review_days")
    op.drop_column("workspaces", "default_retention_days")
    op.execute("DELETE FROM api_keys WHERE scopes = ARRAY['workspace:manage']")
    op.execute("UPDATE api_keys SET scopes = array_remove(scopes, 'workspace:manage')")
    _replace_scope_constraint()
