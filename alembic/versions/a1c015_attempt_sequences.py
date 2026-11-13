"""Scope callback sequence allocation and uniqueness to an execution attempt.

Revision ID: a1c015
Revises: a1c014
"""
from alembic import op
import sqlalchemy as sa

revision = "a1c015"
down_revision = "a1c014"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("callback_attempt", sa.Integer(), nullable=False, server_default="1"))
    with op.batch_alter_table("outbox") as batch:
        batch.add_column(sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"))
        batch.drop_constraint("uq_outbox_job_sequence", type_="unique")
        batch.create_unique_constraint("uq_outbox_job_attempt_sequence", ["job_id", "attempt", "sequence"])


def downgrade():
    if op.get_bind().execute(sa.text("SELECT count(*) FROM outbox WHERE attempt <> 1")).scalar():
        raise RuntimeError("Cannot flatten multiple execution attempts into a single sequence; archive later attempts first")
    with op.batch_alter_table("outbox") as batch:
        batch.drop_constraint("uq_outbox_job_attempt_sequence", type_="unique")
        batch.drop_column("attempt")
        batch.create_unique_constraint("uq_outbox_job_sequence", ["job_id", "sequence"])
    with op.batch_alter_table("jobs") as batch:
        batch.drop_column("callback_attempt")
