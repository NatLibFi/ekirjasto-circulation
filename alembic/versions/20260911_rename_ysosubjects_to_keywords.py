"""Rename YSO subject suggestions to keywords.

Revision ID: 20260911_keywords
Revises: 20260909_ysosubjects
"""

import sqlalchemy as sa

from alembic import op

revision = "20260911_keywords"
down_revision = "20260909_ysosubjects"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.rename_table("ysosubjects", "keywords")
    op.execute(
        sa.text(
            "ALTER INDEX ix_ysosubjects_work_id " "RENAME TO ix_keywords_work_id"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE keywords "
            "RENAME CONSTRAINT ysosubjects_work_id_fkey "
            "TO keywords_work_id_fkey"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "ALTER TABLE keywords "
            "RENAME CONSTRAINT keywords_work_id_fkey "
            "TO ysosubjects_work_id_fkey"
        )
    )
    op.execute(
        sa.text(
            "ALTER INDEX ix_keywords_work_id " "RENAME TO ix_ysosubjects_work_id"
        )
    )
    op.rename_table("keywords", "ysosubjects")
