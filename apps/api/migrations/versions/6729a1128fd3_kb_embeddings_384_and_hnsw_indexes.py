"""kb embeddings 384 and hnsw indexes

Revision ID: 6729a1128fd3
Revises: 40f28b71c5e9
Create Date: 2026-11-16 10:36:47.237330
"""

from collections.abc import Sequence

import pgvector.sqlalchemy
from alembic import op

revision: str = "6729a1128fd3"
down_revision: str | None = "40f28b71c5e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Embeddings from a different model are meaningless: clear them and force re-ingestion.
    op.execute("DELETE FROM kb_public_chunks")
    op.execute("DELETE FROM kb_internal_chunks")
    op.execute("UPDATE documents SET content_hash = NULL")
    op.alter_column(
        "kb_internal_chunks",
        "embedding",
        existing_type=pgvector.sqlalchemy.vector.VECTOR(dim=768),
        type_=pgvector.sqlalchemy.vector.VECTOR(dim=384),
        existing_nullable=False,
    )
    op.create_index(
        "ix_kb_internal_chunks_embedding",
        "kb_internal_chunks",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.alter_column(
        "kb_public_chunks",
        "embedding",
        existing_type=pgvector.sqlalchemy.vector.VECTOR(dim=768),
        type_=pgvector.sqlalchemy.vector.VECTOR(dim=384),
        existing_nullable=False,
    )
    op.create_index(
        "ix_kb_public_chunks_embedding",
        "kb_public_chunks",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    # Embeddings from a different model are meaningless: clear them and force re-ingestion.
    op.execute("DELETE FROM kb_public_chunks")
    op.execute("DELETE FROM kb_internal_chunks")
    op.execute("UPDATE documents SET content_hash = NULL")
    op.drop_index(
        "ix_kb_public_chunks_embedding",
        table_name="kb_public_chunks",
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.alter_column(
        "kb_public_chunks",
        "embedding",
        existing_type=pgvector.sqlalchemy.vector.VECTOR(dim=384),
        type_=pgvector.sqlalchemy.vector.VECTOR(dim=768),
        existing_nullable=False,
    )
    op.drop_index(
        "ix_kb_internal_chunks_embedding",
        table_name="kb_internal_chunks",
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.alter_column(
        "kb_internal_chunks",
        "embedding",
        existing_type=pgvector.sqlalchemy.vector.VECTOR(dim=384),
        type_=pgvector.sqlalchemy.vector.VECTOR(dim=768),
        existing_nullable=False,
    )
    # ### end Alembic commands ###
