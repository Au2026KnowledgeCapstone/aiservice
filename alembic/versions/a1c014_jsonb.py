"""Use JSONB in the PostgreSQL operational store; retain SQLite test support.

Revision ID: a1c014
Revises: 561ef0c991a2
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "a1c014"
down_revision = "561ef0c991a2"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name == "postgresql":
        for table, column in (("jobs", "envelope"), ("outbox", "payload"), ("ai_usage", "tokens")):
            op.alter_column(table, column, type_=JSONB(), existing_type=sa.JSON(),
                            postgresql_using=f"{column}::jsonb")


def downgrade():
    if op.get_bind().dialect.name == "postgresql":
        for table, column in (("jobs", "envelope"), ("outbox", "payload"), ("ai_usage", "tokens")):
            op.alter_column(table, column, type_=sa.JSON(), existing_type=JSONB(),
                            postgresql_using=f"{column}::json")
