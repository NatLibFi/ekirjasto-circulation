"""Cascade work deletion to Annif subject suggestions."""

from alembic import op

revision = "20260909_annifsubj_cascade"
down_revision = "20260909_annifsubjects"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "annifsubjects_work_id_fkey", "annifsubjects", type_="foreignkey"
    )
    op.create_foreign_key(
        "annifsubjects_work_id_fkey",
        "annifsubjects",
        "works",
        ["work_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "annifsubjects_work_id_fkey", "annifsubjects", type_="foreignkey"
    )
    op.create_foreign_key(
        "annifsubjects_work_id_fkey",
        "annifsubjects",
        "works",
        ["work_id"],
        ["id"],
    )
