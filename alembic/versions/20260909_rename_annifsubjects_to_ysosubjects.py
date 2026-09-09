"""Rename Annif subject suggestions to YSO subject suggestions.

Revision ID: 20260909_ysosubjects
Revises: 20260909_annifsubj_cascade
"""

import sqlalchemy as sa

from alembic import op

revision = "20260909_ysosubjects"
down_revision = "20260909_annifsubj_cascade"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.rename_table("annifsubjects", "ysosubjects")
    op.execute(
        sa.text(
            "ALTER INDEX ix_annifsubjects_work_id " "RENAME TO ix_ysosubjects_work_id"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE ysosubjects "
            "RENAME CONSTRAINT annifsubjects_work_id_fkey "
            "TO ysosubjects_work_id_fkey"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "ALTER TABLE ysosubjects "
            "RENAME CONSTRAINT ysosubjects_work_id_fkey "
            "TO annifsubjects_work_id_fkey"
        )
    )
    op.execute(
        sa.text(
            "ALTER INDEX ix_ysosubjects_work_id " "RENAME TO ix_annifsubjects_work_id"
        )
    )
    op.rename_table("ysosubjects", "annifsubjects")
