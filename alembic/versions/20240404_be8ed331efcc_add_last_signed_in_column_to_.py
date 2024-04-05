"""Add last_signed_in column to admincredentials

Revision ID: be8ed331efcc
Revises: 4915a361b082
Create Date: 2024-04-04 11:34:33.755399+00:00

"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "be8ed331efcc"
down_revision = "4915a361b082"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "admincredentials",
        sa.Column(
            "last_signed_in",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("admincredentials", "last_signed_in")
