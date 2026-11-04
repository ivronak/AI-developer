"""per-change Claude Code model and effort

Revision ID: c3a91d5e7f20
Revises: 7fe1e74b2ac6
Create Date: 2026-10-23 07:10:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c3a91d5e7f20'
down_revision = '7fe1e74b2ac6'
branch_labels = None
depends_on = None


def upgrade():
    # additive only: both columns are optional, so every existing change keeps working with its Quick / Normal / Best preset
    with op.batch_alter_table('changes', schema=None) as batch_op:
        batch_op.add_column(sa.Column('agent_model', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('agent_effort', sa.String(length=8), nullable=True))


def downgrade():
    with op.batch_alter_table('changes', schema=None) as batch_op:
        batch_op.drop_column('agent_effort')
        batch_op.drop_column('agent_model')
