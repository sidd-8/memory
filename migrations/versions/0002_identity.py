"""Add workspace identity and subject-scoped API keys."""

from alembic import op

revision = "0002_identity"
down_revision = "0001_pgvector"


def upgrade() -> None:
    op.execute("""
        CREATE TABLE workspaces (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            name text NOT NULL CHECK (btrim(name) <> '')
        )
    """)
    op.execute("""
        CREATE TABLE users (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            identifier text UNIQUE NOT NULL CHECK (btrim(identifier) <> '')
        )
    """)
    op.execute("""
        CREATE TABLE workspace_members (
            workspace_id uuid NOT NULL REFERENCES workspaces (id),
            user_id uuid NOT NULL REFERENCES users (id),
            role text NOT NULL CHECK (role IN ('owner', 'member')),
            PRIMARY KEY (workspace_id, user_id)
        )
    """)
    op.execute("""
        CREATE TABLE agents (
            workspace_id uuid NOT NULL,
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            registered_by uuid NOT NULL,
            name text NOT NULL CHECK (btrim(name) <> ''),
            PRIMARY KEY (workspace_id, id),
            FOREIGN KEY (workspace_id, registered_by)
                REFERENCES workspace_members (workspace_id, user_id)
        )
    """)
    op.execute("""
        CREATE TABLE subjects (
            workspace_id uuid NOT NULL REFERENCES workspaces (id),
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            kind text NOT NULL CHECK (kind IN ('user', 'project', 'team')),
            reference text NOT NULL CHECK (btrim(reference) <> ''),
            PRIMARY KEY (workspace_id, id),
            UNIQUE (workspace_id, kind, reference)
        )
    """)
    op.execute("""
        CREATE TABLE api_keys (
            workspace_id uuid NOT NULL,
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            agent_id uuid NOT NULL,
            subject_id uuid NOT NULL,
            token_digest bytea UNIQUE NOT NULL CHECK (octet_length(token_digest) = 32),
            scopes text[] NOT NULL CHECK (
                cardinality(scopes) > 0
                AND array_position(scopes, NULL) IS NULL
                AND scopes <@ ARRAY[
                    'subject:read', 'memory:read', 'memory:write',
                    'memory:verify', 'memory:delete', 'graph:read'
                ]
            ),
            revoked_at timestamptz,
            PRIMARY KEY (workspace_id, id),
            FOREIGN KEY (workspace_id, agent_id) REFERENCES agents (workspace_id, id),
            FOREIGN KEY (workspace_id, subject_id)
                REFERENCES subjects (workspace_id, id)
        )
    """)


def downgrade() -> None:
    for table in (
        "api_keys",
        "agents",
        "subjects",
        "workspace_members",
        "users",
        "workspaces",
    ):
        op.drop_table(table)
