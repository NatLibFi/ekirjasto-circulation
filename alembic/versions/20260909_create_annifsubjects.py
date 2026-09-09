"""Create Annif subject suggestions.

Revision ID: 20260909_annifsubjects
Revises: 7d3add9fd1fe
"""

import sqlalchemy as sa

from alembic import op

revision = "20260909_annifsubjects"
down_revision = "7d3add9fd1fe"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "annifsubjects",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("work_id", sa.Integer(), nullable=False),
        sa.Column("uri", sa.Unicode(), nullable=False),
        sa.Column("label", sa.Unicode(), nullable=False),
        sa.Column("score", sa.Numeric(precision=6, scale=5), nullable=True),
        sa.ForeignKeyConstraint(["work_id"], ["works.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("work_id", "uri"),
    )
    op.create_index("ix_annifsubjects_work_id", "annifsubjects", ["work_id"])


def downgrade() -> None:
    op.drop_index("ix_annifsubjects_work_id", table_name="annifsubjects")
    op.drop_table("annifsubjects")
