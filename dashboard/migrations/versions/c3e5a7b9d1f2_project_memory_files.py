"""Project memory: Claude manages the md files itself, or the owner picks the repo files (replaces the dashboard-held notes)

Revision ID: c3e5a7b9d1f2
Revises: b2d4f6a8c0e1
Create Date: 2026-11-05 15:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'c3e5a7b9d1f2'
down_revision = 'b2d4f6a8c0e1'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('projects') as b:
        b.add_column(sa.Column('memory_mode', sa.String(length=8), nullable=False, server_default='auto'))
        b.add_column(sa.Column('memory_files', sa.JSON(), nullable=False, server_default='[]'))
        b.drop_column('cc_notes')
        b.drop_column('cc_notes_at')
        b.drop_column('cc_notes_prev')
        b.drop_column('cc_notes_by')


def downgrade():
    with op.batch_alter_table('projects') as b:
        b.add_column(sa.Column('cc_notes', sa.Text(), nullable=False, server_default=''))
        b.add_column(sa.Column('cc_notes_at', sa.DateTime(), nullable=True))
        b.add_column(sa.Column('cc_notes_prev', sa.Text(), nullable=False, server_default=''))
        b.add_column(sa.Column('cc_notes_by', sa.String(length=20), nullable=False, server_default=''))
        b.drop_column('memory_files')
        b.drop_column('memory_mode')
