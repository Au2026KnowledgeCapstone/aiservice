"""Rename the capability tables to operations.

The name "capability" recorded a change-management boundary — the work added
after the A1-only audit — not a property of the three synchronous endpoints it
covered. `operations` is what the model classes already meant, so the rename
drops a stutter (`capability_operations` -> `operations`) rather than inventing
a term. Nothing on the wire changes: no route, field or schema class is affected.

Revision ID: aext002
Revises: aext001
"""
from alembic import op

revision = 'aext002'
down_revision = 'aext001'
branch_labels = None
depends_on = None


def upgrade():
    op.rename_table('capability_operations', 'operations')
    op.rename_table('capability_usage', 'operation_usage')


def downgrade():
    op.rename_table('operation_usage', 'capability_usage')
    op.rename_table('operations', 'capability_operations')
