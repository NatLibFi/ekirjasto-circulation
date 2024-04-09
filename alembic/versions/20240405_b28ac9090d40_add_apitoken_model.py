"""Add ApiToken model

Revision ID: b28ac9090d40
Revises: be8ed331efcc
Create Date: 2024-04-05 12:18:48.616932+00:00

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "b28ac9090d40"
down_revision = "be8ed331efcc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "apitokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("token", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("collection_id", sa.Integer(), nullable=True),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["collection_id"], ["collections.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("label"),
        sa.UniqueConstraint("token"),
    )


def downgrade() -> None:
    op.drop_table("apitokens")
