"""Add duration to circulationevents

Revision ID: 4915a361b082
Revises: 9966f6f95674
Create Date: 2024-03-21 14:06:48.744935+00:00

"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "4915a361b082"
down_revision = "9966f6f95674"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "circulationevents", sa.Column("duration", sa.Integer(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("circulationevents", "duration")
