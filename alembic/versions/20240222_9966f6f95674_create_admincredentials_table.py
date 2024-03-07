"""Create admincredentials table

Revision ID: 9966f6f95674
Revises: 993729d4bf97
Create Date: 2024-02-22 02:36:07.130941+00:00

"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "9966f6f95674"
down_revision = "993729d4bf97"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admincredentials",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("external_id", sa.Unicode(), nullable=False),
        sa.Column("admin_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["admin_id"], ["admins.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_admincredentials_admin_id"),
        "admincredentials",
        ["admin_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_admincredentials_admin_id"), table_name="admincredentials")
    op.drop_table("admincredentials")
