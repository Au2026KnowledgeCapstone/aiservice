"""Durable capability idempotency and honest extraction usage.

Revision ID: aext001
Revises: a1c015
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
revision = 'aext001'
down_revision = 'a1c015'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('capability_operations',
        sa.Column('request_id', sa.String(64), primary_key=True), sa.Column('kind', sa.String(20), nullable=False),
        sa.Column('fingerprint', sa.String(64), nullable=False), sa.Column('status', sa.String(20), nullable=False),
        sa.Column('result', sa.JSON().with_variant(JSONB(), 'postgresql')), sa.Column('error', sa.String(100)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_table('capability_usage', sa.Column('request_id', sa.String(64), primary_key=True),
        sa.Column('revision', sa.Integer(), nullable=False), sa.Column('status', sa.String(20), nullable=False),
        sa.Column('input_tokens', sa.Integer()), sa.Column('output_tokens', sa.Integer()), sa.Column('amount', sa.Numeric(24, 12)))


def downgrade():
    op.drop_table('capability_usage')
    op.drop_table('capability_operations')
