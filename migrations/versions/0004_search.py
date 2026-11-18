"""Add lexical search and durable indexing state."""

from alembic import op

revision = "0004_search"
down_revision = "0003_memories"


def upgrade() -> None:
    op.execute("""
        ALTER TABLE memories
            ADD COLUMN search_text tsvector GENERATED ALWAYS AS
                (to_tsvector('english', content)) STORED,
            ADD COLUMN embedding_model text,
            ADD COLUMN index_due_at timestamptz DEFAULT now(),
            ADD COLUMN index_attempts smallint NOT NULL DEFAULT 0,
            ADD COLUMN extraction_status text NOT NULL DEFAULT 'disabled'
                CHECK (extraction_status IN ('disabled', 'pending', 'complete', 'rejected'))
    """)
    op.execute("CREATE INDEX memories_search_text ON memories USING gin (search_text)")
    op.execute("""
        CREATE INDEX memories_due_indexing ON memories (index_due_at)
        WHERE index_due_at IS NOT NULL
    """)
    op.execute("ALTER TABLE claims ADD COLUMN extraction_model text")


def downgrade() -> None:
    op.drop_column("claims", "extraction_model")
    for column in (
        "extraction_status",
        "index_attempts",
        "index_due_at",
        "embedding_model",
        "search_text",
    ):
        op.drop_column("memories", column)
