"""Add verification, freshness, and effective claim reads."""

from alembic import op

revision = "0005_claim_lifecycle"
down_revision = "0004_search"


def upgrade() -> None:
    op.execute("""
        ALTER TABLE claims
            ADD COLUMN verified_at timestamptz,
            ADD COLUMN verified_by uuid,
            ADD COLUMN review_due_at timestamptz,
            ADD COLUMN reviewed_at timestamptz,
            ADD COLUMN reviewed_by uuid,
            ADD FOREIGN KEY (workspace_id, verified_by) REFERENCES agents (workspace_id, id),
            ADD FOREIGN KEY (workspace_id, reviewed_by) REFERENCES agents (workspace_id, id)
    """)
    op.execute("""
        CREATE INDEX claims_due_review ON claims (review_due_at)
        WHERE status = 'active' AND review_due_at IS NOT NULL
    """)
    op.execute("""
        CREATE INDEX claims_verified_predicate
        ON claims (workspace_id, subject_id, predicate, subject_entity_id)
        WHERE verified_at IS NOT NULL AND superseded_at IS NULL AND status <> 'archived'
    """)
    op.execute("""
        CREATE VIEW claim_evidence AS
        SELECT c.*, jsonb_build_object(
            'id', c.id, 'predicate', c.predicate, 'object_value', c.object_value,
            'subject_entity', CASE WHEN subject.id IS NOT NULL THEN
                jsonb_build_object('id', subject.id, 'kind', subject.kind, 'reference', subject.reference) END,
            'object_entity', CASE WHEN object.id IS NOT NULL THEN
                jsonb_build_object('id', object.id, 'kind', object.kind, 'reference', object.reference) END,
            'source', jsonb_build_object(
                'id', s.id, 'origin', s.origin, 'source_ref', s.source_ref,
                'observed_at', s.observed_at, 'trust_class', s.trust_class
            ),
            'valid_from', c.valid_from, 'valid_to', c.valid_to, 'confidence', c.confidence,
            'status', CASE WHEN c.status = 'active' AND c.review_due_at <= statement_timestamp()
                THEN 'stale' ELSE c.status END,
            'created_at', c.created_at, 'superseded_at', c.superseded_at,
            'verified_at', c.verified_at, 'verified_by', c.verified_by,
            'review_due_at', c.review_due_at, 'reviewed_at', c.reviewed_at,
            'reviewed_by', c.reviewed_by, 'extraction_model', c.extraction_model
        ) AS evidence
        FROM claims c
        JOIN sources s ON (s.workspace_id, s.subject_id, s.memory_id, s.id)
            = (c.workspace_id, c.subject_id, c.memory_id, c.source_id)
        LEFT JOIN entities subject ON (subject.workspace_id, subject.subject_id, subject.id)
            = (c.workspace_id, c.subject_id, c.subject_entity_id)
        LEFT JOIN entities object ON (object.workspace_id, object.subject_id, object.id)
            = (c.workspace_id, c.subject_id, c.object_entity_id)
    """)
    op.execute("""
        CREATE VIEW current_memory_snapshots AS
        SELECT v.workspace_id, v.subject_id, v.memory_id, v.revision,
            jsonb_set(v.snapshot, '{claims}', COALESCE((
                SELECT jsonb_agg(c.evidence ORDER BY item.position)
                FROM jsonb_array_elements(v.snapshot->'claims') WITH ORDINALITY AS item(claim, position)
                JOIN claim_evidence c
                    ON (c.workspace_id, c.subject_id, c.memory_id, c.id)
                     = (v.workspace_id, v.subject_id, v.memory_id, (item.claim->>'id')::uuid)
                WHERE c.superseded_at IS NULL AND c.status <> 'archived'
            ), '[]'::jsonb)) AS snapshot
        FROM memory_versions v
        JOIN memories m ON (m.workspace_id, m.subject_id, m.id, m.revision)
                         = (v.workspace_id, v.subject_id, v.memory_id, v.revision)
    """)


def downgrade() -> None:
    op.execute("DROP VIEW current_memory_snapshots")
    op.execute("DROP VIEW claim_evidence")
    op.drop_index("claims_verified_predicate")
    op.drop_index("claims_due_review")
    for column in (
        "reviewed_by",
        "reviewed_at",
        "review_due_at",
        "verified_by",
        "verified_at",
    ):
        op.drop_column("claims", column)
