"""Live folder vs GitHub: what the dashboard saw (uncommitted files, unpushed commits, newer main)

Revision ID: b2d4f6a8c0e1
Revises: a1c3e5f7b9d2
Create Date: 2026-11-05 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'b2d4f6a8c0e1'
down_revision = 'a1c3e5f7b9d2'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('environments') as b:
        b.add_column(sa.Column('drift', sa.JSON(), nullable=True))
        b.add_column(sa.Column('drift_at', sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table('environments') as b:
        b.drop_column('drift_at')
        b.drop_column('drift')
