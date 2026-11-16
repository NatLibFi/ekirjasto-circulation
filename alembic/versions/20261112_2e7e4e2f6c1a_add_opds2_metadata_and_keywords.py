"""add OPDS2 original metadata and work keywords

Revision ID: 2e7e4e2f6c1a
Revises: e9a23a3f988d
Create Date: 2026-11-12
"""

import sqlalchemy as sa

from alembic import op


revision = "2e7e4e2f6c1a"
down_revision = "e9a23a3f988d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("editions", sa.Column("original_language", sa.Unicode(), nullable=True))
    op.add_column("editions", sa.Column("original_published", sa.Date(), nullable=True))
    op.add_column("editions", sa.Column("original_title", sa.Unicode(), nullable=True))
    op.add_column("editions", sa.Column("file_size", sa.Float(), nullable=True))
    op.add_column(
        "editions", sa.Column("keywords", sa.ARRAY(sa.Unicode()), nullable=True)
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_keywords_work_id"), table_name="keywords")
    op.drop_table("keywords")
    op.drop_column("editions", "file_size")
    op.drop_column("editions", "original_title")
    op.drop_column("editions", "original_published")
    op.drop_column("editions", "original_language")
    op.drop_column("editions", "keywords")
