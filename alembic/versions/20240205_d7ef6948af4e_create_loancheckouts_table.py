"""Create loancheckouts table

Revision ID: d7ef6948af4e
Revises: cc084e35e037
Create Date: 2024-02-05 08:45:20.164531+00:00

"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "d7ef6948af4e"
down_revision = "cc084e35e037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "loancheckouts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("patron_id", sa.Integer(), nullable=True),
        sa.Column("license_pool_id", sa.Integer(), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["license_pool_id"],
            ["licensepools.id"],
        ),
        sa.ForeignKeyConstraint(["patron_id"], ["patrons.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_loancheckouts_license_pool_id"),
        "loancheckouts",
        ["license_pool_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_loancheckouts_patron_id"), "loancheckouts", ["patron_id"], unique=False
    )
    op.create_index(
        op.f("ix_loancheckouts_timestamp"), "loancheckouts", ["timestamp"], unique=False
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    op.drop_index(op.f("ix_loancheckouts_timestamp"), table_name="loancheckouts")
    op.drop_index(op.f("ix_loancheckouts_patron_id"), table_name="loancheckouts")
    op.drop_index(op.f("ix_loancheckouts_license_pool_id"), table_name="loancheckouts")
    op.drop_table("loancheckouts")
    # ### end Alembic commands ###
