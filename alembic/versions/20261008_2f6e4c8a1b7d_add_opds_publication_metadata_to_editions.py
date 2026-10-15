"""Add OPDS publication metadata to editions.

Revision ID: 2f6e4c8a1b7d
Revises: 7d3add9fd1fe
Create Date: 2026-10-08

"""
import sqlalchemy as sa

from alembic import op

revision = "2f6e4c8a1b7d"
down_revision = "7d3add9fd1fe"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("editions", sa.Column("abridged", sa.Boolean(), nullable=True))
    op.add_column("editions", sa.Column("number_of_pages", sa.Integer(), nullable=True))
    op.add_column("editions", sa.Column("layout", sa.Unicode(), nullable=True))
    op.add_column("editions", sa.Column("collection", sa.Unicode(), nullable=True))


def downgrade() -> None:
    op.drop_column("editions", "collection")
    op.drop_column("editions", "layout")
    op.drop_column("editions", "number_of_pages")
    op.drop_column("editions", "abridged")
