"""Create SelectedBooks

Revision ID: d3aaeb6a9e6b
Revises: b28ac9090d40
Create Date: 2024-12-10 14:16:32.223456+00:00

"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "d3aaeb6a9e6b"
down_revision = "b28ac9090d40"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table(
        "selected_books",
        sa.Column("id", sa.Integer()),
        sa.Column("patron_id", sa.Integer()),
        sa.Column("work_id", sa.Integer()),
        sa.Column("creation_date", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["patron_id"],
            ["patrons.id"],
        ),
        sa.ForeignKeyConstraint(
            ["work_id"],
            ["works.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("patron_id", "work_id"),
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table("selected_books")
    # ### end Alembic commands ###
