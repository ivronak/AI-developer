"""Debug mode per change + root-command approvals

Revision ID: f8a2c4e6b1d3
Revises: e7d1a3c5b9f2
Create Date: 2026-10-23 12:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f8a2c4e6b1d3'
down_revision = 'e7d1a3c5b9f2'
branch_labels = None
depends_on = None


def upgrade():
    # additive only: every existing change is a normal (non-debug) change
    with op.batch_alter_table('changes', schema=None) as batch_op:
        batch_op.add_column(sa.Column('agent_debug', sa.Boolean(), nullable=False, server_default='0'))
    with op.batch_alter_table('change_questions', schema=None) as batch_op:
        # root-command cards: the root service's one-use approval, kept only between the owner's answer and Claude's tool
        batch_op.add_column(sa.Column('ticket', sa.String(length=64), nullable=True))


def downgrade():
    with op.batch_alter_table('change_questions', schema=None) as batch_op:
        batch_op.drop_column('ticket')
    with op.batch_alter_table('changes', schema=None) as batch_op:
        batch_op.drop_column('agent_debug')
