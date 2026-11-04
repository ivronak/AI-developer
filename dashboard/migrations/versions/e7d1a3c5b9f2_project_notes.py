"""project notes: what Claude learned about a project (its CLAUDE.md)

Revision ID: e7d1a3c5b9f2
Revises: d5b7e9a1c3f4
Create Date: 2026-10-23 11:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e7d1a3c5b9f2'
down_revision = 'd5b7e9a1c3f4'
branch_labels = None
depends_on = None


def upgrade():
    # additive only: every existing project starts with empty notes
    with op.batch_alter_table('projects', schema=None) as batch_op:
        batch_op.add_column(sa.Column('cc_notes', sa.Text(), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('cc_notes_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('cc_notes_prev', sa.Text(), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('cc_notes_by', sa.String(length=20), nullable=False, server_default=''))


def downgrade():
    with op.batch_alter_table('projects', schema=None) as batch_op:
        batch_op.drop_column('cc_notes_by')
        batch_op.drop_column('cc_notes_prev')
        batch_op.drop_column('cc_notes_at')
        batch_op.drop_column('cc_notes')
