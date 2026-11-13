"""Ordered callbacks, encrypted authentication, and delivery leases.

Revision ID: 36fb0d13a104
Revises: 2c5c976b0eca
"""
from alembic import op
import sqlalchemy as sa

revision = "36fb0d13a104"
down_revision = "2c5c976b0eca"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("callback_sequence", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("callback_secret", sa.Text(), nullable=True))
    with op.batch_alter_table("outbox") as batch:
        batch.add_column(sa.Column("sequence", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("lease_owner", sa.String(36), nullable=True))
        batch.create_unique_constraint("uq_outbox_job_sequence", ["job_id", "sequence"])
    # Old envelopes may contain a job token. Remove it rather than retaining plaintext;
    # legacy jobs need redispatch under a new ID to obtain callback authentication.
    jobs = sa.table("jobs", sa.column("job_id", sa.String), sa.column("envelope", sa.JSON))
    connection = op.get_bind()
    for row in connection.execute(sa.select(jobs)).mappings():
        envelope = dict(row["envelope"])
        envelope["callback_token"] = ""
        connection.execute(jobs.update().where(jobs.c.job_id == row["job_id"]).values(envelope=envelope))


def downgrade():
    with op.batch_alter_table("outbox") as batch:
        batch.drop_constraint("uq_outbox_job_sequence", type_="unique")
        for name in ("lease_owner", "lease_until", "failed_at", "sequence"):
            batch.drop_column(name)
    with op.batch_alter_table("jobs") as batch:
        batch.drop_column("callback_secret")
        batch.drop_column("callback_sequence")
