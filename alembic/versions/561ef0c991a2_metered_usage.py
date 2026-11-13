"""Durable per-request metering and cost-session closure.

Revision ID: 561ef0c991a2
Revises: 36fb0d13a104
"""
from alembic import op
import sqlalchemy as sa
revision = "561ef0c991a2"
down_revision = "36fb0d13a104"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("costs_closed", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table("ai_usage",
        sa.Column("usage_id", sa.String(36), primary_key=True),
        sa.Column("job_id", sa.String(64), sa.ForeignKey("jobs.job_id", ondelete="CASCADE"), nullable=False),
        sa.Column("cost_session_id", sa.String(36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(100), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("operation", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("tokens", sa.JSON(), nullable=False),
        sa.Column("amount", sa.Numeric(24,12), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_ai_usage_job_id", "ai_usage", ["job_id"])


def downgrade():
    op.drop_table("ai_usage")
    with op.batch_alter_table("jobs") as batch:
        batch.drop_column("costs_closed")
